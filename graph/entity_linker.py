"""Entity linking — maps user query mentions to concrete knowledge graph entities.

Supports exact match, substring match, fuzzy match (rapidfuzz), and alias expansion.
"""
from __future__ import annotations

from typing import List, Dict, Any, Optional, Tuple

from loguru import logger

from graph.client import Neo4jClient
from graph.entity_resolver import EntityResolver


class EntityLinker:
    """Resolve entity mentions in queries to KG entities."""

    def __init__(self, client: Neo4jClient, resolver: Optional[EntityResolver] = None) -> None:
        self._client = client
        self._resolver = resolver or EntityResolver()
        self._cache: Optional[List[Dict[str, Any]]] = None

    def _load_entity_index(self, force: bool = False) -> List[Dict[str, Any]]:
        """Load all entity names and labels into an in-memory index."""
        if self._cache is not None and not force:
            return self._cache

        records = self._client.execute_query(
            "MATCH (n) WHERE n.name IS NOT NULL "
            "RETURN n.name AS name, labels(n) AS labels, n.description AS description "
            "ORDER BY n.name"
        )
        self._cache = [dict(r) for r in records]
        logger.info(f"EntityLinker: indexed {len(self._cache)} entities")
        return self._cache

    def link(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 60.0,
    ) -> List[Tuple[Dict[str, Any], float]]:
        """Link a user query to known entities.

        Returns list of (entity_dict, score) sorted by score descending.
        Score is 0-100, with 100 being exact match.
        """
        entities = self._load_entity_index()
        if not entities:
            return []

        results: List[Tuple[Dict[str, Any], float]] = []
        query_lower = query.lower()

        # Build alias-expanded set for matching
        alias_expansions: Dict[str, str] = {}  # alias_lower -> canonical_lower
        for ent in entities:
            name = ent["name"]
            labels = ent.get("labels", [])
            for lbl in labels:
                if lbl == "IngestLog":
                    continue
                resolved = self._resolver.resolve(name, lbl)
                if resolved.resolved_from != "self":
                    alias_expansions[name.lower()] = resolved.canonical.lower()

        for ent in entities:
            name = ent["name"]
            name_lower = name.lower()
            canonical_lower = alias_expansions.get(name_lower, name_lower)

            score = self._score_match(query_lower, name_lower, query, name)
            # Also try matching against canonical form
            if canonical_lower != name_lower:
                canonical_score = self._score_match(query_lower, canonical_lower, query, name)
                score = max(score, canonical_score)
            if score >= min_score:
                results.append((ent, score))

        results.sort(key=lambda x: -x[1])
        return results[:top_k]

    @staticmethod
    def _score_match(query_lower: str, name_lower: str, query: str, name: str) -> float:
        """Score how well an entity name matches a query.

        Priority:
        1. Exact match: 100
        2. Query contains entity name: 95
        3. Entity name contains query: 90
        4. Token overlap (Jaccard): up to 80
        5. Fuzzy match (rapidfuzz): up to 75
        """
        # 1. Exact match
        if query_lower.strip() == name_lower.strip():
            return 100.0

        # 2. Full entity name appears as substring in query
        if name_lower in query_lower:
            # Longer match = better. Scale by ratio of entity_len/query_len
            ratio = len(name) / max(len(query), 1)
            return 85.0 + 10.0 * min(ratio, 1.0)

        # 3. Query is substring of entity name
        if query_lower in name_lower:
            ratio = len(query) / max(len(name), 1)
            return 80.0 + 10.0 * min(ratio, 1.0)

        # 4. Token overlap
        query_tokens = set(query_lower.split())
        name_tokens = set(name_lower.split())
        if query_tokens and name_tokens:
            jaccard = len(query_tokens & name_tokens) / len(query_tokens | name_tokens)
            if jaccard > 0.3:
                return 60.0 + 20.0 * jaccard

        # 5. Fuzzy match for typos / abbreviations
        try:
            from rapidfuzz import fuzz
            ratio = fuzz.partial_ratio(query_lower, name_lower)
            if ratio > 80:
                return 55.0 + 0.25 * (ratio - 80)
        except ImportError:
            pass

        return 0.0

    def extract_spans_and_link(
        self, query: str, min_score: float = 70.0
    ) -> List[Tuple[Dict[str, Any], float, str]]:
        """Extract potential entity spans from query and link them.

        Returns list of (entity, score, matched_span).
        """
        entities = self._load_entity_index()
        if not entities:
            return []

        results: List[Tuple[Dict[str, Any], float, str]] = []
        query_lower = query.lower()

        for ent in entities:
            name = ent["name"]
            name_lower = name.lower()

            # Find where the entity name appears in the query
            idx = query_lower.find(name_lower)
            if idx >= 0:
                span = query[idx : idx + len(name)]
                score = 85.0 + 10.0 * min(len(name) / max(len(query), 1), 1.0)
                results.append((ent, score, span))
                continue

            # Find longest common substring (simple sliding window)
            matched_span = self._longest_common_substring(query_lower, name_lower, min_len=2)
            if matched_span and len(matched_span) >= 2:
                score = self._score_match(query_lower, name_lower, query, name)
                if score >= min_score:
                    results.append((ent, score, matched_span))

        # Deduplicate: keep best score per entity
        seen: set = set()
        deduped = []
        for ent, score, span in sorted(results, key=lambda x: -x[1]):
            key = ent["name"]
            if key not in seen:
                seen.add(key)
                deduped.append((ent, score, span))

        return deduped[:10]

    @staticmethod
    def _longest_common_substring(s1: str, s2: str, min_len: int = 2) -> str:
        """Find longest common substring between s1 and s2."""
        if not s1 or not s2:
            return ""
        m, n = len(s1), len(s2)
        # Use simple approach for short strings
        best = ""
        for i in range(m):
            for j in range(n):
                k = 0
                while i + k < m and j + k < n and s1[i + k] == s2[j + k]:
                    k += 1
                if k > len(best) and k >= min_len:
                    best = s1[i : i + k]
        return best

    def refresh(self) -> None:
        """Force refresh the entity index cache."""
        self._load_entity_index(force=True)
