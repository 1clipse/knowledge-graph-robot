from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from config.settings import get_config
from extractors.llm_extractor import LLMExtractor, ExtractionResult
from extractors.rule_extractor import RuleExtractor
from extractors.structured_mapper import StructuredMapper
from graph.client import Neo4jClient
from graph.schema_manager import SchemaManager
from loaders.csv_loader import CSVLoader
from loaders.db_loader import DBLoader
from loaders.pdf_loader import PDFLoader
from loaders.web_loader import WebLoader


class IngestPipeline:
    def __init__(self, neo4j_client: Optional[Neo4jClient] = None) -> None:
        self._config = get_config()
        self._client = neo4j_client or Neo4jClient()
        self._schema_manager: Optional[SchemaManager] = None
        self._llm_extractor: Optional[LLMExtractor] = None
        self._rule_extractor = RuleExtractor()
        self._mapper = StructuredMapper()

    def initialize(self) -> None:
        self._client.connect()
        self._schema_manager = SchemaManager(self._client)
        self._schema_manager.initialize_schema()
        self._llm_extractor = LLMExtractor()
        logger.info("IngestPipeline initialized")

    def _write_result(self, result: ExtractionResult) -> None:
        if not result.entities:
            return

        from graph.client import _validate_identifier

        # Batch embed
        embed_texts: list[str] = []
        valid_entities: list = []
        for entity in result.entities:
            if self._schema_manager and not self._schema_manager.validate_entity_type(entity.type):
                logger.warning(f"Skipping invalid entity type: {entity.type}")
                continue
            embed_texts.append(
                f"{entity.type} {entity.name} {entity.properties.get('description', '')}"
            )
            valid_entities.append(entity)

        embeddings_map: dict[int, list] = {}
        if embed_texts:
            try:
                from graph.embeddings import embed_texts as batch_embed
                emb_list = batch_embed(embed_texts)
                for i, emb in enumerate(emb_list):
                    embeddings_map[i] = emb
            except Exception as e:
                logger.warning(f"Batch embedding failed: {e}")

        # Batch write entities
        entity_queries: list[tuple[str, dict]] = []
        for i, entity in enumerate(valid_entities):
            props = {k: v for k, v in entity.properties.items() if v is not None}
            props["name"] = entity.name
            if entity.source:
                props["_source"] = entity.source
            props["_confidence"] = entity.confidence
            if entity.valid_from:
                props["valid_from"] = entity.valid_from
            if entity.valid_to:
                props["valid_to"] = entity.valid_to
            if i in embeddings_map:
                props["_embedding"] = embeddings_map[i]

            _validate_identifier(entity.type, "entity_type")
            prop_assignments = ", ".join(f"n.{k} = ${k}" for k in props.keys())
            query = f"MERGE (n:`{entity.type}` {{name: $name}}) SET {prop_assignments}"
            entity_queries.append((query, props))

        relation_queries: list[tuple[str, dict]] = []
        for rel in result.relations:
            if self._schema_manager and not self._schema_manager.validate_relation_type(rel.relation_type):
                logger.warning(f"Skipping invalid relation type: {rel.relation_type}")
                continue
            rel_props = {k: v for k, v in rel.properties.items() if v is not None}
            if rel.source_ref:
                rel_props["_source"] = rel.source_ref
            rel_props["_confidence"] = rel.confidence
            if rel.valid_from:
                rel_props["valid_from"] = rel.valid_from
            if rel.valid_to:
                rel_props["valid_to"] = rel.valid_to

            _validate_identifier(rel.source.type, "source_type")
            _validate_identifier(rel.target.type, "target_type")
            _validate_identifier(rel.relation_type, "relation_type")
            params: dict = {
                "source_name": rel.source.name,
                "target_name": rel.target.name,
            }
            set_clause = ""
            if rel_props:
                set_clause = " SET " + ", ".join(f"r.{k} = ${k}" for k in rel_props.keys())
                params.update(rel_props)
            query = (
                f"MATCH (s:`{rel.source.type}` {{name: $source_name}}) "
                f"MATCH (t:`{rel.target.type}` {{name: $target_name}}) "
                f"MERGE (s)-[r:`{rel.relation_type}`]->(t)"
                f"{set_clause}"
            )
            relation_queries.append((query, params))

        if entity_queries:
            self._client.execute_write_batch(entity_queries)
        if relation_queries:
            self._client.execute_write_batch(relation_queries)

    async def ingest_text(self, text: str, use_llm: bool = True) -> ExtractionResult:
        result = ExtractionResult()
        if use_llm and self._llm_extractor:
            try:
                result = await self._llm_extractor.extract(text)
            except Exception as e:
                logger.error(f"LLM extraction failed: {e}")

        if not result.entities:
            result = self._rule_extractor.extract(text)

        self._write_result(result)
        return result

    async def ingest_pdf(self, file_path: str) -> ExtractionResult:
        loader = PDFLoader(
            chunk_size=self._config.extraction.chunk_size,
            chunk_overlap=self._config.extraction.chunk_overlap,
        )
        chunks = loader.load_and_chunk(file_path)
        logger.info(f"PDF loaded: {len(chunks)} chunks")

        all_results: List[ExtractionResult] = []
        if self._llm_extractor:
            all_results = await self._llm_extractor.extract_batch(chunks)

        merged = self._merge_results(all_results)
        if not merged.entities:
            full_text = "\n\n".join(chunks)
            merged = self._rule_extractor.extract(full_text)

        self._write_result(merged)
        return merged

    async def ingest_url(self, url: str, selector: Optional[str] = None) -> ExtractionResult:
        loader = WebLoader()
        text = loader.load(url, selector)
        return await self.ingest_text(text)

    def ingest_csv(self, file_path: str) -> ExtractionResult:
        result = self._mapper.map_csv(file_path)
        self._write_result(result)
        return result

    def ingest_directory(self, dir_path: str) -> Dict[str, ExtractionResult]:
        path = Path(dir_path)
        if not path.is_dir():
            raise ValueError(f"Not a directory: {dir_path}")

        results: Dict[str, ExtractionResult] = {}
        for file_path in path.rglob("*"):
            if file_path.is_file():
                suffix = file_path.suffix.lower()
                try:
                    if suffix == ".pdf":
                        results[str(file_path)] = asyncio.run(
                            self.ingest_pdf(str(file_path))
                        )
                    elif suffix == ".csv":
                        results[str(file_path)] = self.ingest_csv(str(file_path))
                    elif suffix in (".txt", ".md"):
                        text = file_path.read_text(encoding="utf-8")
                        results[str(file_path)] = asyncio.run(
                            self.ingest_text(text)
                        )
                    else:
                        logger.info(f"Skipping unsupported file: {file_path}")
                except Exception as e:
                    logger.error(f"Failed to ingest {file_path}: {e}")
                    results[str(file_path)] = ExtractionResult()

        return results

    def load_sample_data(self) -> None:
        if self._schema_manager:
            self._schema_manager.load_sample_data()

    def _merge_results(self, results: List[ExtractionResult]) -> ExtractionResult:
        all_entities: List = []
        all_relations: List = []
        for r in results:
            all_entities.extend(r.entities)
            all_relations.extend(r.relations)

        if self._llm_extractor and all_entities:
            all_entities = self._llm_extractor.disambiguate_entities(all_entities)

        return ExtractionResult(entities=all_entities, relations=all_relations)

    def close(self) -> None:
        self._client.close()
