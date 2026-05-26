from __future__ import annotations

import os as _os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger

from api.routes import ingest, query, ask, subgraph, quality
from config.settings import get_config
from graph.client import Neo4jClient
from graph.schema_manager import SchemaManager
from api import deps

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_UI_DIR = _PROJECT_ROOT / "ui"

_API_KEY = _os.environ.get("KG_API_KEY", "")
_CORS_ORIGINS = _os.environ.get("KG_CORS_ORIGINS", "http://localhost:3000,http://localhost:8000").split(",")

SKIP_AUTH_PATHS = {"/health", "/", "/chat", "/docs", "/openapi.json", "/redoc"}


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
    if not _API_KEY:
        logger.warning("KG_API_KEY not set — API has no authentication")

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


@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    if not _API_KEY:
        return await call_next(request)

    path = request.url.path
    if path in SKIP_AUTH_PATHS:
        return await call_next(request)

    if path.startswith("/api/v1/"):
        auth = request.headers.get("Authorization", "")
        if not auth:
            return JSONResponse(status_code=401, content={"detail": "Missing Authorization header"})
        scheme, _, token = auth.partition(" ")
        if scheme.lower() != "bearer" or token != _API_KEY:
            return JSONResponse(status_code=403, content={"detail": "Invalid API key"})

    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
def index_page():
    return (_UI_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/chat", response_class=HTMLResponse)
def chat_page():
    return (_UI_DIR / "index.html").read_text(encoding="utf-8")


app.include_router(ingest.router, prefix="/api/v1", tags=["数据摄入"])
app.include_router(query.router, prefix="/api/v1", tags=["图查询"])
app.include_router(ask.router, prefix="/api/v1", tags=["知识问答"])
app.include_router(subgraph.router, prefix="/api/v1", tags=["可视化"])
app.include_router(quality.router, prefix="/api/v1", tags=["数据质量"])


@app.get("/health")
def health_check() -> dict:
    db_ok = deps.neo4j_client.health_check()
    return {"status": "ok" if db_ok else "degraded", "database": db_ok}
