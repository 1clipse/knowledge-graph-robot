from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import neo4j_client, schema_manager
from extractors.llm_extractor import LLMExtractor, ExtractionResult, ExtractedEntity, ExtractedRelation, EntityRef
from extractors.rule_extractor import RuleExtractor
from extractors.structured_mapper import StructuredMapper
from loaders.pdf_loader import PDFLoader
from loaders.csv_loader import CSVLoader
from loaders.web_loader import WebLoader
from loaders.step_loader import STEPLoader
from loaders.dxf_loader import DXFLoader, DXFMetadata

router = APIRouter()

ODA_CONVERTER = os.environ.get("ODA_CONVERTER_PATH", "E:/ODA/ODAFileConverter.exe")


def _convert_dwg_to_dxf(dwg_path: str) -> str:
    """使用 ODA File Converter 将 DWG 转为 DXF，返回 DXF 文件路径"""
    import shutil

    # ODA CLI 要求输入是文件夹，把 DWG 复制到临时输入目录
    input_dir = tempfile.mkdtemp(prefix="dwg_input_")
    output_dir = tempfile.mkdtemp(prefix="dwg_output_")
    dwg_name = Path(dwg_path).stem
    src_name = os.path.basename(dwg_path)
    shutil.copy2(dwg_path, os.path.join(input_dir, src_name))

    cmd = [
        ODA_CONVERTER,
        input_dir,
        output_dir,
        "ACAD2018",
        "DXF",
        "0",
        "0",
    ]
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0x08000000
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, creationflags=creationflags)
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip()
            logger.error(f"ODA conversion failed (rc={result.returncode}): {stderr}")
            raise RuntimeError(f"DWG→DXF conversion failed: {stderr}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("DWG→DXF conversion timed out (60s)")
    except FileNotFoundError:
        raise RuntimeError(
            f"ODA File Converter not found at {ODA_CONVERTER}. "
            "Set ODA_CONVERTER_PATH env var or install ODA File Converter."
        )
    finally:
        shutil.rmtree(input_dir, ignore_errors=True)

    dxf_files = list(Path(output_dir).glob("*.dxf"))
    if not dxf_files:
        raise RuntimeError(f"No DXF output found in {output_dir}")
    logger.info(f"DWG→DXF converted: {dwg_name} → {dxf_files[0].name}")
    return str(dxf_files[0])


def _extract_dwg_strings(file_path: str, filename: str) -> str:
    """从 DWG 二进制文件中提取可读字符串作为元数据"""
    import re
    parts: list = [f"DWG文件名: {filename}"]

    try:
        with open(file_path, "rb") as f:
            raw = f.read(2 * 1024 * 1024)  # read up to 2MB
    except Exception:
        return f"DWG 文件: {filename}（无法读取）"

    # DWG version detection from header bytes
    version_map = {
        b"AC1015": "AutoCAD 2000/2000i/2002",
        b"AC1018": "AutoCAD 2004/2005/2006",
        b"AC1021": "AutoCAD 2007/2008/2009",
        b"AC1024": "AutoCAD 2010/2011/2012",
        b"AC1027": "AutoCAD 2013/2014/2015/2016/2017",
        b"AC1032": "AutoCAD 2018/2019/2020/2021/2022/2023/2024",
    }
    for sig, ver_name in version_map.items():
        if sig in raw[:4096]:
            parts.append(f"DWG 版本: {ver_name}")
            break

    # Extract readable ASCII strings (minimum 4 chars)
    ascii_strings = re.findall(rb"[\x20-\x7E]{4,80}", raw)
    seen: set[str] = set()
    readable: list[str] = []

    for s in ascii_strings:
        try:
            text = s.decode("ascii", errors="ignore").strip()
            if len(text) >= 4 and text not in seen:
                alpha_ratio = sum(1 for c in text if c.isalpha())
                if alpha_ratio >= len(text) * 0.3:
                    seen.add(text)
                    readable.append(text)
        except Exception:
            logger.debug(f"Skipping non-decodable ASCII fragment")
            pass

    # Also try UTF-8 decode for CJK content
    try:
        utf8_text = raw.decode("utf-8", errors="ignore")
        cjk_strings = re.findall(r"[一-鿿　-〿＀-￯㐀-䶿]{2,30}", utf8_text)
        for cs in cjk_strings:
            if cs not in seen:
                seen.add(cs)
                readable.append(cs)
    except Exception:
        logger.debug(f"UTF-8 decode failed for DWG string extraction")

    if readable:
        parts.append(f"文件中提取到的文字/标注 ({len(readable)} 条):")
        for t in readable[:60]:
            parts.append(f"  - {t}")

    return "\n".join(parts)


class IngestTextRequest(BaseModel):
    text: str = Field(..., description="要抽取的文本内容")
    use_llm: bool = Field(default=True, description="是否使用LLM抽取")
    use_rule_fallback: bool = Field(default=True, description="LLM失败时是否使用规则回退")


def _dxf_meta_to_entities(meta: DXFMetadata, filename: str = "") -> ExtractionResult:
    """将 DXF 结构化元数据直接映射为知识图谱实体，不依赖 LLM"""
    entities: List[ExtractedEntity] = []
    relations: List[ExtractedRelation] = []

    drawing_name = filename or "CAD_Drawing"

    # Block names → Component entities
    component_names: set[str] = set()
    for name in meta.block_names:
        name = name.strip()
        if name and len(name) >= 1:
            entities.append(ExtractedEntity(name=name, type="Component",
                                            properties={"source": "DXF_BLOCK", "file": drawing_name}))
            component_names.add(name)

    # Layer names → referenced as properties on a "Drawing" entity
    layer_names: list[str] = []
    for name in meta.layers:
        name = name.strip()
        if name:
            layer_names.append(name)

    # Create a Drawing entity to hold the file metadata
    drawing_props: Dict[str, Any] = {
        "dxf_version": meta.file_version,
        "entity_count": meta.entity_count,
        "line_count": meta.line_count,
        "circle_count": meta.circle_count,
        "arc_count": meta.arc_count,
        "polyline_count": meta.polyline_count,
        "file": drawing_name,
    }
    if layer_names:
        drawing_props["layers"] = ", ".join(layer_names[:10])
    if meta.extents:
        drawing_props["width"] = meta.extents.get("width")
        drawing_props["height"] = meta.extents.get("height")

    entities.append(ExtractedEntity(name=drawing_name, type="Component", properties=drawing_props))

    # Block insertions → relations: Drawing contains Component
    inserted_blocks: Dict[str, int] = {}
    for ins in meta.inserts:
        n = ins.get("name", "").strip()
        if n:
            inserted_blocks[n] = inserted_blocks.get(n, 0) + 1

    for blk_name, count in inserted_blocks.items():
        if blk_name in component_names or blk_name:
            relations.append(ExtractedRelation(
                source=EntityRef(name=drawing_name, type="Component"),
                target=EntityRef(name=blk_name, type="Component"),
                relation_type="contains",
                properties={"count": count},
            ))

    # Text annotations → try to extract robot/manufacturer names via keyword matching
    robot_keywords = {"FANUC", "ABB", "KUKA", "安川", "Yaskawa", "川崎", "Kawasaki",
                      "爱普生", "Epson", "史陶比尔", "Stäubli", "柯马", "Comau",
                      "那智", "Nachi", "优傲", "Universal", "埃斯顿", "Estun", "汇川", "新松"}
    all_texts = meta.texts + meta.mtexts
    for txt in all_texts:
        for kw in robot_keywords:
            if kw.lower() in txt.lower():
                entities.append(ExtractedEntity(name=kw, type="Manufacturer", properties={"source": "DXF_text"}))
                break

    # Attributes (TAG=VALUE pairs) → properties or entities
    for attr in meta.attributes:
        tag = attr.get("tag", "").strip()
        text_val = attr.get("text", "").strip()
        if tag and text_val and len(tag) >= 2:
            entities.append(ExtractedEntity(
                name=tag,
                type="Component",
                properties={"value": text_val, "source": "DXF_ATTR"}
            ))

    # Dimensions → potential specification entities
    for dim in meta.dimensions[:20]:
        dim = dim.strip()
        if dim:
            entities.append(ExtractedEntity(
                name=f"Dimension_{dim[:30]}",
                type="Component",
                properties={"dimension_value": dim, "source": "DXF_DIM"}
            ))

    return ExtractionResult(entities=entities, relations=relations)


class IngestURLRequest(BaseModel):
    url: str = Field(..., description="要抓取的网页URL")
    selector: Optional[str] = Field(default=None, description="CSS选择器")
    use_llm: bool = Field(default=True)


class IngestResponse(BaseModel):
    status: str
    entities_count: int
    relations_count: int
    entities: List[Dict[str, Any]] = Field(default_factory=list)
    relations: List[Dict[str, Any]] = Field(default_factory=list)
    message: str = ""


def _stamp_source(result: ExtractionResult, source: str) -> ExtractionResult:
    """Set source provenance on all entities and relations."""
    for e in result.entities:
        e.source = source
    for r in result.relations:
        r.source_ref = source
    return result


def _write_to_graph(result: ExtractionResult) -> None:
    """Write extraction result to graph with batch embedding and batch write."""
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")

    if not result.entities:
        return

    # Phase 1: Generate embeddings in batch
    embed_texts: list[str] = []
    valid_entities: list = []
    for entity in result.entities:
        if schema_manager and not schema_manager.validate_entity_type(entity.type):
            logger.warning(f"Skipping invalid entity type: {entity.type}")
            continue
        embed_texts.append(
            f"{entity.type} {entity.name} {entity.properties.get('description', '')}"
        )
        valid_entities.append(entity)

    embeddings_map: dict[int, list] = {}
    if embed_texts:
        try:
            from graph.embeddings import embed_texts as batch_embed
            emb_list = batch_embed(embed_texts)
            for i, emb in enumerate(emb_list):
                embeddings_map[i] = emb
        except Exception as e:
            logger.warning(f"Batch embedding failed, skipping embeddings: {e}")

    # Phase 2: Build entity write queries
    entity_queries: list[tuple[str, dict]] = []
    for i, entity in enumerate(valid_entities):
        props = {k: v for k, v in entity.properties.items() if v is not None}
        props["name"] = entity.name
        if entity.source:
            props["_source"] = entity.source
        props["_confidence"] = entity.confidence
        if entity.valid_from:
            props["valid_from"] = entity.valid_from
        if entity.valid_to:
            props["valid_to"] = entity.valid_to
        if i in embeddings_map:
            props["_embedding"] = embeddings_map[i]

        from graph.client import _validate_identifier
        _validate_identifier(entity.type, "entity_type")
        prop_assignments = ", ".join(f"n.{k} = ${k}" for k in props.keys())
        query = f"MERGE (n:`{entity.type}` {{name: $name}}) SET {prop_assignments}"
        entity_queries.append((query, props))

    # Phase 3: Build relation write queries
    relation_queries: list[tuple[str, dict]] = []
    for rel in result.relations:
        if schema_manager and not schema_manager.validate_relation_type(rel.relation_type):
            logger.warning(f"Skipping invalid relation type: {rel.relation_type}")
            continue
        rel_props = {k: v for k, v in rel.properties.items() if v is not None}
        if rel.source_ref:
            rel_props["_source"] = rel.source_ref
        rel_props["_confidence"] = rel.confidence
        if rel.valid_from:
            rel_props["valid_from"] = rel.valid_from
        if rel.valid_to:
            rel_props["valid_to"] = rel.valid_to

        from graph.client import _validate_identifier
        _validate_identifier(rel.source.type, "source_type")
        _validate_identifier(rel.target.type, "target_type")
        _validate_identifier(rel.relation_type, "relation_type")
        params: dict = {
            "source_name": rel.source.name,
            "target_name": rel.target.name,
        }
        set_clause = ""
        if rel_props:
            set_clause = " SET " + ", ".join(f"r.{k} = ${k}" for k in rel_props.keys())
            params.update(rel_props)
        query = (
            f"MATCH (s:`{rel.source.type}` {{name: $source_name}}) "
            f"MATCH (t:`{rel.target.type}` {{name: $target_name}}) "
            f"MERGE (s)-[r:`{rel.relation_type}`]->(t)"
            f"{set_clause}"
        )
        relation_queries.append((query, params))

    # Phase 4: Execute all writes in two batches (entities first, then relations)
    if entity_queries:
        neo4j_client.execute_write_batch(entity_queries)
    if relation_queries:
        neo4j_client.execute_write_batch(relation_queries)


def _log_ingest(source: str, filename: str, entities_count: int, relations_count: int, success: bool, message: str = "") -> None:
    """Write an IngestLog node to the graph."""
    if neo4j_client is None:
        return
    ts = datetime.now().isoformat()
    props = {
        "name": f"{source}_{filename}_{ts}",
        "source": source,
        "filename": filename,
        "entities_count": entities_count,
        "relations_count": relations_count,
        "timestamp": ts,
        "success": success,
        "message": message,
    }
    neo4j_client.create_node("IngestLog", props, merge=True)


@router.post("/ingest/text", response_model=IngestResponse)
async def ingest_text(request: IngestTextRequest) -> IngestResponse:
    result = ExtractionResult()
    if request.use_llm:
        try:
            extractor = LLMExtractor()
            result = await extractor.extract(request.text)
        except Exception as e:
            logger.error(f"LLM extraction failed: {e}")
            if not request.use_rule_fallback:
                raise HTTPException(status_code=500, detail=f"LLM extraction failed: {e}")

    if not result.entities and request.use_rule_fallback:
        rule_extractor = RuleExtractor()
        result = rule_extractor.extract(request.text)

    _stamp_source(result, "text")
    try:
        _write_to_graph(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to write to graph: {e}")
        _log_ingest("text", "", len(result.entities), len(result.relations), False, str(e))
        raise HTTPException(status_code=500, detail=f"Graph write failed: {e}")

    _log_ingest("text", "", len(result.entities), len(result.relations), True)
    return IngestResponse(
        status="success",
        entities_count=len(result.entities),
        relations_count=len(result.relations),
        entities=[e.model_dump() for e in result.entities],
        relations=[r.model_dump() for r in result.relations],
    )


def _load_file_content(
    file_path: str, suffix: str, filename: str, raw_bytes: bytes
) -> tuple[str, ExtractionResult]:
    """Load and parse a file, returning (all_text, dxf_direct_result)."""
    dxf_result = ExtractionResult()

    if suffix == ".pdf":
        loader = PDFLoader()
        chunks = loader.load_and_chunk(file_path)
        all_text = "\n\n".join(chunks)
    elif suffix in (".step", ".stp", ".igs", ".iges"):
        loader = STEPLoader()
        all_text = loader.load_as_text(file_path)
        logger.info(f"STEP file parsed: {len(all_text)} chars of metadata")
    elif suffix == ".dxf":
        loader = DXFLoader()
        dxf_meta = loader.load_metadata(file_path)
        all_text = loader.load_as_text(file_path)
        dxf_result = _dxf_meta_to_entities(dxf_meta, filename)
        logger.info(f"DXF file parsed: {dxf_meta.entity_count} entities, {dxf_meta.layer_count} layers, "
                    f"{dxf_meta.block_count} blocks -> {len(dxf_result.entities)} direct entities")
    elif suffix == ".dwg":
        try:
            dxf_path = _convert_dwg_to_dxf(file_path)
        except Exception as e:
            logger.warning(f"DWG->DXF conversion failed, falling back to string scan: {e}")
            all_text = _extract_dwg_strings(file_path, filename)
            logger.info(f"DWG file scanned: {len(all_text)} chars extracted")
        else:
            try:
                loader = DXFLoader()
                dxf_meta = loader.load_metadata(dxf_path)
                all_text = loader.load_as_text(dxf_path)
                dxf_result = _dxf_meta_to_entities(dxf_meta, filename)
                logger.info(f"DWG->DXF parsed: {dxf_meta.entity_count} entities, {dxf_meta.layer_count} layers, "
                            f"{dxf_meta.block_count} blocks -> {len(dxf_result.entities)} direct entities")
            finally:
                _safe_remove_dir(os.path.dirname(dxf_path))
    else:
        all_text = raw_bytes.decode("utf-8", errors="ignore")

    return all_text, dxf_result


def _safe_remove_dir(dir_path: str) -> None:
    """Best-effort recursive cleanup of a directory."""
    import shutil
    try:
        shutil.rmtree(dir_path, ignore_errors=True)
    except Exception:
        pass


async def _extract_knowledge(text: str, use_llm: bool) -> ExtractionResult:
    """Extract knowledge from text using LLM with rule fallback."""
    llm_text = text
    if len(text) > 4000:
        llm_text = text[:3500] + "\n...(文本过长已截断，完整内容由规则引擎处理)"
        logger.info(f"File text truncated for LLM: {len(text)} -> {len(llm_text)} chars")

    result = ExtractionResult()
    if use_llm and llm_text.strip():
        try:
            extractor = LLMExtractor()
            result = await extractor.extract(llm_text)
        except Exception as e:
            logger.error(f"LLM extraction failed: {e}")

    if not result.entities and text.strip():
        rule_extractor = RuleExtractor()
        result = rule_extractor.extract(text)

    return result


@router.post("/ingest/file", response_model=IngestResponse)
async def ingest_file(
    file: UploadFile = File(...),
    use_llm: bool = True,
) -> IngestResponse:
    suffix = os.path.splitext(file.filename or "")[1].lower()
    content = await file.read()

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        if suffix == ".csv":
            mapper = StructuredMapper()
            result = mapper.map_csv(tmp_path)
            _stamp_source(result, file.filename or "unknown.csv")
            for e in result.entities:
                e.confidence = 1.0
            for r in result.relations:
                r.confidence = 1.0
            _write_to_graph(result)
            _log_ingest("file", file.filename or "unknown.csv", len(result.entities), len(result.relations), True)
            return IngestResponse(
                status="success",
                entities_count=len(result.entities),
                relations_count=len(result.relations),
            )

        all_text, dxf_result = _load_file_content(tmp_path, suffix, file.filename or "unknown", content)
        result = await _extract_knowledge(all_text, use_llm)

        # Merge direct CAD mapping results with LLM/rule results
        result.entities = dxf_result.entities + result.entities
        result.relations = dxf_result.relations + result.relations

        _stamp_source(result, file.filename or "unknown")
        _write_to_graph(result)
        _log_ingest("file", file.filename or "unknown", len(result.entities), len(result.relations), True)
        return IngestResponse(
            status="success",
            entities_count=len(result.entities),
            relations_count=len(result.relations),
            entities=[e.model_dump() for e in result.entities],
            relations=[r.model_dump() for r in result.relations],
        )
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


@router.post("/ingest/url", response_model=IngestResponse)
async def ingest_url(request: IngestURLRequest) -> IngestResponse:
    try:
        web_loader = WebLoader()
        text = web_loader.load(request.url, request.selector)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {e}")

    result = ExtractionResult()
    if request.use_llm:
        try:
            extractor = LLMExtractor()
            result = await extractor.extract(text)
        except Exception as e:
            logger.error(f"LLM extraction failed: {e}")

    if not result.entities:
        rule_extractor = RuleExtractor()
        result = rule_extractor.extract(text)

    _stamp_source(result, request.url)
    _write_to_graph(result)
    _log_ingest("url", request.url, len(result.entities), len(result.relations), True)
    return IngestResponse(
        status="success",
        entities_count=len(result.entities),
        relations_count=len(result.relations),
    )


# ── Ingest Logs ──────────────────────────────────────────────


@router.get("/ingest/logs", response_model=List[Dict[str, Any]])
def get_ingest_logs(limit: int = 50):
    """返回最近的摄入日志记录"""
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")
    return neo4j_client.get_ingest_logs(limit)


@router.get("/ingest/files", response_model=List[Dict[str, Any]])
def list_graph_files():
    """列出图谱中所有已上传文件及其关联实体数（不依赖 IngestLog）"""
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")
    return neo4j_client.list_graph_files()


@router.delete("/ingest/log/by-source")
def delete_ingest_log(source: str):
    """删除指定来源的摄入日志"""
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")
    count = neo4j_client.delete_ingest_log(source)
    return {"status": "deleted", "count": count}


@router.delete("/ingest/file/{filename:path}")
def delete_file_entities(filename: str):
    """删除指定文件关联的所有图谱节点和关系"""
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")
    count = neo4j_client.delete_by_file(filename)
    logger.info(f"Deleted {count} nodes for file '{filename}'")
    return {"status": "deleted", "filename": filename, "nodes_removed": count}
