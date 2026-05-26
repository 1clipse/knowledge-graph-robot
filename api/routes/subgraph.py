from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from api.deps import neo4j_client
from graph.query import GraphQuery

router = APIRouter()


def _primary_label(labels: List[str]) -> str:
    """Return the primary domain label, skipping the internal 'Entity' base label."""
    for l in labels:
        if l != "Entity":
            return l
    return labels[0] if labels else ""


@router.get("/subgraph/search/{keyword}", response_model=Dict[str, Any])
def search_subgraph(
    keyword: str,
    depth: int = Query(default=1, le=3),
    limit: int = Query(default=100, le=300),
) -> Dict[str, Any]:
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")

    graph_query = GraphQuery(neo4j_client)
    search_results = graph_query.hybrid_search(keyword, top_k=5)

    if not search_results:
        return {"nodes": [], "edges": [], "message": "No matching entities found"}

    all_nodes: Dict[str, Dict[str, Any]] = {}
    all_edges: List[Dict[str, Any]] = []

    for item in search_results[:3]:
        node = item.get("node", {})
        labels = item.get("labels", [])
        if not labels or "name" not in node:
            continue
        try:
            sub = graph_query.subgraph(_primary_label(labels), node["name"], depth, limit)
            for n in sub.get("nodes", []):
                node_id = n.get("id", "")
                if node_id and node_id not in all_nodes:
                    # Filter out Entity from displayed labels
                    n["labels"] = [l for l in n.get("labels", []) if l != "Entity"]
                    all_nodes[node_id] = n
            all_edges.extend(sub.get("edges", []))
        except Exception as e:
            logger.warning(f"Subgraph extraction for {node.get('name', '')} failed: {e}")

    seen_edges = set()
    unique_edges = []
    for edge in all_edges:
        edge_key = f"{edge.get('source', '')}-{edge.get('type', '')}-{edge.get('target', '')}"
        if edge_key not in seen_edges:
            seen_edges.add(edge_key)
            unique_edges.append(edge)

    return {"nodes": list(all_nodes.values()), "edges": unique_edges}


@router.get("/subgraph/{label}/{name}", response_model=Dict[str, Any])
def get_subgraph(
    label: str,
    name: str,
    depth: int = Query(default=2, le=5),
    limit: int = Query(default=200, le=500),
) -> Dict[str, Any]:
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")

    graph_query = GraphQuery(neo4j_client)
    try:
        result = graph_query.subgraph(label, name, depth, limit)
        # Filter out Entity from displayed labels
        for n in result.get("nodes", []):
            n["labels"] = [l for l in n.get("labels", []) if l != "Entity"]
        return result
    except Exception as e:
        logger.error(f"Subgraph extraction failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
