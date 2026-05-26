from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from loguru import logger

from config.settings import get_config
from extractors.llm_extractor import LLMExtractor, ExtractionResult
from extractors.rule_extractor import RuleExtractor
from graph.client import Neo4jClient
from graph.query import GraphQuery
from loaders.csv_loader import CSVLoader
from loaders.db_loader import DBLoader


class UpdatePipeline:
    def __init__(self, neo4j_client: Optional[Neo4jClient] = None) -> None:
        self._config = get_config()
        self._client = neo4j_client or Neo4jClient()
        self._llm_extractor: Optional[LLMExtractor] = None
        self._rule_extractor = RuleExtractor()

    def initialize(self) -> None:
        self._client.connect()
        self._llm_extractor = LLMExtractor()
        logger.info("UpdatePipeline initialized")

    def _compute_hash(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _get_processed_hashes(self) -> Set[str]:
        try:
            records = self._client.execute_query(
                "MATCH (n:IngestLog) RETURN n.content_hash AS hash"
            )
            return {r["hash"] for r in records}
        except Exception:
            return set()

    def _mark_processed(self, source: str, content_hash: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        props = {
            "source": source,
            "content_hash": content_hash,
            "timestamp": datetime.now().isoformat(),
        }
        if metadata:
            props.update(metadata)
        self._client.create_node("IngestLog", props, merge=True)

    async def incremental_text_update(
        self, texts: List[Dict[str, str]], force: bool = False
    ) -> Dict[str, ExtractionResult]:
        if not force:
            processed = self._get_processed_hashes()
        else:
            processed = set()

        results: Dict[str, ExtractionResult] = {}
        for item in texts:
            source = item.get("source", "unknown")
            text = item.get("text", "")
            content_hash = self._compute_hash(text)

            if not force and content_hash in processed:
                logger.info(f"Skipping already processed: {source}")
                continue

            result = ExtractionResult()
            if self._llm_extractor:
                try:
                    result = await self._llm_extractor.extract(text)
                except Exception as e:
                    logger.error(f"LLM extraction failed for {source}: {e}")

            if not result.entities:
                result = self._rule_extractor.extract(text)

            self._write_result(result)
            self._mark_processed(source, content_hash)
            results[source] = result

        logger.info(f"Incremental update: {len(results)} items processed")
        return results

    def incremental_csv_update(
        self, file_path: str, key_columns: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        loader = CSVLoader()
        rows = loader.load(file_path)

        graph_query = GraphQuery(self._client)
        stats = {"new": 0, "updated": 0, "unchanged": 0}

        for row in rows:
            name = row.get("name", "")
            if not name:
                continue

            existing = self._client.get_node("Robot", name)
            if existing:
                changed = False
                for k, v in row.items():
                    if v is not None and str(existing.get(k, "")) != str(v):
                        changed = True
                        break
                if changed:
                    props = {k: v for k, v in row.items() if v is not None}
                    self._client.create_node("Robot", props, merge=True)
                    stats["updated"] += 1
                else:
                    stats["unchanged"] += 1
            else:
                props = {k: v for k, v in row.items() if v is not None}
                self._client.create_node("Robot", props, merge=False)
                stats["new"] += 1

        logger.info(f"CSV incremental update: {stats}")
        return stats

    def incremental_db_update(
        self,
        query: str,
        node_type: str,
        key_column: str = "name",
        connection_string: Optional[str] = None,
    ) -> Dict[str, Any]:
        db_loader = DBLoader(connection_string)
        rows = db_loader.load(query)

        stats = {"new": 0, "updated": 0, "unchanged": 0}

        for row in rows:
            key_value = row.get(key_column, "")
            if not key_value:
                continue

            existing = self._client.get_node(node_type, key_value)
            if existing:
                changed = False
                for k, v in row.items():
                    if v is not None and str(existing.get(k, "")) != str(v):
                        changed = True
                        break
                if changed:
                    props = {k: v for k, v in row.items() if v is not None}
                    self._client.create_node(node_type, props, merge=True)
                    stats["updated"] += 1
                else:
                    stats["unchanged"] += 1
            else:
                props = {k: v for k, v in row.items() if v is not None}
                self._client.create_node(node_type, props, merge=False)
                stats["new"] += 1

        logger.info(f"DB incremental update: {stats}")
        return stats

    def _write_result(self, result: ExtractionResult) -> None:
        for entity in result.entities:
            props = {k: v for k, v in entity.properties.items() if v is not None}
            props["name"] = entity.name
            self._client.create_node(entity.type, props, merge=True)

        for rel in result.relations:
            rel_props = {k: v for k, v in rel.properties.items() if v is not None}
            self._client.create_relation(
                source_label=rel.source.type,
                source_name=rel.source.name,
                target_label=rel.target.type,
                target_name=rel.target.name,
                relation_type=rel.relation_type,
                properties=rel_props,
            )

    def close(self) -> None:
        self._client.close()
