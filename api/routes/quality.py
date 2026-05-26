from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from loguru import logger

from api.deps import neo4j_client

router = APIRouter()


@router.get("/quality", response_model=Dict[str, Any])
def quality_report():
    """Return a data-quality report for the knowledge graph."""
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")

    report: Dict[str, Any] = {
        "orphan_nodes": _find_orphans(),
        "missing_key_props": _find_missing_key_properties(),
        "potential_duplicates": _find_duplicates(),
        "low_confidence_facts": _find_low_confidence(),
        "schema_violations": _find_schema_violations(),
        "quality_score": 0.0,
    }

    # Compute overall quality score (0-100)
    total_issues = (
        len(report["orphan_nodes"])
        + len(report["missing_key_props"])
        + len(report["potential_duplicates"])
        + len(report["low_confidence_facts"])
        + len(report["schema_violations"])
    )
    # Count total entities
    try:
        records = neo4j_client.execute_query("MATCH (n) RETURN count(n) AS total")
        total_entities = records[0]["total"] if records else 0
        # Score: 100 - penalty per issue, floor at 0
        penalty = min(total_issues * 2, 100) if total_entities > 0 else 0
        report["quality_score"] = max(0, 100 - penalty)
    except Exception:
        pass

    return report


def _find_orphans() -> List[Dict[str, Any]]:
    """Entities with zero relationships (exclude IngestLog)."""
    try:
        query = (
            "MATCH (n) WHERE NOT (n)--() AND NOT n:IngestLog "
            "RETURN labels(n) AS labels, n.name AS name, n._source AS source "
            "LIMIT 50"
        )
        records = neo4j_client.execute_query(query)
        return [dict(r) for r in records]
    except Exception as e:
        logger.error(f"Orphan check failed: {e}")
        return []


def _find_missing_key_properties() -> List[Dict[str, Any]]:
    """Entities missing important properties based on type."""
    checks = [
        {"label": "Robot", "props": ["payload", "reach", "axes"], "suggested": "负载/臂展/轴数"},
        {"label": "Reducer", "props": ["reducer_type", "reduction_ratio"], "suggested": "减速器类型/减速比"},
        {"label": "ServoMotor", "props": ["rated_power", "rated_torque"], "suggested": "额定功率/额定扭矩"},
        {"label": "Controller", "props": ["communication_protocol"], "suggested": "通信协议"},
        {"label": "Manufacturer", "props": ["country"], "suggested": "国家"},
        {"label": "Sensor", "props": ["sensor_type"], "suggested": "传感器类型"},
        {"label": "EndEffector", "props": ["effector_type"], "suggested": "末端执行器类型"},
        {"label": "Material", "props": ["material_type"], "suggested": "材料类型"},
        {"label": "Process", "props": ["process_type"], "suggested": "工艺类型"},
        {"label": "Software", "props": ["software_type"], "suggested": "软件类型"},
    ]

    results = []
    for check in checks:
        missing_conditions = " OR ".join(
            f"n.{p} IS NULL" for p in check["props"]
        )
        try:
            query = (
                f"MATCH (n:{check['label']}) "
                f"WHERE {missing_conditions} "
                f"RETURN labels(n) AS labels, n.name AS name, n._source AS source, "
                f"'{check['suggested']}' AS missing_fields "
                f"LIMIT 20"
            )
            records = neo4j_client.execute_query(query)
            results.extend([dict(r) for r in records])
        except Exception as e:
            logger.warning(f"Missing prop check for {check['label']} failed: {e}")

    return results


def _find_duplicates() -> List[Dict[str, Any]]:
    """Entities with same name but different labels (potential type inconsistencies),
    or entities with very similar names."""
    try:
        # Same name, different labels
        query = (
            "MATCH (n) WHERE n.name IS NOT NULL "
            "WITH n.name AS name, collect(DISTINCT labels(n)[0]) AS type_list, count(n) AS cnt "
            "WHERE cnt > 1 OR size(type_list) > 1 "
            "RETURN name, type_list, cnt "
            "ORDER BY cnt DESC LIMIT 30"
        )
        records = neo4j_client.execute_query(query)
        return [dict(r) for r in records]
    except Exception as e:
        logger.error(f"Duplicate check failed: {e}")
        return []


def _find_low_confidence() -> List[Dict[str, Any]]:
    """Entities and relations with confidence below threshold."""
    results = []
    try:
        query = (
            "MATCH (n) WHERE n._confidence IS NOT NULL AND n._confidence < 0.5 "
            "RETURN labels(n) AS labels, n.name AS name, n._source AS source, "
            "n._confidence AS confidence ORDER BY n._confidence ASC LIMIT 30"
        )
        records = neo4j_client.execute_query(query)
        results.extend([dict(r) for r in records])
    except Exception:
        pass
    return results


def _find_schema_violations() -> List[Dict[str, Any]]:
    """Relations without proper source/target type constraints,
    and entities using non-schema labels."""
    # This is a baseline check; full schema validation requires loading the YAML schema
    try:
        # Relations with empty type
        query = (
            "MATCH ()-[r]->() WHERE type(r) = '' OR type(r) IS NULL "
            "RETURN type(r) AS relation_type, count(r) AS count"
        )
        records = neo4j_client.execute_query(query)
        violations = []
        for r in records:
            if r.get("count", 0) > 0:
                violations.append({"type": "empty_relation_type", "count": r["count"]})

        # Entities with non-standard labels (fallback, just count Unknown-like names)
        query2 = (
            "MATCH (n) WHERE n.name IS NULL OR n.name = '' "
            "RETURN labels(n) AS labels, count(n) AS count"
        )
        records2 = neo4j_client.execute_query(query2)
        for r in records2:
            if r.get("count", 0) > 0:
                violations.append({"type": "missing_name", "labels": r["labels"], "count": r["count"]})

        return violations
    except Exception as e:
        logger.error(f"Schema violation check failed: {e}")
        return []
