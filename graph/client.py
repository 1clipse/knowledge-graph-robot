from __future__ import annotations

import re
import ssl
from typing import Any, Dict, List, Optional

import certifi
from loguru import logger
from neo4j import GraphDatabase, ManagedTransaction
from neo4j.exceptions import ServiceUnavailable

from config.settings import Neo4jConfig, get_config

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_identifier(value: str, kind: str = "identifier") -> str:
    """Validate a Cypher identifier (label, relation type) against a safe pattern.

    Raises ValueError if the value contains characters that could enable injection.
    """
    if not value or not _IDENTIFIER_RE.match(value):
        raise ValueError(
            f"Invalid {kind}: '{value}'. Must match pattern: {_IDENTIFIER_RE.pattern}"
        )
    return value


class Neo4jClient:
    def __init__(self, config: Optional[Neo4jConfig] = None) -> None:
        self._config = config or get_config().neo4j
        self._driver = None

    def connect(self) -> None:
        if self._driver is not None:
            return
        try:
            driver_kwargs: Dict[str, Any] = {
                "auth": (self._config.username, self._config.password),
                "max_connection_pool_size": self._config.max_connection_pool_size,
                "connection_timeout": self._config.connection_timeout,
            }
            # Only force SSL for cloud URIs (neo4j:// or neo4j+s://)
            if "+s" in self._config.uri or self._config.uri.startswith("neo4j://"):
                driver_kwargs["ssl_context"] = ssl.create_default_context(cafile=certifi.where())
            self._driver = GraphDatabase.driver(self._config.uri, **driver_kwargs)
            self._driver.verify_connectivity()
            logger.info(f"Connected to Neo4j at {self._config.uri}")
        except ServiceUnavailable as e:
            logger.error(f"Cannot connect to Neo4j: {e}")
            raise
        except Exception as e:
            logger.error(f"Neo4j connection error: {e}")
            raise

    def close(self) -> None:
        if self._driver:
            self._driver.close()
            self._driver = None
            logger.info("Neo4j connection closed")

    @property
    def driver(self):
        if self._driver is None:
            self.connect()
        return self._driver

    def execute_query(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        db = database or self._config.database
        try:
            with self.driver.session(database=db) as session:
                result = session.run(query, parameters or {})
                records = [dict(record) for record in result]
                logger.debug(f"Query executed: {query[:80]}... ({len(records)} records)")
                return records
        except Exception as e:
            logger.error(f"Query execution failed: {e}")
            raise

    def execute_read(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
        database: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Execute a query in a read-only transaction. Write operations are rejected by Neo4j."""
        db = database or self._config.database

        def _tx(tx: ManagedTransaction) -> List[Dict[str, Any]]:
            result = tx.run(query, parameters or {})
            return [dict(record) for record in result]

        try:
            with self.driver.session(database=db) as session:
                return session.execute_read(_tx)
        except Exception as e:
            logger.error(f"Read query failed: {e}")
            raise

    def execute_write(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
        database: Optional[str] = None,
    ) -> None:
        db = database or self._config.database

        def _tx(tx: ManagedTransaction) -> None:
            tx.run(query, parameters or {})

        try:
            with self.driver.session(database=db) as session:
                session.execute_write(_tx)
                logger.debug(f"Write executed: {query[:80]}...")
        except Exception as e:
            logger.error(f"Write execution failed: {e}")
            raise

    def execute_write_batch(
        self,
        queries: List[tuple],
        database: Optional[str] = None,
    ) -> int:
        db = database or self._config.database
        success_count = 0

        def _tx(tx: ManagedTransaction) -> int:
            count = 0
            for query, params in queries:
                try:
                    tx.run(query, params)
                    count += 1
                except Exception as e:
                    logger.warning(f"Batch item failed: {e}")
            return count

        try:
            with self.driver.session(database=db) as session:
                success_count = session.execute_write(_tx)
                logger.info(f"Batch write: {success_count}/{len(queries)} succeeded")
        except Exception as e:
            logger.error(f"Batch write failed: {e}")
            raise

        return success_count

    def create_node(
        self,
        label: str,
        properties: Dict[str, Any],
        merge: bool = True,
    ) -> None:
        _validate_identifier(label, "label")
        prop_assignments = ", ".join(
            f"n.{k} = ${k}" for k in properties.keys()
        )
        if merge:
            query = f"MERGE (n:`{label}` {{name: $name}}) SET {prop_assignments}"
        else:
            query = f"CREATE (n:`{label}`) SET {prop_assignments}"
        self.execute_write(query, properties)

    def create_relation(
        self,
        source_label: str,
        source_name: str,
        target_label: str,
        target_name: str,
        relation_type: str,
        properties: Optional[Dict[str, Any]] = None,
    ) -> None:
        _validate_identifier(source_label, "source_label")
        _validate_identifier(target_label, "target_label")
        _validate_identifier(relation_type, "relation_type")
        query = (
            f"MATCH (s:`{source_label}` {{name: $source_name}}) "
            f"MATCH (t:`{target_label}` {{name: $target_name}}) "
            f"MERGE (s)-[r:`{relation_type}`]->(t)"
        )
        params: Dict[str, Any] = {
            "source_name": source_name,
            "target_name": target_name,
        }
        if properties:
            set_clause = ", ".join(f"r.{k} = ${k}" for k in properties.keys())
            query += f" SET {set_clause}"
            params.update(properties)
        self.execute_write(query, params)

    def delete_node(self, label: str, name: str) -> int:
        """Delete a node and its relations. Returns count of deleted nodes."""
        _validate_identifier(label, "label")
        query = f"MATCH (n:`{label}` {{name: $name}}) WITH n, count(n) AS cnt DETACH DELETE n RETURN cnt"
        records = self.execute_query(query, {"name": name})
        return records[0]["cnt"] if records else 0

    def delete_by_file(self, filename: str) -> int:
        """Delete all nodes (and their relations) that have file property matching filename."""
        query = (
            "MATCH (n) WHERE n.file = $filename OR n.file = $basename "
            "WITH n, count(n) AS cnt DETACH DELETE n RETURN cnt"
        )
        basename = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        records = self.execute_query(query, {"filename": filename, "basename": basename})
        return records[0]["cnt"] if records else 0

    def get_ingest_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return recent ingest log entries, newest first."""
        query = (
            "MATCH (n:IngestLog) WHERE n.source IS NOT NULL "
            "RETURN n.source AS source, n.filename AS filename, "
            "n.entities_count AS entities_count, n.relations_count AS relations_count, "
            "n.timestamp AS timestamp, n.success AS success "
            "ORDER BY n.timestamp DESC LIMIT $limit"
        )
        return self.execute_query(query, {"limit": limit})

    def list_graph_files(self) -> List[Dict[str, Any]]:
        """List distinct files uploaded to the graph, with entity counts (works without IngestLog nodes)."""
        query = (
            "MATCH (n) WHERE n.file IS NOT NULL "
            "WITH n.file AS filename, collect(DISTINCT labels(n)[0]) AS types, count(n) AS cnt "
            "RETURN filename, types, cnt ORDER BY cnt DESC"
        )
        return self.execute_query(query)

    def delete_ingest_log(self, source: str) -> int:
        """Delete a specific IngestLog entry by source identifier."""
        query = "MATCH (n:IngestLog {source: $source}) WITH n, count(n) AS cnt DETACH DELETE n RETURN cnt"
        records = self.execute_query(query, {"source": source})
        return records[0]["cnt"] if records else 0

    def get_node(self, label: str, name: str) -> Optional[Dict[str, Any]]:
        _validate_identifier(label, "label")
        query = f"MATCH (n:`{label}` {{name: $name}}) RETURN n"
        records = self.execute_query(query, {"name": name})
        if records:
            return dict(records[0]["n"])
        return None

    def vector_search(
        self,
        query_embedding: List[float],
        top_k: int = 20,
    ) -> List[Dict[str, Any]]:
        """Native Neo4j vector index search (Neo4j 5.15+)."""
        try:
            records = self.execute_query(
                "CALL db.index.vector.queryNodes('entity_embeddings', $top_k, $embedding) "
                "YIELD node, score "
                "RETURN labels(node) AS labels, node.name AS name, node AS properties, score "
                "ORDER BY score DESC",
                {"embedding": query_embedding, "top_k": top_k},
            )
            return [dict(r) for r in records]
        except Exception as e:
            logger.debug(f"Native vector search unavailable, falling back: {e}")
            return []

    def health_check(self) -> bool:
        try:
            result = self.execute_query("RETURN 1 AS health")
            return len(result) > 0
        except Exception:
            return False
