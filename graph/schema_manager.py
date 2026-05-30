from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from graph.client import Neo4jClient, _validate_identifier
from schema.loader import DomainSchema, EntityType, RelationType, load_schema


class SchemaManager:
    def __init__(self, client: Neo4jClient, schema: Optional[DomainSchema] = None) -> None:
        self._client = client
        self._schema = schema or load_schema()

    def create_constraints(self) -> None:
        for name, entity_type in self._schema.entity_types.items():
            _validate_identifier(name, "entity_type_name")
            try:
                query = f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:`{name}`) REQUIRE n.name IS UNIQUE"
                self._client.execute_write(query)
                logger.info(f"Created unique constraint for {name}")
            except Exception as e:
                logger.warning(f"Constraint creation for {name} failed (may already exist): {e}")

    def create_fulltext_index(self) -> None:
        labels = list(self._schema.entity_types.keys())
        if not labels:
            return
        for lbl in labels:
            _validate_identifier(lbl, "label")
        label_pattern = " | ".join(f"`{lbl}`" for lbl in labels)
        try:
            query = (
                f"CREATE FULLTEXT INDEX entity_search IF NOT EXISTS "
                f"FOR (n:{label_pattern}) ON EACH [n.name, n.description]"
            )
            self._client.execute_write(query)
            logger.info("Created fulltext index 'entity_search'")
        except Exception as e:
            logger.warning(f"Fulltext index creation failed (may already exist): {e}")

    def create_property_indexes(self) -> None:
        """Create indexes on frequently filtered properties per entity type."""
        node_props = ["file", "_confidence", "_source"]
        rel_props = ["valid_from", "valid_to", "_confidence"]

        for label in self._schema.entity_types:
            _validate_identifier(label, "label")
            for prop in node_props:
                try:
                    query = f"CREATE INDEX IF NOT EXISTS FOR (n:`{label}`) ON (n.{prop})"
                    self._client.execute_write(query)
                    logger.debug(f"Ensured index on (`{label}`).{prop}")
                except Exception as e:
                    logger.warning(f"Index on (`{label}`).{prop} skipped: {e}")

        for rel_type in self._schema.relation_types:
            _validate_identifier(rel_type, "rel_type")
            for prop in rel_props:
                try:
                    query = f"CREATE INDEX IF NOT EXISTS FOR ()-[r:`{rel_type}`]-() ON (r.{prop})"
                    self._client.execute_write(query)
                    logger.debug(f"Ensured index on [`{rel_type}`].{prop}")
                except Exception as e:
                    logger.warning(f"Index on [`{rel_type}`].{prop} skipped: {e}")

    def create_vector_index(self) -> None:
        """Create native vector index on Entity._embedding for ANN search (Neo4j 5.15+)."""
        try:
            query = (
                "CREATE VECTOR INDEX entity_embeddings IF NOT EXISTS "
                "FOR (n:Entity) ON (n._embedding) "
                "OPTIONS {indexConfig: {"
                "  `vector.dimensions`: 1024,"
                "  `vector.similarity_function`: 'COSINE'"
                "}}"
            )
            self._client.execute_write(query)
            logger.info("Created vector index 'entity_embeddings' on Entity._embedding")
        except Exception as e:
            logger.warning(f"Vector index creation failed (may not support vectors): {e}")

    def initialize_schema(self) -> None:
        logger.info("Initializing graph database schema...")
        self.create_constraints()
        self.create_fulltext_index()
        # Vector index is created on-demand via create_vector_index() when needed
        logger.info("Schema initialization complete")

    def get_existing_labels(self) -> List[str]:
        try:
            records = self._client.execute_query("CALL db.labels() YIELD label RETURN label")
            return [r["label"] for r in records]
        except Exception as e:
            logger.error(f"Failed to get labels: {e}")
            return []

    def get_existing_relation_types(self) -> List[str]:
        try:
            records = self._client.execute_query(
                "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType"
            )
            return [r["relationshipType"] for r in records]
        except Exception as e:
            logger.error(f"Failed to get relation types: {e}")
            return []

    def validate_entity_type(self, entity_type: str) -> bool:
        return entity_type in self._schema.entity_types

    def validate_relation_type(self, relation_type: str) -> bool:
        return relation_type in self._schema.relation_types

    def validate_relation(
        self, relation_type: str, source_type: str, target_type: str
    ) -> bool:
        rel_def = self._schema.relation_types.get(relation_type)
        if not rel_def:
            return False
        return rel_def.source == source_type and rel_def.target == target_type

    def get_entity_schema(self, entity_type: str) -> Optional[EntityType]:
        return self._schema.entity_types.get(entity_type)

    def get_relation_schema(self, relation_type: str) -> Optional[RelationType]:
        return self._schema.relation_types.get(relation_type)

    def get_ui_metadata(self) -> Dict[str, Any]:
        """Return schema metadata used by the local graph UI renderer."""
        return {
            "domain": self._schema.domain,
            "version": self._schema.version,
            "entity_types": {
                name: {
                    "label_zh": entity.label_zh,
                    "label_en": entity.label_en,
                    "description": entity.description,
                }
                for name, entity in self._schema.entity_types.items()
            },
            "relation_types": {
                name: {
                    "label_zh": relation.label_zh,
                    "label_en": relation.label_en,
                    "source": relation.source,
                    "target": relation.target,
                    "description": relation.description,
                }
                for name, relation in self._schema.relation_types.items()
            },
        }

    def drop_all_data(self) -> None:
        logger.warning(f"Dropping all data for domain {self._domain}!")
        self._client.execute_write("MATCH (n) WHERE n._domain = $_domain DETACH DELETE n", {"_domain": self._domain})

    def drop_constraints(self) -> None:
        try:
            records = self._client.execute_query("SHOW CONSTRAINTS")
            for record in records:
                name = record.get("name", "")
                if name:
                    _validate_identifier(name, "constraint_name")
                    self._client.execute_write(f"DROP CONSTRAINT `{name}`")
            logger.info("All constraints dropped")
        except Exception as e:
            logger.warning(f"Failed to drop constraints: {e}")

    def load_sample_data(self) -> None:
        schema = load_schema()
        sample = _get_sample_data()
        if not sample:
            return

        for entity_data in sample.get("entities", []):
            entity_type = entity_data["type"]
            props = entity_data["properties"]
            if not self.validate_entity_type(entity_type):
                logger.warning(f"Skipping unknown entity type: {entity_type}")
                continue
            self._client.create_node(entity_type, props, merge=True)
            logger.info(f"Created sample entity: {entity_type}/{props.get('name', '')}")

        for rel_data in sample.get("relations", []):
            rel_type = rel_data["type"]
            source = rel_data["source"]
            target = rel_data["target"]
            props = rel_data.get("properties", {})
            if not self.validate_relation_type(rel_type):
                logger.warning(f"Skipping unknown relation type: {rel_type}")
                continue
            self._client.create_relation(
                source_label=source["type"],
                source_name=source["name"],
                target_label=target["type"],
                target_name=target["name"],
                relation_type=rel_type,
                properties=props,
            )
            logger.info(f"Created sample relation: {rel_type}")

        logger.info("Sample data loaded successfully")


def _get_sample_data() -> Dict[str, Any]:
    try:
        import yaml
        from pathlib import Path

        schema_path = Path(__file__).resolve().parent.parent / "schema" / "industrial_robot.yaml"
        with open(schema_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("sample_data", {})
    except Exception as e:
        logger.error(f"Failed to load sample data: {e}")
        return {}
