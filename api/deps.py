from __future__ import annotations

from typing import Optional

from fastapi import Request

from graph.client import Neo4jClient
from graph.schema_manager import SchemaManager

# Application-scoped instances (set during lifespan startup).
# Prefer get_db() / get_schema_manager() dependencies for new code.
neo4j_client: Neo4jClient = Neo4jClient()
schema_manager: Optional[SchemaManager] = None


def get_db(request: Request) -> Neo4jClient:
    """FastAPI dependency: returns the Neo4jClient from app state."""
    return request.app.state.neo4j_client


def get_schema_manager(request: Request) -> Optional[SchemaManager]:
    """FastAPI dependency: returns the SchemaManager from app state."""
    return request.app.state.schema_manager
