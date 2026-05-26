from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from graph.client import Neo4jClient, _validate_identifier


class GraphQuery:
    def __init__(self, client: Neo4jClient) -> None:
        self._client = client

    def shortest_path(
        self,
        source_label: str,
        source_name: str,
        target_label: str,
        target_name: str,
        max_depth: int = 5,
    ) -> List[Dict[str, Any]]:
        s_clause = f"(s:`{_validate_identifier(source_label, 'source_label')}`)" if source_label else "(s)"
        t_clause = f"(t:`{_validate_identifier(target_label, 'target_label')}`)" if target_label else "(t)"
        query = (
            f"MATCH {s_clause} WHERE s.name = $source_name "
            f"MATCH {t_clause} WHERE t.name = $target_name "
            f"MATCH p = shortestPath((s)-[*..{max_depth}]-(t)) "
            f"RETURN p"
        )
        params = {"source_name": source_name, "target_name": target_name}
        records = self._client.execute_query(query, params)
        paths: List[Dict[str, Any]] = []
        for record in records:
            path = record.get("p")
            if path:
                paths.append(self._path_to_dict(path))
        return paths

    def neighbors(
        self,
        label: str,
        name: str,
        relation_type: Optional[str] = None,
        direction: str = "both",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        _validate_identifier(label, "label")
        rel_type_str = f":`{_validate_identifier(relation_type, 'relation_type')}`" if relation_type else ""
        if direction == "out":
            rel_pattern = f"-[r{rel_type_str}]->"
        elif direction == "in":
            rel_pattern = f"<-[r{rel_type_str}]-"
        else:
            rel_pattern = f"-[r{rel_type_str}]-"

        query = (
            f"MATCH (n:`{label}` {{name: $name}}) {rel_pattern} (m) "
            f"RETURN m, type(r) AS relation_type, "
            f"CASE WHEN startNode(r) = n THEN 'out' ELSE 'in' END AS direction "
            f"LIMIT $limit"
        )
        records = self._client.execute_query(query, {"name": name, "limit": limit})
        results: List[Dict[str, Any]] = []
        for record in records:
            node = record.get("m", {})
            results.append(
                {
                    "node": dict(node),
                    "relation_type": record.get("relation_type", ""),
                    "direction": record.get("direction", ""),
                }
            )
        return results

    def multi_hop_paths(
        self,
        seed_labels: List[str],
        seed_names: List[str],
        max_hops: int = 3,
        max_paths: int = 30,
    ) -> List[Dict[str, Any]]:
        """Expand from seed entities and return multi-hop paths as reasoning chains."""
        if not seed_names or not seed_labels:
            return []

        seed_pairs = []
        for i, (label, name) in enumerate(zip(seed_labels, seed_names)):
            _validate_identifier(label, f"seed_label[{i}]")
            seed_pairs.append(f"(n{i}:`{label}` {{name: $name{i}}})")

        match_seeds = "MATCH " + ", ".join(seed_pairs)
        params: Dict[str, Any] = {}
        for i, name in enumerate(seed_names):
            params[f"name{i}"] = name
        params["max_paths"] = max_paths

        query = (
            f"{match_seeds} "
            f"WITH [{', '.join(f'n{i}' for i in range(len(seed_names)))}] AS seeds "
            f"UNWIND seeds AS seed "
            f"MATCH path = (seed)-[*1..{max_hops}]-(reachable) "
            f"RETURN path, length(path) AS hops "
            f"ORDER BY hops ASC "
            f"LIMIT $max_paths"
        )

        records = self._client.execute_query(query, params)
        paths: List[Dict[str, Any]] = []
        for record in records:
            p = record.get("path")
            if p:
                paths.append(self._path_to_dict(p))
        return paths

    @staticmethod
    def _display_labels(node_labels) -> list:
        """Filter out the internal 'Entity' base label from display."""
        raw = list(node_labels) if hasattr(node_labels, "__iter__") else []
        return [l for l in raw if l != "Entity"]

    @staticmethod
    def _primary_label(node_labels) -> str:
        """Return the primary domain label, skipping 'Entity'."""
        for l in (list(node_labels) if hasattr(node_labels, "__iter__") else []):
            if l != "Entity":
                return l
        return ""

    def subgraph(
        self,
        label: str,
        name: str,
        depth: int = 2,
        limit: int = 200,
    ) -> Dict[str, Any]:
        _validate_identifier(label, "label")
        query = (
            f"MATCH (n:`{label}` {{name: $name}})-[r*1..{depth}]-(m) "
            f"RETURN n, r, m LIMIT $limit"
        )
        records = self._client.execute_query(query, {"name": name, "limit": limit})

        nodes: Dict[str, Dict[str, Any]] = {}
        edges: List[Dict[str, Any]] = []

        for record in records:
            n = record.get("n", {})
            m = record.get("m", {})
            n_key = f"{self._primary_label(n.labels)}::{n['name']}" if hasattr(n, "labels") else str(id(n))
            m_key = f"{self._primary_label(m.labels)}::{m['name']}" if hasattr(m, "labels") else str(id(m))

            if n_key not in nodes:
                nodes[n_key] = {"id": n_key, "labels": self._display_labels(n.labels), "properties": dict(n)}
            if m_key not in nodes:
                nodes[m_key] = {"id": m_key, "labels": self._display_labels(m.labels), "properties": dict(m)}

            rels = record.get("r", [])
            if not isinstance(rels, list):
                rels = [rels]
            for rel in rels:
                start_node = rel.start_node
                end_node = rel.end_node
                start_key = f"{self._primary_label(start_node.labels)}::{start_node['name']}" if hasattr(start_node, "labels") else str(id(start_node))
                end_key = f"{self._primary_label(end_node.labels)}::{end_node['name']}" if hasattr(end_node, "labels") else str(id(end_node))
                edges.append(
                    {
                        "source": start_key,
                        "target": end_key,
                        "type": rel.type,
                        "properties": dict(rel),
                    }
                )

        return {"nodes": list(nodes.values()), "edges": edges}

    def hybrid_search(self, query_text: str, top_k: int = 20) -> List[Dict[str, Any]]:
        """Hybrid search: native vector index + fulltext, deduplicated and ranked."""
        all_results: List[Dict[str, Any]] = []

        # Vector search — prefer native Neo4j index, fall back to in-memory scan
        try:
            from graph.embeddings import embed_single

            query_emb = embed_single(query_text)
            native_results = self._client.vector_search(query_emb, top_k)
            if native_results:
                for r in native_results:
                    all_results.append({
                        "labels": self._display_labels(r.get("labels", [])),
                        "node": r.get("properties", {"name": r.get("name", "")}),
                        "score": r.get("score", 0),
                        "source": "vector",
                    })
            else:
                # Fallback: in-memory cosine similarity scan
                self._vector_search_fallback(query_emb, top_k, all_results)
        except Exception as e:
            logger.debug(f"Vector search skipped: {e}")

        # Keyword search
        kw_results = self.fulltext_search(query_text, limit=top_k)
        for item in kw_results:
            item["source"] = "keyword"
        all_results.extend(kw_results)

        # Deduplicate by name
        seen_names: set = set()
        merged: List[Dict[str, Any]] = []
        for item in all_results:
            name = item.get("node", {}).get("name", "")
            if name and name not in seen_names:
                seen_names.add(name)
                merged.append(item)
        return merged[:top_k]

    def _vector_search_fallback(
        self, query_emb: list, top_k: int, results: list
    ) -> None:
        """In-memory cosine similarity scan — fallback when native vector index is unavailable."""
        from graph.embeddings import cosine_similarity

        raw = self._client.execute_query(
            "MATCH (n) WHERE n._embedding IS NOT NULL AND n.name IS NOT NULL "
            "RETURN labels(n) AS labels, n.name AS name, n._embedding AS embedding "
            "LIMIT 500"
        )
        scored = []
        for r in raw:
            emb = r.get("embedding")
            name = r.get("name", "")
            if emb and name and not all(v == 0.0 for v in emb):
                sim = cosine_similarity(query_emb, emb)
                scored.append((r["labels"], name, sim))
        scored.sort(key=lambda x: -x[2])
        for labels, name, score in scored[:top_k]:
            results.append({
                "labels": self._display_labels(labels),
                "node": {"name": name},
                "score": score,
                "source": "vector_fallback",
            })

    def fulltext_search(self, search_term: str, limit: int = 20) -> List[Dict[str, Any]]:
        query = (
            "CALL db.index.fulltext.queryNodes('entity_search', $search_term) "
            "YIELD node, score RETURN node, score LIMIT $limit"
        )
        try:
            records = self._client.execute_query(query, {"search_term": search_term, "limit": limit})
            results: List[Dict[str, Any]] = []
            for record in records:
                node = record.get("node", {})
                results.append(
                    {
                        "node": dict(node),
                        "labels": self._display_labels(node.labels),
                        "score": record.get("score", 0),
                    }
                )
            if not results:
                return self._fallback_search(search_term, limit)
            return results
        except Exception as e:
            logger.warning(f"Fulltext search failed (index may not exist): {e}")
            return self._fallback_search(search_term, limit)

    def _fallback_search(self, search_term: str, limit: int = 20) -> List[Dict[str, Any]]:
        query = (
            "MATCH (n) WHERE n.name CONTAINS $search_term "
            "RETURN n LIMIT $limit"
        )
        records = self._client.execute_query(query, {"search_term": search_term, "limit": limit})
        return [
            {
                "node": dict(record["n"]),
                "labels": self._display_labels(record["n"].labels),
                "score": 1.0,
            }
            for record in records
        ]

    def statistics(self) -> Dict[str, Any]:
        stats: Dict[str, Any] = {}
        try:
            node_count_records = self._client.execute_query(
                "MATCH (n) RETURN count(n) AS count"
            )
            stats["total_nodes"] = node_count_records[0]["count"] if node_count_records else 0

            rel_count_records = self._client.execute_query(
                "MATCH ()-[r]->() RETURN count(r) AS count"
            )
            stats["total_relations"] = rel_count_records[0]["count"] if rel_count_records else 0

            label_records = self._client.execute_query(
                "CALL db.labels() YIELD label RETURN label"
            )
            stats["node_labels"] = [r["label"] for r in label_records]

            rel_type_records = self._client.execute_query(
                "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType"
            )
            stats["relation_types"] = [r["relationshipType"] for r in rel_type_records]

            degree_records = self._client.execute_query(
                "MATCH (n)-[r]-() "
                "RETURN labels(n) AS labels, n.name AS name, count(r) AS degree "
                "ORDER BY degree DESC LIMIT 15"
            )
            for rec in degree_records:
                lbls = rec.get("labels", [])
                rec["label"] = lbls[0] if lbls else "Unknown"
            stats["top_degree_nodes"] = degree_records
        except Exception as e:
            logger.error(f"Statistics query failed: {e}")
        return stats

    def _path_to_dict(self, path: Any) -> Dict[str, Any]:
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        for node in path.nodes:
            nodes.append(
                {
                    "labels": self._display_labels(node.labels),
                    "properties": dict(node),
                }
            )
        for rel in path.relationships:
            edges.append(
                {
                    "type": rel.type,
                    "properties": dict(rel),
                    "start": rel.start_node["name"],
                    "end": rel.end_node["name"],
                }
            )
        return {"nodes": nodes, "edges": edges}
