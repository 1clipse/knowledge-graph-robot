from __future__ import annotations

import os as _os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from loguru import logger

from api.routes import ingest, query, ask, subgraph, quality, eval
from api.security import auth_middleware, audit_middleware
from config.settings import get_config
from graph.client import Neo4jClient
from graph.schema_manager import SchemaManager
from api import deps

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_UI_DIR = _PROJECT_ROOT / "ui"

_API_KEY = _os.environ.get("KG_API_KEY", "")
_CORS_ORIGINS = _os.environ.get("KG_CORS_ORIGINS", "http://localhost:3000,http://localhost:8000").split(",")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    config = get_config()
    logger.add(
        "logs/kg_robot.log",
        rotation=config.logging.rotation,
        retention=config.logging.retention,
        level=config.logging.level,
        format=config.logging.log_format,
    )
    logger.info("Starting Industrial Robot Knowledge Graph API...")

    auth_mode = _os.environ.get("KG_AUTH_MODE", "none")
    if not _API_KEY and auth_mode != "none":
        logger.warning("KG_AUTH_MODE={} but KG_API_KEY not set — auth will fail", auth_mode)
    admin_key = _os.environ.get("KG_ADMIN_KEY", "")
    if not admin_key and auth_mode == "admin_only":
        logger.warning("KG_AUTH_MODE=admin_only but KG_ADMIN_KEY not set — admin endpoints blocked")

    # BGE-M3 model path (read from .env, fall back to default)
    _os.environ.setdefault("EMBEDDING_MODEL_PATH", "E:/huggingface_cache/BAAI/bge-m3")
    _os.environ.setdefault("HF_HOME", "E:/huggingface_cache")
    _os.environ.setdefault("HF_HUB_OFFLINE", "1")

    # Preload embedding model in background to avoid blocking first request
    from graph.embeddings import init_model
    init_model()

    try:
        deps.neo4j_client.connect()
        deps.schema_manager = SchemaManager(deps.neo4j_client)
        deps.schema_manager.initialize_schema()
        # Store in app.state for dependency injection
        app.state.neo4j_client = deps.neo4j_client
        app.state.schema_manager = deps.schema_manager
        # Clean up IngestLog nodes with missing names (from old code)
        try:
            result = deps.neo4j_client.execute_query(
                "MATCH (n:IngestLog) WHERE n.name IS NULL OR n.name = '' "
                "WITH n, count(n) AS cnt DETACH DELETE n RETURN cnt"
            )
            count = result[0]["cnt"] if result else 0
            if count:
                logger.info(f"Cleaned up {count} stale IngestLog nodes")
        except Exception as e:
            logger.warning(f"Failed to clean up stale IngestLog nodes: {e}")
        logger.info("Database schema initialized")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        logger.warning("API will start but database features will be unavailable")

    yield

    logger.info("Shutting down...")
    deps.neo4j_client.close()


app = FastAPI(
    title="工业机器人知识图谱 API",
    description="Industrial Robot Knowledge Graph System API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security: auth middleware (applied before audit so rejected requests are logged)
app.middleware("http")(auth_middleware)
# Audit: log all write operations
app.middleware("http")(audit_middleware)


@app.get("/", response_class=HTMLResponse)
def index_page():
    return (_UI_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/chat", response_class=HTMLResponse)
def chat_page():
    return (_UI_DIR / "index.html").read_text(encoding="utf-8")


# Static file serving — /css/* and /js/*
@app.get("/css/{filename:path}")
async def serve_css(filename: str):
    file_path = _UI_DIR / "css" / filename
    if not file_path.resolve().is_relative_to(_UI_DIR.resolve()):
        raise HTTPException(status_code=404)
    if not file_path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(str(file_path))

@app.get("/js/{filename:path}")
async def serve_js(filename: str):
    file_path = _UI_DIR / "js" / filename
    if not file_path.resolve().is_relative_to(_UI_DIR.resolve()):
        raise HTTPException(status_code=404)
    if not file_path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(str(file_path), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

app.include_router(ingest.router, prefix="/api/v1", tags=["数据摄入"])
app.include_router(query.router, prefix="/api/v1", tags=["图查询"])
app.include_router(ask.router, prefix="/api/v1", tags=["知识问答"])
app.include_router(subgraph.router, prefix="/api/v1", tags=["可视化"])
app.include_router(quality.router, prefix="/api/v1", tags=["数据质量"])
app.include_router(eval.router, prefix="/api/v1", tags=["质量评估"])


@app.get("/health")
def health_check() -> dict:
    db_ok = deps.neo4j_client.health_check()
    return {"status": "ok" if db_ok else "degraded", "database": db_ok}
