from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from api.deps import neo4j_client, schema_manager
from quality.checker import QualityChecker

router = APIRouter()


@router.get("/quality", response_model=Dict[str, Any])
def quality_report():
    """Return a data-quality report for the knowledge graph (schema-driven)."""
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")

    checker = QualityChecker(neo4j_client, schema_manager)
    report = checker.run()
    return report.to_dict()


# ── Semantic reasoning ────────────────────────────────────


@router.get("/reasoner/rules")
async def get_reasoner_rules() -> Dict[str, Any]:
    """Return the ontology rules used for semantic reasoning."""
    from graph.reasoner import Reasoner, DEFAULT_RULES
    r = Reasoner(neo4j_client)
    return {"status": "success", "rules": DEFAULT_RULES, "inferable": r.get_inferable_relations()}


@router.post("/reasoner/run")
async def run_reasoner(dry_run: bool = Query(True, description="If true, only preview what would be inferred")):
    """Run the semantic reasoner to infer new knowledge."""
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")

    from graph.reasoner import Reasoner
    r = Reasoner(neo4j_client)
    stats = r.infer(dry_run=dry_run)
    return {
        "status": "success",
        "dry_run": dry_run,
        "stats": stats,
        "message": "Preview only — no changes made" if dry_run else "Inference applied"
    }
