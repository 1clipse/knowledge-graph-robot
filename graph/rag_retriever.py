"""GraphRagRetriever — centralized retrieval pipeline for GraphRAG Q&A.

Extracts retrieval strategy from api/routes/ask.py into a dedicated module
with path scoring, context budget management, and citation map building.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger

from graph.client import Neo4jClient
from graph.entity_linker import EntityLinker
from graph.query import GraphQuery

# ── Relation weights for path scoring ──────────────────────────

_RELATION_WEIGHTS: Dict[str, float] = {
    "manufactures": 0.9,
    "uses_reducer": 0.9,
    "uses_servo": 0.9,
    "uses_controller": 0.85,
    "uses_sensor": 0.85,
    "uses_end_effector": 0.85,
    "applied_in": 0.75,
    "performs_process": 0.75,
    "uses_component": 0.7,
    "supplies_component": 0.7,
    "contains": 0.65,
    "component_compatible": 0.5,
    "competitor_of": 0.4,
    "subsidiary_of": 0.4,
    "uses_software": 0.6,
    "complies_with": 0.6,
    "process_requires": 0.7,
    "process_material": 0.6,
    "scenario_includes": 0.7,
}

DEFAULT_RELATION_WEIGHT = 0.5


@dataclass
class ScoredPath:
    index: int
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    path_score: float = 0.0
    seed_match_score: float = 0.0
    relation_weight_score: float = 0.0
    confidence_score: float = 0.0
    path_length_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "path_score": round(self.path_score, 4),
        }


@dataclass
class RetrievalResult:
    question: str
    context_used: str = ""
    search_results: List[Dict[str, Any]] = field(default_factory=list)
    scored_paths: List[ScoredPath] = field(default_factory=list)
    citation_map: Dict[str, int] = field(default_factory=dict)  # marker "P1" -> path index


class GraphRagRetriever:
    """Retrieve and score knowledge graph paths for a question.

    Replaces the inline retrieval logic in ask.py with a testable module.
    """

    def __init__(
        self,
        client: Neo4jClient,
        linker: Optional[EntityLinker] = None,
        max_paths: int = 10,
        max_tokens_est: int = 2500,
    ) -> None:
        self._client = client
        self._linker = linker
        self._graph_query = GraphQuery(client)
        self._max_paths = max_paths
        self._max_tokens_est = max_tokens_est

    def retrieve(
        self,
        question: str,
        top_k: int = 5,
        max_hops: int = 3,
    ) -> RetrievalResult:
        result = RetrievalResult(question=question)

        # Step 1: Entity linking
        linked_entities = self._entity_link(question)

        # Step 2: Hybrid search → seed entities
        search_results = self._graph_query.hybrid_search(question, top_k)

        # Merge linked entities (prefer link results at front)
        if linked_entities:
            linked_names = {e["name"] for e in linked_entities}
            existing_names = {r.get("node", {}).get("name", "") for r in search_results}
            for ent in linked_entities:
                name = ent.get("name", "")
                if name and name not in existing_names:
                    search_results.insert(0, {
                        "labels": [l for l in ent.get("labels", []) if l != "Entity"],
                        "node": {"name": name},
                        "score": 1.0,
                        "source": "entity_linker",
                    })

        if not search_results:
            return result

        search_results = search_results[:top_k]
        result.search_results = search_results

        # Step 3: Get multi-hop paths
        seed_labels: List[str] = []
        seed_names: List[str] = []
        for item in search_results:
            labels = item.get("labels", [])
            node = item.get("node", {})
            name = node.get("name", "")
            if labels and name:
                seed_labels.append(labels[0])
                seed_names.append(name)

        raw_paths = self._graph_query.multi_hop_paths(
            seed_labels, seed_names, max_hops=max_hops, max_paths=20
        )

        # Fallback: when multi-hop returns nothing (isolated entity, no relations),
        # build single-node paths from search results so the LLM can still use them.
        if not raw_paths and search_results:
            raw_paths = self._search_results_as_paths(search_results)

        # Step 4: Score paths
        scored = self._score_paths(raw_paths, seed_names)

        # Step 5: Build context within token budget
        context_parts, kept_paths = self._build_context(scored)
        result.context_used = "\n".join(context_parts)
        result.scored_paths = kept_paths

        # Step 6: Build citation map
        result.citation_map = {f"P{p.index}": p.index - 1 for p in kept_paths}

        logger.info(
            f"GraphRagRetriever: {len(result.search_results)} seeds, "
            f"{len(kept_paths)}/{len(scored)} paths in context, "
            f"~{self._estimate_tokens(result.context_used)} tokens"
        )
        return result

    # ── Entity linking ──────────────────────────────────────────

    def _entity_link(self, question: str) -> List[Dict[str, Any]]:
        linked: List[Dict[str, Any]] = []
        if self._linker is None:
            return linked
        try:
            matches = self._linker.extract_spans_and_link(question, min_score=70.0)
            return [e for e, _, _ in matches]
        except Exception as e:
            logger.debug(f"Entity linker skipped: {e}")
        return linked

    # ── Fallback: isolated entities as single-node paths ───────

    @staticmethod
    def _search_results_as_paths(
        search_results: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Convert search hits into synthetic single-node paths.

        Used when multi_hop_paths returns nothing (entity has no relations
        in the graph), so the LLM still gets the raw entity info.
        """
        paths: List[Dict[str, Any]] = []
        for item in search_results:
            labels = item.get("labels", [])
            node_data = item.get("node", {})
            props = {k: v for k, v in node_data.items()
                     if not k.startswith("_") and v is not None}
            paths.append({
                "nodes": [{"labels": labels, "properties": props}],
                "edges": [],
            })
        return paths

    # ── Path scoring ────────────────────────────────────────────

    def _score_paths(
        self, raw_paths: List[Dict[str, Any]], seed_names: List[str]
    ) -> List[ScoredPath]:
        scored: List[ScoredPath] = []
        seed_set = set(seed_names)

        for i, path in enumerate(raw_paths, 1):
            nodes = path.get("nodes", [])
            edges = path.get("edges", [])
            if not nodes:
                continue

            # Seed match score: how many seed entities are in this path
            path_names = {n.get("properties", {}).get("name", "") for n in nodes}
            seed_hits = len(seed_set & path_names)
            seed_match_score = min(seed_hits / max(len(seed_set), 1), 1.0)

            # Relation weight score: average weight of relations in path
            rel_scores: List[float] = []
            for edge in edges:
                rel_type = edge.get("type", "")
                weight = _RELATION_WEIGHTS.get(rel_type, DEFAULT_RELATION_WEIGHT)
                rel_scores.append(weight)
            relation_weight_score = sum(rel_scores) / max(len(rel_scores), 1) if rel_scores else 0.5

            # Confidence score: average _confidence of relations
            confidences: List[float] = []
            for edge in edges:
                props = edge.get("properties", {})
                conf = props.get("_confidence", 0.7)
                if isinstance(conf, (int, float)):
                    confidences.append(float(conf))
            confidence_score = sum(confidences) / max(len(confidences), 1) if confidences else 0.7

            # Path length score: prefer shorter paths
            path_len = len(edges)
            path_length_score = max(0, 1.0 - (path_len - 1) * 0.2)  # 1 hop=1.0, 2=0.8, 3=0.6...

            # Composite score
            path_score = (
                seed_match_score * 0.35
                + relation_weight_score * 0.20
                + confidence_score * 0.15
                + path_length_score * 0.30
            )

            scored.append(ScoredPath(
                index=i,
                nodes=nodes,
                edges=edges,
                path_score=path_score,
                seed_match_score=seed_match_score,
                relation_weight_score=relation_weight_score,
                confidence_score=confidence_score,
                path_length_score=path_length_score,
            ))

        # Sort by score descending
        scored.sort(key=lambda p: -p.path_score)
        return scored

    # ── Context building ────────────────────────────────────────

    def _build_context(
        self, scored_paths: List[ScoredPath]
    ) -> Tuple[List[str], List[ScoredPath]]:
        context_parts: List[str] = []
        kept: List[ScoredPath] = []
        total_est = 0
        path_index = 1

        for sp in scored_paths:
            if len(kept) >= self._max_paths:
                break
            if total_est > self._max_tokens_est:
                break

            path_line = self._format_path_line(sp, path_index)
            path_tokens = self._estimate_tokens(path_line)
            if total_est + path_tokens > self._max_tokens_est and kept:
                break

            context_parts.append(path_line)
            sp.index = path_index  # renumber for final output
            kept.append(sp)
            total_est += path_tokens
            path_index += 1

        return context_parts, kept

    @staticmethod
    def _format_path_line(sp: ScoredPath, idx: int) -> str:
        chain_parts: List[str] = []
        nodes = sp.nodes
        edges = sp.edges
        priority_keys = (
            "payload", "reach", "repeatability", "axes",
            "rated_torque", "rated_power", "reducer_type",
            "application_type", "process_type", "country",
        )
        for j, node in enumerate(nodes):
            label = "/".join(node.get("labels", ["?"]))
            props = node.get("properties", {})
            name = props.get("name", "?")
            key_props = []
            for pk in priority_keys:
                v = props.get(pk)
                if v is not None and v != "":
                    key_props.append(f"{pk}={v}")
                    if len(key_props) >= 2:
                        break
            node_desc = f"[{label}] {name}"
            if key_props:
                node_desc += f"({', '.join(key_props)})"
            chain_parts.append(node_desc)
            if j < len(edges):
                rel_type = edges[j].get("type", "?")
                chain_parts.append(f" --[{rel_type}]--> ")
        return f"P{idx}: {' '.join(chain_parts)}"

    # ── Token estimation ────────────────────────────────────────

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token estimation: CJK chars ~1 token, English words ~1.3 tokens."""
        chinese = len(re.findall(r'[一-鿿]', text))
        english_tokens = len(re.findall(r'[a-zA-Z]+', text)) * 1.3
        other = len(text) // 6
        return int(chinese + english_tokens + other)

    # ── Context for communities ─────────────────────────────────

    def attach_community_context(
        self, result: RetrievalResult, max_extra_tokens: int = 500
    ) -> None:
        """Append community summaries to the context if space permits."""
        try:
            from graph.communities import CommunityManager
            cm = CommunityManager(self._client)
            communities = cm.detect()
            if not communities:
                return

            current_tokens = self._estimate_tokens(result.context_used)
            if current_tokens > self._max_tokens_est + 100:
                return

            entity_names = {n.get("node", {}).get("name", "") for n in result.search_results}
            relevant_ids: Set[int] = set()
            for c in communities:
                for nd in c["nodes"]:
                    if nd["name"] in entity_names:
                        relevant_ids.add(c["id"])
                        break

            if relevant_ids:
                comm_lines = []
                for c in communities:
                    if c["id"] in relevant_ids:
                        nodes_str = "、".join(n["name"] for n in c["nodes"][:5])
                        rels_str = "、".join(
                            f"{e['source']} {e['relation']} {e['target']}"
                            for e in c["internal_edges"][:5]
                        )
                        comm_lines.append(
                            f"社区{c['id']}({c['size']}个实体): {nodes_str}; 关系: {rels_str}"
                        )
                comm_text = "\n".join(comm_lines)
                comm_tokens = self._estimate_tokens(comm_text)
                if current_tokens + comm_tokens <= self._max_tokens_est + max_extra_tokens:
                    result.context_used += "\n\n--- 社区背景 ---\n" + comm_text
                    logger.debug(f"Added community context: {len(comm_lines)} communities")
        except Exception as e:
            logger.debug(f"Community context skipped: {e}")
