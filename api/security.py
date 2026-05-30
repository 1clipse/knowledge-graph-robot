"""API security: auth modes, admin token validation, audit logging.

AUTH MODES (KG_AUTH_MODE env var):
  - "none":       No authentication (default when KG_API_KEY is empty)
  - "admin_only": Only dangerous endpoints (POST /query, DELETE, ingest)
                  require KG_ADMIN_KEY; read endpoints are open
  - "full":       All /api/v1/ endpoints require KG_API_KEY;
                  dangerous endpoints additionally require KG_ADMIN_KEY

Admin token (KG_ADMIN_KEY) is separate from the regular API key.
If KG_ADMIN_KEY is not set but auth mode is "admin_only", admin endpoints
are rejected with 403.
"""

from __future__ import annotations

import re
import time
from functools import wraps
from typing import Callable, Optional, Set

from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger

from config.settings import get_config


# Paths exempt from auth even in "full" mode
SKIP_AUTH_PATHS: Set[str] = {
    "/", "/health", "/chat", "/docs", "/openapi.json", "/redoc",
    "/css/", "/js/", "/favicon.ico",
}

# Endpoints that require admin token in "admin_only" or "full" mode
_ADMIN_REQUIRED_PREFIXES: Set[str] = {
    "/api/v1/query/",  # raw Cypher execution
    "/api/v1/ingest",   # data ingestion
}

_ADMIN_REQUIRED_METHODS: dict[str, Set[str]] = {
    "DELETE": set(),  # all DELETE require admin
}

# ── Cypher write-operation patterns (blocked for non-admin) ──

_CYPHER_WRITE_PATTERNS = [
    (re.compile(r"\bCREATE\b", re.IGNORECASE), "CREATE"),
    (re.compile(r"\bMERGE\b", re.IGNORECASE), "MERGE"),
    (re.compile(r"\bDELETE\b", re.IGNORECASE), "DELETE"),
    (re.compile(r"\bDETACH\s+DELETE\b", re.IGNORECASE), "DETACH DELETE"),
    (re.compile(r"\bSET\b", re.IGNORECASE), "SET"),
    (re.compile(r"\bREMOVE\b", re.IGNORECASE), "REMOVE"),
    (re.compile(r"\bDROP\b", re.IGNORECASE), "DROP"),
    (re.compile(r"\bLOAD\s+CSV\b", re.IGNORECASE), "LOAD CSV"),
    (re.compile(r"\bCALL\s+db\b", re.IGNORECASE), "CALL db.*"),
    (re.compile(r"\bCALL\s+apoc\b", re.IGNORECASE), "CALL apoc.*"),
]


def _is_admin_path(method: str, path: str) -> bool:
    """Check if a path requires admin privileges."""
    if method.upper() == "DELETE":
        return True
    for prefix in _ADMIN_REQUIRED_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def _is_skip_path(path: str) -> bool:
    if path in SKIP_AUTH_PATHS:
        return True
    for prefix in ["/css/", "/js/"]:
        if path.startswith(prefix):
            return True
    return False


async def auth_middleware(request: Request, call_next: Callable):
    """Main auth middleware. Evaluates KG_AUTH_MODE and enforces checks."""
    path = request.url.path
    method = request.method

    # Always allow skip paths
    if _is_skip_path(path):
        return await call_next(request)

    # Only protect /api/v1/ paths
    if not path.startswith("/api/v1/"):
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    auth_config = get_config().auth
    auth_mode = (auth_config.mode or "none").lower()
    api_key = auth_config.api_key
    admin_key = auth_config.admin_key

    if auth_mode == "none":
        return await call_next(request)

    elif auth_mode == "admin_only":
        if _is_admin_path(method, path):
            blocked = _check_admin_auth(auth_header, request, admin_key)
            if blocked is not None:
                return blocked
        return await call_next(request)

    elif auth_mode == "full":
        # All API paths need at least API key
        if not api_key:
            blocked = _check_admin_auth(auth_header, request, admin_key)
            if blocked is not None:
                return blocked
            return await call_next(request)
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() != "bearer" or token not in (api_key, admin_key):
            return _json_403("Invalid API key")
        # Admin paths additionally require admin key
        if _is_admin_path(method, path):
            if token != admin_key or not admin_key:
                return _json_403("Admin key required for this endpoint")
        return await call_next(request)

    else:
        logger.warning(f"Unknown KG_AUTH_MODE: {auth_mode}")
        return await call_next(request)


def _check_admin_auth(auth_header: str, request: Request, admin_key: str):
    """Validate admin key, or reject if not configured."""
    if not admin_key:
        logger.warning(f"Admin endpoint {request.method} {request.url.path} blocked: KG_ADMIN_KEY not set")
        return _json_403("Admin key not configured on server")
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or token != admin_key:
        return _json_403("Invalid or missing admin key")
    return None  # OK, caller must check for None


def _json_401(msg: str) -> JSONResponse:
    return JSONResponse(status_code=401, content={"detail": msg})


def _json_403(msg: str) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": msg})


def validate_read_only_cypher(cypher: str) -> Optional[str]:
    """Check a Cypher query is read-only. Returns error message or None."""
    for pattern, name in _CYPHER_WRITE_PATTERNS:
        if pattern.search(cypher):
            return f"Write operation '{name}' is not allowed. Only read-only Cypher queries are permitted."
    return None


# ── Audit logging ──


def _ensure_audit_logger():
    """Lazily configure audit logger."""
    if not hasattr(_ensure_audit_logger, "_ready"):
        logger.add(
            get_config().app.audit_log_path,
            format="{time:YYYY-MM-DD HH:mm:ss} | AUDIT | {message}",
            rotation="50 MB",
            retention="90 days",
            level="INFO",
            filter=lambda record: record["extra"].get("audit", False),
        )
        _ensure_audit_logger._ready = True


def audit_log(
    action: str,
    method: str = "",
    path: str = "",
    client_ip: str = "",
    user_agent: str = "",
    details: str = "",
) -> None:
    """Log an auditable action."""
    _ensure_audit_logger()
    parts = [
        f"action={action}",
        f"method={method}" if method else "",
        f"path={path}" if path else "",
        f"ip={client_ip}" if client_ip else "",
        f"ua={user_agent[:100]}" if user_agent else "",
        details if details else "",
    ]
    msg = " | ".join(p for p in parts if p)
    logger.bind(audit=True).info(msg)


async def audit_middleware(request: Request, call_next: Callable):
    """Middleware that logs all write operations and admin access."""
    start = time.time()
    response = await call_next(request)
    elapsed_ms = (time.time() - start) * 1000

    method = request.method
    path = request.url.path

    # Log all non-GET, non-HEAD requests
    if method not in ("GET", "HEAD"):
        audit_log(
            action="write",
            method=method,
            path=path,
            client_ip=request.client.host if request.client else "",
            user_agent=request.headers.get("User-Agent", ""),
            details=f"status={response.status_code} elapsed={elapsed_ms:.0f}ms",
        )

    return response
