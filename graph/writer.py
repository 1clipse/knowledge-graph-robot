from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

from extractors.llm_extractor import ExtractionResult
from graph.client import Neo4jClient, _validate_identifier
from graph.entity_resolver import EntityResolver
from graph.schema_manager import SchemaManager
from schema.loader import active_domain_key


@dataclass
class WriteSummary:
    entities_received: int = 0
    entities_written: int = 0
    entities_skipped: int = 0
    relations_received: int = 0
    relations_written: int = 0
    relations_skipped: int = 0
    validation_errors: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def total_written(self) -> int:
        return self.entities_written + self.relations_written


class GraphWriter:
    """Centralized Neo4j write module with schema validation.

    All ingest paths (API, pipeline, scripts) should route through this
    single module so that entity/relation validation, embedding generation,
    and provenance stamping are applied uniformly.
    """

    def __init__(
        self,
        neo4j_client: Neo4jClient,
        schema_manager: Optional[SchemaManager] = None,
        entity_resolver: Optional[EntityResolver] = None,
        domain: Optional[str] = None,
    ) -> None:
        self._client = neo4j_client
        self._schema_manager = schema_manager
        self._entity_resolver = entity_resolver or EntityResolver()
        self._domain = domain or active_domain_key()

    def write(self, result: ExtractionResult) -> WriteSummary:
        summary = WriteSummary(
            entities_received=len(result.entities),
            relations_received=len(result.relations),
        )

        if not result.entities:
            return summary

        # ── Phase 0: Normalize entity names via EntityResolver ───
        if self._entity_resolver:
            self._entity_resolver.resolve_result(result)

        # ── Phase 1: Validate & filter entities ──────────────────
        valid_entities, entity_errors = self._validate_entities(result.entities)
        summary.entities_skipped = summary.entities_received - len(valid_entities)
        summary.validation_errors.extend(entity_errors)

        if not valid_entities:
            return summary

        # ── Phase 2: Batch embed ─────────────────────────────────
        embeddings_map = self._generate_embeddings(valid_entities)

        # ── Phase 3: Build & execute entity queries ──────────────
        entity_queries = self._build_entity_queries(valid_entities, embeddings_map)
        if entity_queries:
            self._client.execute_write_batch(entity_queries)
            summary.entities_written = len(entity_queries)

        # ── Phase 4: Validate & filter relations ─────────────────
        valid_relations, relation_errors = self._validate_relations(result.relations)
        summary.relations_skipped = summary.relations_received - len(valid_relations)
        summary.validation_errors.extend(relation_errors)

        # ── Phase 5: Build & execute relation queries ────────────
        relation_queries = self._build_relation_queries(valid_relations)
        if relation_queries:
            self._client.execute_write_batch(relation_queries)
            summary.relations_written = len(relation_queries)

        logger.info(
            f"GraphWriter: entities {summary.entities_written}/{summary.entities_received}, "
            f"relations {summary.relations_written}/{summary.relations_received}, "
            f"errors {len(summary.validation_errors)}"
        )
        return summary

    # ── validation helpers ──────────────────────────────────────

    def _validate_entities(
        self, entities: List
    ) -> tuple[List, List[Dict[str, Any]]]:
        valid: List = []
        errors: List[Dict[str, Any]] = []
        for entity in entities:
            if self._schema_manager and not self._schema_manager.validate_entity_type(entity.type):
                msg = f"Unknown entity type '{entity.type}' for '{entity.name}'"
                logger.warning(f"GraphWriter: {msg}")
                errors.append({"entity": entity.name, "type": entity.type, "reason": msg})
                continue
            valid.append(entity)
        return valid, errors

    def _validate_relations(
        self, relations: List
    ) -> tuple[List, List[Dict[str, Any]]]:
        valid: List = []
        errors: List[Dict[str, Any]] = []
        for rel in relations:
            if self._schema_manager:
                if not self._schema_manager.validate_relation_type(rel.relation_type):
                    msg = f"Unknown relation type '{rel.relation_type}' ({rel.source.name} -> {rel.target.name})"
                    logger.warning(f"GraphWriter: {msg}")
                    errors.append({
                        "relation": rel.relation_type,
                        "source": f"{rel.source.type}:{rel.source.name}",
                        "target": f"{rel.target.type}:{rel.target.name}",
                        "reason": msg,
                    })
                    continue
                if not self._schema_manager.validate_relation(
                    rel.relation_type, rel.source.type, rel.target.type
                ):
                    rel_def = self._schema_manager.get_relation_schema(rel.relation_type)
                    expected = f"{rel_def.source} -> {rel_def.target}" if rel_def else "?"
                    msg = (
                        f"Relation '{rel.relation_type}' endpoint mismatch: "
                        f"got {rel.source.type} -> {rel.target.type}, "
                        f"expected {expected}"
                    )
                    logger.warning(f"GraphWriter: {msg}")
                    errors.append({
                        "relation": rel.relation_type,
                        "source": f"{rel.source.type}:{rel.source.name}",
                        "target": f"{rel.target.type}:{rel.target.name}",
                        "reason": msg,
                    })
                    continue
            valid.append(rel)
        return valid, errors

    # ── embedding ───────────────────────────────────────────────

    def _generate_embeddings(self, entities: List) -> Dict[int, List[float]]:
        embed_texts: List[str] = []
        for entity in entities:
            embed_texts.append(
                f"{entity.type} {entity.name} {entity.properties.get('description', '')}"
            )

        if not embed_texts:
            return {}

        try:
            from graph.embeddings import embed_texts as batch_embed
            emb_list = batch_embed(embed_texts)
            return {i: emb for i, emb in enumerate(emb_list)}
        except Exception as e:
            logger.warning(f"GraphWriter: batch embedding failed, writing without embeddings: {e}")
            return {}

    # ── query builders ──────────────────────────────────────────

    def _build_entity_queries(
        self, entities: List, embeddings_map: Dict[int, List[float]]
    ) -> List[tuple]:
        queries: List[tuple] = []
        for i, entity in enumerate(entities):
            props = {k: v for k, v in entity.properties.items() if v is not None}
            props["name"] = entity.name
            if entity.source:
                props["_source"] = entity.source
                props["file"] = entity.source
            props["_confidence"] = entity.confidence
            props["_domain"] = self._domain
            if entity.valid_from:
                props["valid_from"] = entity.valid_from
            if entity.valid_to:
                props["valid_to"] = entity.valid_to
            if i in embeddings_map:
                props["_embedding"] = embeddings_map[i]

            _validate_identifier(entity.type, "entity_type")
            prop_assignments = ", ".join(f"n.{k} = ${k}" for k in props.keys())
            query = f"MERGE (n:`{entity.type}` {{name: $name, _domain: $_domain}}) SET {prop_assignments}"
            queries.append((query, props))
        return queries

    def _build_relation_queries(self, relations: List) -> List[tuple]:
        queries: List[tuple] = []
        for rel in relations:
            rel_props = {k: v for k, v in rel.properties.items() if v is not None}
            if rel.source_ref:
                rel_props["_source"] = rel.source_ref
            rel_props["_confidence"] = rel.confidence
            rel_props["_domain"] = self._domain
            if rel.valid_from:
                rel_props["valid_from"] = rel.valid_from
            if rel.valid_to:
                rel_props["valid_to"] = rel.valid_to

            _validate_identifier(rel.source.type, "source_type")
            _validate_identifier(rel.target.type, "target_type")
            _validate_identifier(rel.relation_type, "relation_type")
            params: Dict[str, Any] = {
                "source_name": rel.source.name,
                "target_name": rel.target.name,
                "_domain": self._domain,
            }
            set_clause = ""
            if rel_props:
                set_clause = " SET " + ", ".join(f"r.{k} = ${k}" for k in rel_props.keys())
                params.update(rel_props)
            query = (
                f"MATCH (s:`{rel.source.type}` {{name: $source_name, _domain: $_domain}}) "
                f"MATCH (t:`{rel.target.type}` {{name: $target_name, _domain: $_domain}}) "
                f"MERGE (s)-[r:`{rel.relation_type}`]->(t)"
                f"{set_clause}"
            )
            queries.append((query, params))
        return queries
