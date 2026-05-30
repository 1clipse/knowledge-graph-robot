from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import get_db, get_schema_manager
from config.settings import get_config
from extractors.llm_extractor import ExtractionResult, ExtractedEntity, ExtractedRelation, EntityRef
from extractors.funnel import ExtractionFunnel
from graph.client import Neo4jClient
from graph.schema_manager import SchemaManager
from graph.writer import GraphWriter
from extractors.structured_mapper import StructuredMapper
from loaders.pdf_loader import PDFLoader
from loaders.csv_loader import CSVLoader
from loaders.web_loader import URLSafetyError, WebLoader
from loaders.step_loader import STEPLoader
from loaders.dxf_loader import DXFLoader
from loaders.cad_adapter import CADGraphData
from loaders.txt_loader import TXTLoader
from loaders.docx_loader import DocxLoader

router = APIRouter()

_ODA_CONVERTER = os.environ.get("ODA_CONVERTER_PATH", "")


def _get_oda_path() -> str:
    """Resolve ODA converter path from env or config."""
    if _ODA_CONVERTER:
        return _ODA_CONVERTER
    try:
        return get_config().paths.oda_converter_path
    except Exception:
        return ""


def _convert_dwg_to_dxf(dwg_path: str) -> str:
    """使用 ODA File Converter 将 DWG 转为 DXF，返回需由调用方清理的 DXF 文件路径。"""
    import shutil

    oda_path = _get_oda_path()
    if not oda_path:
        raise RuntimeError(
            "ODA File Converter path not configured. "
            "Set ODA_CONVERTER_PATH env var or add 'paths.oda_converter_path' to config/default.yaml."
        )

    dwg_name = Path(dwg_path).stem
    src_name = os.path.basename(dwg_path)

    # ODA CLI 要求输入/输出都是目录。输入/输出 staging 目录由本函数清理；
    # 成功转换后的 DXF 复制到独立临时目录并交给调用方清理。
    with tempfile.TemporaryDirectory(prefix="dwg_input_") as input_dir:
        with tempfile.TemporaryDirectory(prefix="dwg_output_") as output_dir:
            shutil.copy2(dwg_path, os.path.join(input_dir, src_name))

            cmd = [
                oda_path,
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
                    f"ODA File Converter not found at {oda_path}. "
                    "Set ODA_CONVERTER_PATH env var or install ODA File Converter."
                )

            dxf_files = list(Path(output_dir).glob("*.dxf"))
            if not dxf_files:
                raise RuntimeError(f"No DXF output found in {output_dir}")

            result_dir = tempfile.mkdtemp(prefix="dwg_dxf_")
            result_path = os.path.join(result_dir, dxf_files[0].name)
            shutil.copy2(str(dxf_files[0]), result_path)
            logger.info(f"DWG→DXF converted: {dwg_name} → {dxf_files[0].name}")
            return result_path


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


def _cad_graph_data_to_result(data: CADGraphData) -> ExtractionResult:
    """Convert ontology-aligned CADGraphData into the local extraction result shape."""
    entities = [
        ExtractedEntity(
            name=item.get("name", ""),
            type=item.get("type", ""),
            properties=item.get("properties", {}) or {},
            source=item.get("source", data.file_path),
            confidence=item.get("confidence", 0.7),
        )
        for item in data.entities
        if _is_valid_entity_name(item.get("name", ""))
    ]
    entity_keys = {(entity.type, entity.name) for entity in entities}
    relations = [
        ExtractedRelation(
            source=EntityRef(
                name=item.get("source", {}).get("name", ""),
                type=item.get("source", {}).get("type", ""),
            ),
            target=EntityRef(
                name=item.get("target", {}).get("name", ""),
                type=item.get("target", {}).get("type", ""),
            ),
            relation_type=item.get("relation_type", ""),
            properties=item.get("properties", {}) or {},
            source_ref=data.file_path,
            confidence=item.get("confidence", 0.7),
        )
        for item in data.relations
        if (
            (item.get("source", {}).get("type", ""), item.get("source", {}).get("name", "")) in entity_keys
            and (item.get("target", {}).get("type", ""), item.get("target", {}).get("name", "")) in entity_keys
        )
    ]
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


def _write_to_graph(db: Neo4jClient, sm: SchemaManager, result: ExtractionResult) -> None:
    """Write extraction result to graph via GraphWriter (centralized schema validation)."""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not connected")
    if not result.entities:
        return
    writer = GraphWriter(db, sm)
    writer.write(result)


def _log_ingest(db: Neo4jClient, source: str, filename: str, entities_count: int, relations_count: int, success: bool, message: str = "") -> None:
    """Write an IngestLog node to the graph."""
    if db is None:
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
    db.create_node("IngestLog", props, merge=True)


@router.post("/ingest/text", response_model=IngestResponse)
async def ingest_text(
    request: IngestTextRequest,
    db: Neo4jClient = Depends(get_db),
    sm: SchemaManager = Depends(get_schema_manager),
) -> IngestResponse:
    result = await _extract_knowledge(request.text, use_llm=request.use_llm)

    _stamp_source(result, "text")
    try:
        _write_to_graph(db, sm, result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to write to graph: {e}")
        _log_ingest(db, "text", "", len(result.entities), len(result.relations), False, str(e))
        raise HTTPException(status_code=500, detail=f"Graph write failed: {e}")

    _log_ingest(db, "text", "", len(result.entities), len(result.relations), True)
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
        dxf_result = _cad_graph_data_to_result(loader.to_graph_data(file_path))
        logger.info(f"STEP file parsed: {len(all_text)} chars, {len(dxf_result.entities)} ontology entities")
    elif suffix == ".dxf":
        loader = DXFLoader()
        dxf_meta = loader.load_metadata(file_path)
        all_text = loader.load_as_text(file_path)
        dxf_result = _cad_graph_data_to_result(loader.to_graph_data(file_path))
        logger.info(f"DXF file parsed: {dxf_meta.entity_count} entities, {dxf_meta.layer_count} layers, "
                    f"{dxf_meta.block_count} blocks -> {len(dxf_result.entities)} ontology entities")
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
                dxf_result = _cad_graph_data_to_result(loader.to_graph_data(dxf_path))
                logger.info(f"DWG->DXF parsed: {dxf_meta.entity_count} entities, {dxf_meta.layer_count} layers, "
                            f"{dxf_meta.block_count} blocks -> {len(dxf_result.entities)} ontology entities")
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
    """Run the shared local extraction funnel used by routes and pipelines."""
    return await ExtractionFunnel().extract(text, use_llm=use_llm)


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
    db: Neo4jClient = Depends(get_db),
    sm: SchemaManager = Depends(get_schema_manager),
):
    """批量上传多个文件"""
    response = BatchIngestResponse(status="success", total_files=len(files))

    for file in files:
        suffix = os.path.splitext(file.filename or "")[1].lower()
        content = await file.read()
        tmp_path = ""

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
                _write_to_graph(db, sm, result)
                _log_ingest(db, "file", file.filename or "unknown.csv", len(result.entities), len(result.relations), True)
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
                _write_to_graph(db, sm, result)
                _log_ingest(db, "file", file.filename or "unknown", len(result.entities), len(result.relations), True)
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
    db: Neo4jClient = Depends(get_db),
    sm: SchemaManager = Depends(get_schema_manager),
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
            _write_to_graph(db, sm, result)
            _log_ingest(db, "file", file.filename or "unknown.csv", len(result.entities), len(result.relations), True)
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
        _write_to_graph(db, sm, result)
        _log_ingest(db, "file", file.filename or "unknown", len(result.entities), len(result.relations), True)
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
async def ingest_url(
    request: IngestURLRequest,
    db: Neo4jClient = Depends(get_db),
    sm: SchemaManager = Depends(get_schema_manager),
) -> IngestResponse:
    try:
        web_loader = WebLoader()
        text = web_loader.load(request.url, request.selector)
    except URLSafetyError as e:
        raise HTTPException(status_code=400, detail=f"Unsafe URL: {e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {e}")

    result = await _extract_knowledge(text, use_llm=request.use_llm)

    _stamp_source(result, request.url)
    _write_to_graph(db, sm, result)
    _log_ingest(db, "url", request.url, len(result.entities), len(result.relations), True)
    return IngestResponse(
        status="success",
        entities_count=len(result.entities),
        relations_count=len(result.relations),
    )


# ── Ingest Logs ──────────────────────────────────────────────


@router.get("/ingest/logs", response_model=List[Dict[str, Any]])
def get_ingest_logs(limit: int = 50, db: Neo4jClient = Depends(get_db)):
    """返回最近的摄入日志记录"""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not connected")
    return db.get_ingest_logs(limit)


@router.get("/ingest/files", response_model=List[Dict[str, Any]])
def list_graph_files(db: Neo4jClient = Depends(get_db)):
    """列出图谱中所有已上传文件及其关联实体数（不依赖 IngestLog）"""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not connected")
    return db.list_graph_files()


@router.delete("/ingest/log/by-source")
def delete_ingest_log(source: str, db: Neo4jClient = Depends(get_db)):
    """删除指定来源的摄入日志"""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not connected")
    count = db.delete_ingest_log(source)
    return {"status": "deleted", "count": count}


@router.delete("/ingest/file/{filename:path}")
def delete_file_entities(filename: str, db: Neo4jClient = Depends(get_db)):
    """删除指定文件关联的所有图谱节点和关系"""
    if db is None:
        raise HTTPException(status_code=503, detail="Database not connected")
    count = db.delete_by_file(filename)
    logger.info(f"Deleted {count} nodes for file '{filename}'")
    return {"status": "deleted", "filename": filename, "nodes_removed": count}
