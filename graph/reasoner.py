"""Lightweight RDF/OWL semantic reasoning engine.

Supports:
- subClassOf: propagate instances to parent types
- Transitive properties: A --p--> B --p--> C  =>  A --p--> C
- Symmetric properties: A --p--> B  =>  B --p--> A  (bidirectional)
- Property inheritance: Subclasses inherit relations from superclasses
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger

from graph.client import Neo4jClient

# Default ontology rules for the industrial robot domain
DEFAULT_RULES: Dict[str, Any] = {
    "subclass_of": {
        # Child class inherits all properties from parent
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
        self._rules = rules or DEFAULT_RULES

    def infer(self, dry_run: bool = True) -> Dict[str, int]:
        """Run all inference rules. Returns counts of inferred triples."""
        stats: Dict[str, int] = {}

        # 1. Symmetric relations: add reverse edges
        sym_count = self._infer_symmetric(dry_run)
        stats["symmetric_inferred"] = sym_count

        # 2. Transitive relations: add shortcuts
        trans_count = self._infer_transitive(dry_run)
        stats["transitive_inferred"] = trans_count

        # 3. SubClassOf: add extra labels to nodes
        sub_count = self._infer_subclass(dry_run)
        stats["subclass_inferences"] = sub_count

        logger.info(f"Inference done (dry_run={dry_run}): {stats}")
        return stats

    def _infer_symmetric(self, dry_run: bool) -> int:
        count = 0
        for rel in self._rules.get("symmetric_relations", []):
            query = f"""
                MATCH (a)-[r:`{rel}`]->(b)
                WHERE NOT (b)-[:`{rel}`]->(a)
                RETURN a.name AS source, b.name AS target
            """
            records = self._client.execute_query(query)
            for rec in records:
                if not dry_run:
                    self._client.execute_write(
                        f"MATCH (a {{name: $source}}), (b {{name: $target}}) "
                        f"MERGE (b)-[:`{rel}`]->(a)",
                        {"source": rec["target"], "target": rec["source"]},
                    )
                count += 1
        return count

    def _infer_transitive(self, dry_run: bool) -> int:
        count = 0
        for rel in self._rules.get("transitive_relations", []):
            # Find paths: A --p--> B --p--> C where A --p--> C is missing
            query = f"""
                MATCH (a)-[:`{rel}`]->(b)-[:`{rel}`]->(c)
                WHERE NOT (a)-[:`{rel}`]->(c) AND a <> c
                RETURN a.name AS source, b.name AS mid, c.name AS target
            """
            records = self._client.execute_query(query)
            for rec in records:
                if not dry_run:
                    self._client.execute_write(
                        f"MATCH (a {{name: $source}}), (c {{name: $target}}) "
                        f"MERGE (a)-[:`{rel}` {{inferred: true}}]->(c)",
                        {"source": rec["source"], "target": rec["target"]},
                    )
                count += 1
        return count

    def _infer_subclass(self, dry_run: bool) -> int:
        count = 0
        subclasses = self._rules.get("subclass_of", {})
        for child_label, parent_label in subclasses.items():
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

    def get_inferable_relations(self) -> Dict[str, Any]:
        """Return what the reasoner can currently infer."""
        return {
            "subclass_of": self._rules["subclass_of"],
            "transitive_count": len(self._rules.get("transitive_relations", [])),
            "symmetric_count": len(self._rules.get("symmetric_relations", [])),
        }
