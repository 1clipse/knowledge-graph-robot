from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from graph.rag_retriever import (
    GraphRagRetriever,
    ScoredPath,
    RetrievalResult,
    _RELATION_WEIGHTS,
    DEFAULT_RELATION_WEIGHT,
)


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.execute_query.return_value = []
    return client


@pytest.fixture
def retriever(mock_client):
    return GraphRagRetriever(mock_client)


def _make_path(node_names, edge_types, labels="Robot"):
    nodes = []
    for name in node_names:
        nodes.append({
            "labels": [labels],
            "properties": {"name": name},
        })
    edges = []
    for i, etype in enumerate(edge_types):
        edges.append({
            "type": etype,
            "properties": {"_confidence": 0.8},
            "start": node_names[i],
            "end": node_names[i + 1],
        })
    return {"nodes": nodes, "edges": edges}


class TestPathScoring:
    def test_seed_match_boosts_score(self, retriever):
        """Paths containing more seed entities score higher."""
        path_with_seed = _make_path(
            ["FANUC", "M-20iA", "RV-40E"],
            ["manufactures", "uses_reducer"],
        )
        path_without_seed = _make_path(
            ["ABB", "IRB 6700"],
            ["manufactures"],
        )
        scored = retriever._score_paths(
            [path_with_seed, path_without_seed],
            seed_names=["FANUC", "M-20iA"],
        )
        assert scored[0].seed_match_score > scored[1].seed_match_score

    def test_high_weight_relations_score_higher(self, retriever):
        """manufactures (0.9) paths score higher than competitor_of (0.4)."""
        path_good = _make_path(["FANUC", "M-20iA"], ["manufactures"])
        path_weak = _make_path(["FANUC", "ABB"], ["competitor_of"])
        scored = retriever._score_paths(
            [path_good, path_weak],
            seed_names=["FANUC"],
        )
        assert scored[0].relation_weight_score > scored[1].relation_weight_score

    def test_shorter_paths_score_higher(self, retriever):
        """1-hop paths score higher path_length_score than 3-hop."""
        path_short = _make_path(["FANUC", "M-20iA"], ["manufactures"])
        path_long = _make_path(
            ["FANUC", "M-20iA", "RV-40E", "MotorX"],
            ["manufactures", "uses_reducer", "uses_servo"],
        )
        scored = retriever._score_paths(
            [path_short, path_long],
            seed_names=["FANUC"],
        )
        assert scored[0].path_length_score > scored[1].path_length_score

    def test_paths_sorted_by_score_desc(self, retriever):
        """Scored paths are sorted by path_score descending."""
        paths = [
            _make_path(["A", "B"], ["competitor_of"]),
            _make_path(["FANUC", "M-20iA", "RV-40E"],
                       ["manufactures", "uses_reducer"]),
            _make_path(["X"], []),
        ]
        scored = retriever._score_paths(paths, seed_names=["FANUC"])
        for i in range(len(scored) - 1):
            assert scored[i].path_score >= scored[i + 1].path_score

    def test_empty_nodes_skipped(self, retriever):
        """Paths with empty nodes are skipped."""
        paths = [
            {"nodes": [], "edges": []},
            _make_path(["FANUC"], []),
        ]
        scored = retriever._score_paths(paths, seed_names=["FANUC"])
        assert len(scored) == 1


class TestContextBuilding:
    def test_context_fits_token_budget(self, retriever):
        """Context built within path limit."""
        retriever._max_paths = 2
        sp = ScoredPath(
            index=1, nodes=[
                {"labels": ["Robot"], "properties": {"name": "M-20iA", "payload": 20}},
            ], edges=[],
            path_score=0.9,
        )
        parts, kept = retriever._build_context([sp])
        assert len(kept) <= retriever._max_paths

    def test_low_score_paths_dropped_when_path_limit_exceeded(self, retriever):
        """Only top-N paths kept when max_paths is limited."""
        retriever._max_paths = 3
        retriever._max_tokens_est = 10000  # effectively no token limit
        scored = [
            ScoredPath(index=i, nodes=[
                {"labels": ["Robot"], "properties": {"name": f"Robot{i}"}}
            ], edges=[], path_score=0.9 - i * 0.15)
            for i in range(1, 8)
        ]
        parts, kept = retriever._build_context(scored)
        assert len(kept) == retriever._max_paths


class TestRetrievalResult:
    def test_result_structure(self):
        r = RetrievalResult(question="test")
        assert r.question == "test"
        assert r.context_used == ""
        assert r.search_results == []
        assert r.scored_paths == []
        assert r.citation_map == {}


class TestRelationWeights:
    def test_key_relations_have_high_weights(self):
        """Important relations are weighted >= 0.75."""
        key_rels = ["manufactures", "uses_reducer", "uses_servo", "applied_in"]
        for rel in key_rels:
            assert _RELATION_WEIGHTS.get(rel, 0) >= 0.75

    def test_unknown_relation_gets_default(self):
        assert DEFAULT_RELATION_WEIGHT == 0.5
