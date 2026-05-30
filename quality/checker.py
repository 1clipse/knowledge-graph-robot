"""Schema-driven data quality checker for the knowledge graph.

Quality rules are derived from the ontology schema (industrial_robot.yaml)
rather than hardcoded, so that schema changes automatically update checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

from graph.client import Neo4jClient
from graph.schema_manager import SchemaManager
from schema.loader import DomainSchema, active_domain_key, load_schema


@dataclass
class QualityReport:
    quality_score: float = 100.0
    completeness: List[Dict[str, Any]] = field(default_factory=list)
    consistency: List[Dict[str, Any]] = field(default_factory=list)
    duplicates: List[Dict[str, Any]] = field(default_factory=list)
    confidence: List[Dict[str, Any]] = field(default_factory=list)
    graph_structure: List[Dict[str, Any]] = field(default_factory=list)
    temporal: List[Dict[str, Any]] = field(default_factory=list)
    suggested_actions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "quality_score": self.quality_score,
            "sections": {
                "completeness": self.completeness,
                "consistency": self.consistency,
                "duplicates": self.duplicates,
                "confidence": self.confidence,
                "graph_structure": self.graph_structure,
                "temporal": self.temporal,
            },
            "suggested_actions": self.suggested_actions,
        }


class QualityChecker:
    """Run data quality checks against the knowledge graph.

    Derives check parameters from the loaded DomainSchema so that
    adding/extending entity types or relations automatically adjusts
    the quality rules.
    """

    def __init__(
        self,
        client: Neo4jClient,
        schema_manager: Optional[SchemaManager] = None,
        schema: Optional[DomainSchema] = None,
        domain: Optional[str] = None,
    ) -> None:
        self._client = client
        self._schema_manager = schema_manager
        self._schema = schema or load_schema()
        self._domain = domain or active_domain_key()

    def run(self) -> QualityReport:
        report = QualityReport()

        # 1. Completeness: entities missing recommended properties
        report.completeness = self._check_completeness()

        # 2. Consistency: schema violations (invalid endpoints, unknown types)
        report.consistency = self._check_consistency()

        # 3. Duplicates: potential duplicate entities
        report.duplicates = self._check_duplicates()

        # 4. Confidence: low-confidence facts
        report.confidence = self._check_confidence()

        # 5. Graph structure: orphans, isolated components
        report.graph_structure = self._check_graph_structure()

        # 6. Temporal: expired or missing time ranges
        report.temporal = self._check_temporal()

        # Compute score
        report.quality_score = self._compute_score(report)
        report.suggested_actions = self._build_actions(report)

        return report

    # ── Completeness ─────────────────────────────────────────────

    def _check_completeness(self) -> List[Dict[str, Any]]:
        """Check entities for missing recommended properties from schema."""
        results: List[Dict[str, Any]] = []
        for etype, entity_def in self._schema.entity_types.items():
            # Collect recommended (not required) properties
            recommended = [
                pname for pname, pdef in entity_def.properties.items()
                if not pdef.required and pname != "name"
            ]
            if not recommended:
                continue

            # Check: at least one recommended prop should be present
            # Entities missing ALL recommended props are flagged
            for prop in recommended[:3]:  # limit to top 3 to keep queries fast
                try:
                    query = (
                        f"MATCH (n:`{etype}`) "
                        f"WHERE n._domain = $_domain AND n.{prop} IS NULL "
                        f"RETURN n.name AS name, labels(n) AS labels, "
                        f"'{prop}' AS missing_prop LIMIT 20"
                    )
                    records = self._client.execute_query(query, {"_domain": self._domain})
                    for r in records:
                        results.append({
                            "entity": r["name"],
                            "type": etype,
                            "missing_prop": r["missing_prop"],
                            "section": "completeness",
                        })
                except Exception as e:
                    logger.debug(f"Completeness check for {etype}.{prop} skipped: {e}")

        return results

    # ── Consistency ──────────────────────────────────────────────

    def _check_consistency(self) -> List[Dict[str, Any]]:
        """Check relation endpoints against schema definitions."""
        results: List[Dict[str, Any]] = []

        for rel_type, rel_def in self._schema.relation_types.items():
            expected_src = rel_def.source
            expected_tgt = rel_def.target
            try:
                query = (
                    f"MATCH (s)-[r:`{rel_type}`]->(t) "
                    f"WHERE s._domain = $_domain AND t._domain = $_domain AND r._domain = $_domain "
                    f"AND (NOT $expected_src IN labels(s) OR NOT $expected_tgt IN labels(t)) "
                    f"RETURN s.name AS source, labels(s) AS source_labels, "
                    f"t.name AS target, labels(t) AS target_labels, "
                    f"'{rel_type}' AS relation_type "
                    f"LIMIT 20"
                )
                records = self._client.execute_query(
                    query, {"expected_src": expected_src, "expected_tgt": expected_tgt, "_domain": self._domain}
                )
                for r in records:
                    results.append({
                        "source": r["source"],
                        "source_labels": r["source_labels"],
                        "target": r["target"],
                        "target_labels": r["target_labels"],
                        "relation_type": r["relation_type"],
                        "expected": f"{expected_src} -> {expected_tgt}",
                        "section": "consistency",
                    })
            except Exception as e:
                logger.debug(f"Consistency check for {rel_type} skipped: {e}")

        return results

    # ── Duplicates ───────────────────────────────────────────────

    def _check_duplicates(self) -> List[Dict[str, Any]]:
        """Find potential duplicate entities."""
        results: List[Dict[str, Any]] = []

        # Same name, different labels
        try:
            query = (
                "MATCH (n) WHERE n.name IS NOT NULL AND n._domain = $_domain "
                "WITH n.name AS name, collect(DISTINCT labels(n)[0]) AS type_list, count(n) AS cnt "
                "WHERE cnt > 1 OR size(type_list) > 1 "
                "RETURN name, type_list, cnt "
                "ORDER BY cnt DESC LIMIT 30"
            )
            records = self._client.execute_query(query, {"_domain": self._domain})
            for r in records:
                results.append({
                    "name": r["name"],
                    "types": r["type_list"],
                    "count": r["cnt"],
                    "reason": "same_name_different_labels" if len(r["type_list"]) > 1 else "multiple_nodes_same_label",
                    "section": "duplicates",
                })
        except Exception as e:
            logger.error(f"Duplicate check failed: {e}")

        return results

    # ── Confidence ───────────────────────────────────────────────

    def _check_confidence(self) -> List[Dict[str, Any]]:
        """Find low-confidence entities and relations."""
        results: List[Dict[str, Any]] = []
        try:
            query = (
                "MATCH (n) WHERE n._domain = $_domain AND n._confidence IS NOT NULL AND n._confidence < 0.5 "
                "RETURN labels(n) AS labels, n.name AS name, n._confidence AS confidence "
                "ORDER BY n._confidence ASC LIMIT 30"
            )
            records = self._client.execute_query(query, {"_domain": self._domain})
            for r in records:
                results.append({
                    "entity": r["name"],
                    "labels": r["labels"],
                    "confidence": r["confidence"],
                    "section": "confidence",
                })
        except Exception:
            pass
        return results

    # ── Graph structure ──────────────────────────────────────────

    def _check_graph_structure(self) -> List[Dict[str, Any]]:
        """Find orphan nodes and structural issues."""
        results: List[Dict[str, Any]] = []
        try:
            query = (
                "MATCH (n) WHERE n._domain = $_domain AND NOT (n)--() AND NOT n:IngestLog "
                "RETURN labels(n) AS labels, n.name AS name "
                "LIMIT 50"
            )
            records = self._client.execute_query(query, {"_domain": self._domain})
            for r in records:
                results.append({
                    "entity": r["name"],
                    "labels": r["labels"],
                    "issue": "orphan",
                    "section": "graph_structure",
                })
        except Exception as e:
            logger.error(f"Orphan check failed: {e}")

        return results

    # ── Temporal ─────────────────────────────────────────────────

    def _check_temporal(self) -> List[Dict[str, Any]]:
        """Check for missing or conflicting temporal data."""
        results: List[Dict[str, Any]] = []
        try:
            query = (
                "MATCH ()-[r]->() WHERE r._domain = $_domain AND r.valid_from IS NOT NULL AND r.valid_to IS NOT NULL "
                "AND r.valid_from > r.valid_to "
                "RETURN type(r) AS rel_type, r.valid_from AS valid_from, r.valid_to AS valid_to "
                "LIMIT 20"
            )
            records = self._client.execute_query(query, {"_domain": self._domain})
            for r in records:
                results.append({
                    "relation_type": r["rel_type"],
                    "valid_from": r["valid_from"],
                    "valid_to": r["valid_to"],
                    "issue": "conflicting_valid_range",
                    "section": "temporal",
                })
        except Exception:
            pass
        return results

    # ── Scoring ──────────────────────────────────────────────────

    def _compute_score(self, report: QualityReport) -> float:
        total_issues = (
            len(report.completeness)
            + len(report.consistency)
            + len(report.duplicates)
            + len(report.confidence)
            + len(report.graph_structure)
            + len(report.temporal)
        )
        try:
            records = self._client.execute_query("MATCH (n) WHERE n._domain = $_domain RETURN count(n) AS total", {"_domain": self._domain})
            total_entities = records[0]["total"] if records else 0
            penalty = min(total_issues * 2, 100) if total_entities > 0 else 0
            return max(0, 100 - penalty)
        except Exception:
            return max(0, 100 - min(total_issues * 2, 100))

    def _build_actions(self, report: QualityReport) -> List[str]:
        actions: List[str] = []
        if report.duplicates:
            actions.append(f"审查 {len(report.duplicates)} 个疑似重复实体，考虑合并或标记 SAME_AS")
        if report.completeness:
            actions.append(f"{len(report.completeness)} 个实体缺少推荐属性，建议补充数据")
        if report.consistency:
            actions.append(f"{len(report.consistency)} 条关系端点与 Schema 不匹配，建议检查数据源")
        if report.confidence:
            actions.append(f"{len(report.confidence)} 条低置信度事实，考虑人工审核或删除")
        if report.graph_structure:
            actions.append(f"{len(report.graph_structure)} 个孤立节点，考虑建立关系或清理")
        if report.temporal:
            actions.append(f"{len(report.temporal)} 条时间范围冲突，建议修正 valid_from/valid_to")
        if not actions:
            actions.append("图谱质量良好，无需操作")
        return actions
