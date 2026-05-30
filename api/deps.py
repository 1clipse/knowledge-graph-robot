from __future__ import annotations

from typing import Optional

from fastapi import Request

from graph.client import Neo4jClient
from graph.schema_manager import SchemaManager


def get_db(request: Request) -> Optional[Neo4jClient]:
    """FastAPI dependency: returns the Neo4jClient from app state."""
    return getattr(request.app.state, "neo4j_client", None)


def get_schema_manager(request: Request) -> Optional[SchemaManager]:
    """FastAPI dependency: returns the SchemaManager from app state."""
    return getattr(request.app.state, "schema_manager", None)
