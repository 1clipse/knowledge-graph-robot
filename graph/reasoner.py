"""Lightweight RDF/OWL semantic reasoning engine.

Supports:
- subClassOf: propagate instances to parent types
- Transitive properties: A --p--> B --p--> C  =>  A --p--> C
- Symmetric properties: A --p--> B  =>  B --p--> A  (bidirectional)
- Property inheritance: Subclasses inherit relations from superclasses
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from loguru import logger

from graph.client import Neo4jClient, _validate_identifier

# Default ontology rules for the industrial robot domain.
# Some defaults are intentionally broad fallback rules; production inference
# only runs a relation when schema endpoints are known.
DEFAULT_RULES: Dict[str, Any] = {
    "subclass_of": {
        "ServoMotor": "Component",
        "Reducer": "Component",
        "Controller": "Component",
        "Sensor": "Component",
        "EndEffector": "Component",
    },
    "transitive_relations": [
        "part_of",
    ],
    "symmetric_relations": [
        "competitor_of",
        "same_as",
    ],
}


class Reasoner:
    """Apply ontology rules to infer new knowledge in the graph."""

    def __init__(self, client: Neo4jClient, rules: Optional[Dict[str, Any]] = None):
        self._client = client
        self._rules = rules if rules is not None else _load_rules_from_schema()
        self._relation_endpoints = _load_relation_endpoints()

    def infer(self, dry_run: bool = True) -> Dict[str, int]:
        """Run all inference rules. Returns counts of inferred triples."""
        stats: Dict[str, int] = {}

        sym_count = self._infer_symmetric(dry_run)
        stats["symmetric_inferred"] = sym_count

        trans_count = self._infer_transitive(dry_run)
        stats["transitive_inferred"] = trans_count

        inv_count = self._infer_inverse(dry_run)
        stats["inverse_inferred"] = inv_count

        sub_count = self._infer_subclass(dry_run)
        stats["subclass_inferences"] = sub_count

        logger.info(f"Inference done (dry_run={dry_run}): {stats}")
        return stats

    def _endpoint_for_relation(self, rel: str) -> tuple[str, str] | None:
        return self._relation_endpoints.get(rel)

    @staticmethod
    def _validate_scoped_relation(rel: str, source_label: str, target_label: str) -> None:
        _validate_identifier(rel, "relation_type")
        _validate_identifier(source_label, "source_label")
        _validate_identifier(target_label, "target_label")

    def _infer_symmetric(self, dry_run: bool) -> int:
        count = 0
        for rel in self._rules.get("symmetric_relations", []):
            endpoint = self._endpoint_for_relation(rel)
            if not endpoint:
                logger.warning(f"Reasoner: skip symmetric relation without schema endpoint: {rel}")
                continue

            source_label, target_label = endpoint
            self._validate_scoped_relation(rel, source_label, target_label)

            query = f"""
                MATCH (a:`{source_label}`)-[r:`{rel}`]->(b:`{target_label}`)
                WHERE NOT (b:`{target_label}`)-[:`{rel}`]->(a:`{source_label}`)
                RETURN a.name AS source, b.name AS target
            """
            records = self._client.execute_query(query)
            for rec in records:
                if not dry_run:
                    self._client.execute_write(
                        f"MATCH (a:`{source_label}` {{name: $source}}), "
                        f"(b:`{target_label}` {{name: $target}}) "
                        f"MERGE (b)-[:`{rel}` {{inferred: true}}]->(a)",
                        {"source": rec["source"], "target": rec["target"]},
                    )
                count += 1
        return count

    def _infer_transitive(self, dry_run: bool) -> int:
        count = 0
        for rel in self._rules.get("transitive_relations", []):
            endpoint = self._endpoint_for_relation(rel)
            if not endpoint:
                logger.warning(f"Reasoner: skip transitive relation without schema endpoint: {rel}")
                continue

            source_label, target_label = endpoint
            self._validate_scoped_relation(rel, source_label, target_label)

            query = f"""
                MATCH (a:`{source_label}`)-[:`{rel}`]->(b:`{target_label}`)-[:`{rel}`]->(c:`{target_label}`)
                WHERE NOT (a)-[:`{rel}`]->(c) AND a <> c
                RETURN a.name AS source, b.name AS mid, c.name AS target
            """
            records = self._client.execute_query(query)
            for rec in records:
                if not dry_run:
                    self._client.execute_write(
                        f"MATCH (a:`{source_label}` {{name: $source}}), "
                        f"(c:`{target_label}` {{name: $target}}) "
                        f"MERGE (a)-[:`{rel}` {{inferred: true}}]->(c)",
                        {"source": rec["source"], "target": rec["target"]},
                    )
                count += 1
        return count

    def _infer_subclass(self, dry_run: bool) -> int:
        count = 0
        subclasses = self._rules.get("subclass_of", {})
        for child_label, parent_label in subclasses.items():
            _validate_identifier(child_label, "child_label")
            _validate_identifier(parent_label, "parent_label")

            query = f"""
                MATCH (n:`{child_label}`)
                WHERE NOT $parent IN labels(n)
                RETURN n.name AS name
            """
            records = self._client.execute_query(query, {"parent": parent_label})
            for rec in records:
                if not dry_run:
                    self._client.execute_write(
                        f"MATCH (n:`{child_label}` {{name: $name}}) "
                        f"SET n:`{parent_label}`",
                        {"name": rec["name"]},
                    )
                count += 1
        return count

    def _infer_inverse(self, dry_run: bool) -> int:
        count = 0
        inverse_pairs = self._rules.get("inverse_pairs", [])
        for forward, reverse in inverse_pairs:
            endpoint = self._endpoint_for_relation(forward)
            if not endpoint:
                logger.warning(f"Reasoner: skip inverse relation without schema endpoint: {forward}")
                continue

            source_label, target_label = endpoint
            self._validate_scoped_relation(forward, source_label, target_label)
            _validate_identifier(reverse, "reverse_relation_type")

            query = f"""
                MATCH (a:`{source_label}`)-[r:`{forward}`]->(b:`{target_label}`)
                WHERE NOT (b:`{target_label}`)-[:`{reverse}`]->(a:`{source_label}`)
                RETURN a.name AS source, b.name AS target
            """
            records = self._client.execute_query(query)
            for rec in records:
                if not dry_run:
                    self._client.execute_write(
                        f"MATCH (a:`{source_label}` {{name: $source}}), "
                        f"(b:`{target_label}` {{name: $target}}) "
                        f"MERGE (b)-[:`{reverse}` {{inferred: true}}]->(a)",
                        {"source": rec["source"], "target": rec["target"]},
                    )
                count += 1
        return count

    def get_inferable_relations(self) -> Dict[str, Any]:
        """Return what the reasoner can currently infer."""
        return {
            "subclass_of": self._rules.get("subclass_of", {}),
            "transitive_count": len(self._rules.get("transitive_relations", [])),
            "symmetric_count": len(self._rules.get("symmetric_relations", [])),
            "inverse_pair_count": len(self._rules.get("inverse_pairs", [])),
        }


def _load_rules_from_schema() -> Dict[str, Any]:
    """Load reasoning rules from the ontology schema, with fallback to defaults."""
    try:
        from schema.loader import get_semantics

        semantics = get_semantics()
        if semantics:
            return dict(semantics)
    except Exception:
        pass
    return dict(DEFAULT_RULES)


def _load_relation_endpoints() -> Dict[str, tuple[str, str]]:
    """Load relation source/target labels from schema."""
    try:
        from schema.loader import get_relation_types

        relation_types = get_relation_types()
        return {
            name: (rel.source, rel.target)
            for name, rel in relation_types.items()
        }
    except Exception as e:
        logger.warning(f"Failed to load relation endpoints for reasoner: {e}")
        return {}
