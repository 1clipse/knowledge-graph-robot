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

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_UI_DIR = _PROJECT_ROOT / "ui"


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

    auth_mode = config.auth.mode if config.auth.api_key else "none"
    if not config.auth.api_key and auth_mode != "none":
        logger.warning("KG_AUTH_MODE={} but KG_API_KEY not set — auth will fail", auth_mode)
    if not config.auth.admin_key and auth_mode == "admin_only":
        logger.warning("KG_AUTH_MODE=admin_only but KG_ADMIN_KEY not set — admin endpoints blocked")

    # BGE-M3 model path from config
    if config.embedding.model_path:
        _os.environ["EMBEDDING_MODEL_PATH"] = config.embedding.model_path
        if not Path(config.embedding.model_path).exists():
            logger.warning(f"Embedding model path does not exist: {config.embedding.model_path}")
    if config.embedding.hf_home:
        _os.environ["HF_HOME"] = config.embedding.hf_home
    if config.embedding.hf_hub_offline:
        _os.environ["HF_HUB_OFFLINE"] = "1"

    # Preload embedding model and expose readiness in /health. Startup waits briefly
    # so a bad model path is visible early without blocking the API for minutes.
    from graph.embeddings import init_model, status as embedding_status
    embedding_ready = init_model(wait=True, timeout=10)
    if embedding_ready:
        logger.info("Embedding model is ready")
    else:
        logger.warning(f"Embedding model is not ready at startup: {embedding_status()}")

    neo4j_client = Neo4jClient()
    schema_mgr: SchemaManager = None
    app.state.neo4j_client = None
    app.state.schema_manager = None
    try:
        neo4j_client.connect()
        schema_mgr = SchemaManager(neo4j_client)
        schema_mgr.initialize_schema()
        app.state.neo4j_client = neo4j_client
        app.state.schema_manager = schema_mgr
        # Clean up IngestLog nodes with missing names (from old code)
        try:
            result = neo4j_client.execute_query(
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
    neo4j_client.close()


app = FastAPI(
    title="工业机器人知识图谱 API",
    description="Industrial Robot Knowledge Graph System API",
    version="2.3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_config().app.cors_origin_list,
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
def health_check(request: Request) -> dict:
    db = getattr(request.app.state, "neo4j_client", None)
    db_ok = db.health_check() if db else False
    try:
        from graph.embeddings import status as embedding_status
        emb = embedding_status()
    except Exception as e:
        emb = {"ready": False, "error": str(e)}
    return {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
        "embedding": emb,
    }
