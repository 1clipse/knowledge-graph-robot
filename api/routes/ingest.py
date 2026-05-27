from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, File, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import neo4j_client, schema_manager
from extractors.llm_extractor import LLMExtractor, ExtractionResult, ExtractedEntity, ExtractedRelation, EntityRef
from graph.writer import GraphWriter
from extractors.rule_extractor import RuleExtractor
from extractors.structured_mapper import StructuredMapper
from loaders.pdf_loader import PDFLoader
from loaders.csv_loader import CSVLoader
from loaders.web_loader import WebLoader
from loaders.step_loader import STEPLoader
from loaders.dxf_loader import DXFLoader, DXFMetadata
from loaders.txt_loader import TXTLoader
from loaders.docx_loader import DocxLoader

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


def _is_valid_entity_name(name: str) -> bool:
    """Filter out truly empty or internal CAD names only."""
    if not name or not name.strip():
        return False
    name = name.strip()
    if name.startswith("*") or name in ("_None",):
        return False
    return True


def _dxf_meta_to_entities(meta: DXFMetadata, filename: str = "") -> ExtractionResult:
    """将 DXF 结构化元数据直接映射为知识图谱实体，不依赖 LLM"""
    entities: List[ExtractedEntity] = []
    relations: List[ExtractedRelation] = []

    drawing_name = filename or "CAD_Drawing"

    # Block names → Component entities
    component_names: set[str] = set()
    for name in meta.block_names:
        name = name.strip()
        if _is_valid_entity_name(name):
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
        if _is_valid_entity_name(tag) and text_val and len(tag) >= 2:
            entities.append(ExtractedEntity(
                name=tag,
                type="Component",
                properties={"value": text_val, "source": "DXF_ATTR"}
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
    """Write extraction result to graph via GraphWriter (centralized schema validation)."""
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")
    if not result.entities:
        return
    writer = GraphWriter(neo4j_client, schema_manager)
    writer.write(result)


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
    result = await _extract_knowledge(request.text, use_llm=request.use_llm)

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
    elif suffix in (".docx", ".doc"):
        loader = DocxLoader()
        all_text = loader.load(file_path)
        logger.info(f"DOCX file parsed: {len(all_text)} chars")
    elif suffix in (".txt", ".md", ".html", ".htm", ".json", ".xml", ".yaml", ".yml", ".csv.txt"):
        loader = TXTLoader()
        all_text = loader.load(file_path)
        logger.info(f"TXT file loaded: {len(all_text)} chars")
    elif suffix in (".xlsx", ".xls", ".pptx", ".ppt"):
        raise ValueError(
            f"File type '{suffix}' is not yet supported. "
            "Please convert to PDF/TXT/DOCX and re-upload."
        )
    else:
        logger.warning(f"Unknown file type '{suffix}' for '{filename}', attempting UTF-8 decode")
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
    """4-tier extraction funnel: Rule → spaCy → merge → LLM augment (low-confidence).

    Tier 2 (Rule):   Fast regex, confidence 0.90–0.95. Catches known patterns.
    Tier 3 (spaCy):  NER + dependency relations, confidence 0.75–0.90. Handles variants.
    Tier 4 (LLM):    Only runs on low-confidence (< 0.7) or missing entity types.
    """
    result = ExtractionResult()

    # Tier 2: Rule-based extraction (always run — fast & precise)
    if text.strip():
        try:
            rule_extractor = RuleExtractor()
            result = rule_extractor.extract(text)
            for e in result.entities:
                e.confidence = e.confidence or 0.95
            for r in result.relations:
                r.confidence = r.confidence or 0.95
        except Exception as e:
            logger.warning(f"Rule extraction failed: {e}")

    # Tier 3: spaCy extraction (adds recall, catches rule misses)
    if text.strip():
        try:
            from extractors.spacy_extractor import SpacyExtractor
            spacy_extractor = SpacyExtractor()
            spacy_result = spacy_extractor.extract(text)
            result = _merge_results(result, spacy_result)
        except Exception as e:
            logger.warning(f"spaCy extraction failed: {e}, continuing without it")

    # Tier 4: LLM augments low-confidence / missing areas
    llm_text = text
    if len(text) > 4000:
        llm_text = text[:3500] + "\n...(文本过长已截断，完整内容由规则引擎处理)"
        logger.info(f"File text truncated for LLM: {len(text)} -> {len(llm_text)} chars")

    low_conf_entities = [e for e in result.entities if e.confidence < 0.7]
    has_missing = len(result.entities) < 3  # very few entities → likely missed something

    if use_llm and llm_text.strip() and (low_conf_entities or has_missing):
        try:
            extractor = LLMExtractor()
            llm_result = await extractor.extract(llm_text)
            result = _augment_low_confidence(result, llm_result)
        except Exception as e:
            logger.error(f"LLM augmentation failed: {e}")

    return result


def _merge_results(a: ExtractionResult, b: ExtractionResult) -> ExtractionResult:
    """Merge two extraction results, keeping highest-confidence entries."""
    entity_map: Dict[str, ExtractedEntity] = {}
    for e in a.entities:
        key = f"{e.type}::{e.name}"
        entity_map[key] = e
    for e in b.entities:
        key = f"{e.type}::{e.name}"
        if key in entity_map:
            existing = entity_map[key]
            existing.confidence = max(existing.confidence, e.confidence)
            for k, v in (e.properties or {}).items():
                if k not in (existing.properties or {}):
                    existing.properties[k] = v
        else:
            entity_map[key] = e

    rel_set: Set[Tuple[str, str, str]] = set()
    merged_rels: List[ExtractedRelation] = []
    for r in list(a.relations) + list(b.relations):
        key = (r.source.name, r.relation_type, r.target.name)
        if key not in rel_set:
            rel_set.add(key)
            merged_rels.append(r)

    return ExtractionResult(entities=list(entity_map.values()), relations=merged_rels)


def _augment_low_confidence(
    merged: ExtractionResult,
    llm_result: ExtractionResult,
) -> ExtractionResult:
    """Use LLM entities to fill gaps in merged result (low-conf or missing types)."""
    merged_types: Set[str] = {e.type for e in merged.entities}
    merged_keys: Set[str] = {f"{e.type}::{e.name}" for e in merged.entities}

    for e in llm_result.entities:
        key = f"{e.type}::{e.name}"
        if key not in merged_keys:
            e.confidence = max(e.confidence, 0.70)  # LLM baseline
            merged.entities.append(e)
            merged_keys.add(key)

    rel_set: Set[Tuple[str, str, str]] = {
        (r.source.name, r.relation_type, r.target.name) for r in merged.relations
    }
    for r in llm_result.relations:
        key = (r.source.name, r.relation_type, r.target.name)
        if key not in rel_set:
            r.confidence = max(r.confidence, 0.70)
            merged.relations.append(r)
            rel_set.add(key)

    return merged


class BatchIngestResponse(BaseModel):
    status: str
    total_files: int = 0
    success_count: int = 0
    failed_count: int = 0
    total_entities: int = 0
    total_relations: int = 0
    details: List[Dict[str, Any]] = Field(default_factory=list)


@router.post("/ingest/batch", response_model=BatchIngestResponse)
async def ingest_batch(
    files: List[UploadFile] = File(...),
    use_llm: bool = True,
):
    """批量上传多个文件"""
    response = BatchIngestResponse(status="success", total_files=len(files))

    for file in files:
        suffix = os.path.splitext(file.filename or "")[1].lower()
        content = await file.read()

        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(content)
                tmp_path = tmp.name

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
                response.total_entities += len(result.entities)
                response.total_relations += len(result.relations)
                response.success_count += 1
                response.details.append({"filename": file.filename, "status": "ok", "entities": len(result.entities), "relations": len(result.relations)})
            else:
                all_text, dxf_result = _load_file_content(tmp_path, suffix, file.filename or "unknown", content)
                result = await _extract_knowledge(all_text, use_llm)
                result.entities = dxf_result.entities + result.entities
                result.relations = dxf_result.relations + result.relations

                _stamp_source(result, file.filename or "unknown")
                _write_to_graph(result)
                _log_ingest("file", file.filename or "unknown", len(result.entities), len(result.relations), True)
                response.total_entities += len(result.entities)
                response.total_relations += len(result.relations)
                response.success_count += 1
                response.details.append({"filename": file.filename, "status": "ok", "entities": len(result.entities), "relations": len(result.relations)})

        except Exception as e:
            logger.error(f"Batch ingest failed for {file.filename}: {e}")
            response.failed_count += 1
            response.details.append({"filename": file.filename, "status": "failed", "error": str(e)})
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    return response


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

    result = await _extract_knowledge(text, use_llm=request.use_llm)

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
