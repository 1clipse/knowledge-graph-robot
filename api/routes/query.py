from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import neo4j_client
from graph.client import _validate_identifier
from graph.query import GraphQuery

router = APIRouter()


class QueryRequest(BaseModel):
    cypher: str = Field(..., description="Cypher查询语句 (只读)")
    parameters: Optional[Dict[str, Any]] = Field(default=None, description="查询参数")


class QueryResponse(BaseModel):
    status: str
    results: List[Dict[str, Any]] = Field(default_factory=list)
    count: int = 0


class StatsResponse(BaseModel):
    total_nodes: int = 0
    total_relations: int = 0
    node_labels: List[str] = Field(default_factory=list)
    relation_types: List[str] = Field(default_factory=list)
    top_degree_nodes: List[Dict[str, Any]] = Field(default_factory=list)


@router.post("/query", response_model=QueryResponse)
def execute_query(request: QueryRequest) -> QueryResponse:
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")

    try:
        records = neo4j_client.execute_read(request.cypher, request.parameters)
        return QueryResponse(status="success", results=records, count=len(records))
    except Exception as e:
        logger.error(f"Query execution failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/query/node/{label}/{name}", response_model=Dict[str, Any])
def get_node(label: str, name: str) -> Dict[str, Any]:
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")
    try:
        _validate_identifier(label, "label")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    node = neo4j_client.get_node(label, name)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node {label}/{name} not found")
    return node


@router.get("/query/neighbors/{label}/{name}", response_model=List[Dict[str, Any]])
def get_neighbors(
    label: str,
    name: str,
    relation_type: Optional[str] = Query(default=None),
    direction: str = Query(default="both"),
    limit: int = Query(default=50, le=200),
) -> List[Dict[str, Any]]:
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")
    try:
        _validate_identifier(label, "label")
        if relation_type:
            _validate_identifier(relation_type, "relation_type")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    graph_query = GraphQuery(neo4j_client)
    return graph_query.neighbors(label, name, relation_type, direction, limit)


@router.get("/query/shortest-path", response_model=List[Dict[str, Any]])
def shortest_path(
    source_label: str = Query(default=""),
    source_name: str = Query(...),
    target_label: str = Query(default=""),
    target_name: str = Query(...),
    max_depth: int = Query(default=5, le=10),
) -> List[Dict[str, Any]]:
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")
    try:
        if source_label:
            _validate_identifier(source_label, "source_label")
        if target_label:
            _validate_identifier(target_label, "target_label")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    graph_query = GraphQuery(neo4j_client)
    return graph_query.shortest_path(
        source_label, source_name, target_label, target_name, max_depth
    )


@router.get("/query/search", response_model=List[Dict[str, Any]])
def search_nodes(
    q: str = Query(..., description="搜索关键词"),
    limit: int = Query(default=20, le=100),
) -> List[Dict[str, Any]]:
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")
    graph_query = GraphQuery(neo4j_client)
    return graph_query.hybrid_search(q, limit)


@router.get("/query/stats", response_model=StatsResponse)
def get_statistics() -> StatsResponse:
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")
    graph_query = GraphQuery(neo4j_client)
    stats = graph_query.statistics()
    return StatsResponse(**stats)


@router.get("/timeline", response_model=List[Dict[str, Any]])
def get_timeline(
    label: str = Query(default="", description="实体类型过滤（可选）"),
    limit: int = Query(default=50, le=200),
):
    """返回带时间戳的关系时间线，用于时序分析。"""
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")

    if label:
        try:
            _validate_identifier(label, "label")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        label_filter = f"AND (a:`{label}` OR b:`{label}`)"
    else:
        label_filter = ""

    query = (
        "MATCH (a)-[r]->(b) "
        "WHERE r.valid_from IS NOT NULL "
        f"{label_filter} "
        "RETURN a.name AS source_name, labels(a) AS source_labels, "
        "b.name AS target_name, labels(b) AS target_labels, "
        "type(r) AS relation_type, r.valid_from AS valid_from, r.valid_to AS valid_to "
        "ORDER BY r.valid_from DESC "
        "LIMIT $limit"
    )
    try:
        records = neo4j_client.execute_query(query, {"limit": limit})
        return [dict(r) for r in records]
    except Exception as e:
        logger.error(f"Timeline query failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/timeline/{name}", response_model=List[Dict[str, Any]])
def get_entity_timeline(
    name: str,
    label: str = Query(default=""),
    limit: int = Query(default=30, le=100),
):
    """返回特定实体的历史时间线。"""
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")

    if label:
        try:
            _validate_identifier(label, "label")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        label_clause = f":`{label}`"
    else:
        label_clause = ""

    query = (
        f"MATCH (n{label_clause})-[r]-(m) "
        "WHERE n.name = $name AND (r.valid_from IS NOT NULL OR r.valid_to IS NOT NULL) "
        "RETURN n.name AS entity_name, labels(n) AS entity_labels, "
        "m.name AS related_name, labels(m) AS related_labels, "
        "type(r) AS relation_type, r.valid_from AS valid_from, r.valid_to AS valid_to, "
        "startNode(r).name AS start_name, endNode(r).name AS end_name "
        "ORDER BY r.valid_from DESC "
        "LIMIT $limit"
    )
    try:
        records = neo4j_client.execute_query(query, {"name": name, "limit": limit})
        return [dict(r) for r in records]
    except Exception as e:
        logger.error(f"Entity timeline query failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/query/node/{label}/{name}")
def delete_node(label: str, name: str):
    """删除指定实体节点及其所有关系"""
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")
    try:
        _validate_identifier(label, "label")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    count = neo4j_client.delete_node(label, name)
    return {"status": "deleted", "label": label, "name": name, "nodes_removed": count}
