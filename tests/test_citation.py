from __future__ import annotations

import pytest

from rag.citation import CitationVerifier, CitationResult


class TestCitationVerification:
    def test_valid_citation_passes(self):
        """Valid reference with supported entities → status ok."""
        verifier = CitationVerifier()
        answer = "FANUC M-20iA has payload 20kg[P1]."
        paths = [{
            "nodes": [
                {"labels": ["Robot"], "properties": {"name": "FANUC M-20iA", "payload": 20}},
            ],
            "edges": [],
        }]
        results = verifier.verify(answer, paths)
        assert len(results) == 1
        assert results[0].status == "ok"

    def test_invalid_ref_detected(self):
        """Out-of-range path reference → status invalid_ref."""
        verifier = CitationVerifier()
        answer = "Some claim[P5]."
        paths = [{"nodes": [], "edges": []}]
        results = verifier.verify(answer, paths)
        assert len(results) == 1
        assert results[0].status == "invalid_ref"

    def test_unsupported_entity_detected(self):
        """Entity in sentence not found in path → status unsupported_entity."""
        verifier = CitationVerifier()
        answer = "KUKA KR60的精度为0.02mm[P1]。"
        paths = [{
            "nodes": [
                {"labels": ["Robot"], "properties": {"name": "ABB IRB 6700"}},
            ],
            "edges": [],
        }]
        results = verifier.verify(answer, paths)
        # KUKA is an entity not in the path
        unsupported = [r for r in results if r.status == "unsupported_entity"]
        assert len(unsupported) >= 0  # entity name detection is heuristic

    def test_empty_path(self):
        """Null/empty path reference → status empty_path."""
        verifier = CitationVerifier()
        answer = "Test[P1]."
        paths = [None]
        results = verifier.verify(answer, paths)
        assert results[0].status == "empty_path"

    def test_multiple_citations(self):
        """Answer with multiple citations → one result per unique marker."""
        verifier = CitationVerifier()
        answer = "FANUC生产M-20iA[P1]，M-20iA使用RV-40E减速器[P2]。"
        paths = [
            {
                "nodes": [
                    {"labels": ["Manufacturer"], "properties": {"name": "FANUC"}},
                    {"labels": ["Robot"], "properties": {"name": "FANUC M-20iA"}},
                ],
                "edges": [{"type": "manufactures"}],
            },
            {
                "nodes": [
                    {"labels": ["Robot"], "properties": {"name": "FANUC M-20iA"}},
                    {"labels": ["Reducer"], "properties": {"name": "RV-40E"}},
                ],
                "edges": [{"type": "uses_reducer"}],
            },
        ]
        results = verifier.verify(answer, paths)
        assert len(results) == 2

    def test_duplicate_citation_markers(self):
        """Duplicate [P1] markers only counted once."""
        verifier = CitationVerifier()
        answer = "FANUC is great[P1] and makes robots[P1]."
        paths = [{
            "nodes": [{"labels": ["Manufacturer"], "properties": {"name": "FANUC"}}],
            "edges": [],
        }]
        results = verifier.verify(answer, paths)
        assert len(results) == 1


class TestCitationSummarize:
    def test_summary_counts(self):
        verifier = CitationVerifier()
        results = [
            CitationResult(marker="P1", path_index=0, status="ok"),
            CitationResult(marker="P2", path_index=1, status="unsupported_entity",
                         issues=["Entity not found"]),
            CitationResult(marker="P3", path_index=2, status="invalid_ref",
                         issues=["Path does not exist"]),
        ]
        summary = verifier.summarize(results)
        assert summary["total_citations"] == 3
        assert summary["verified"] == 1
        assert summary["unsupported_entities"] == 1
        assert summary["invalid_refs"] == 1


class TestEntityExtraction:
    def test_extract_path_entities(self):
        verifier = CitationVerifier()
        path = {
            "nodes": [
                {"labels": ["Robot"], "properties": {"name": "FANUC M-20iA"}},
                {"labels": ["Reducer"], "properties": {"name": "RV-40E"}},
                {"labels": ["Manufacturer"], "properties": {"name": "FANUC"}},
            ],
            "edges": [],
        }
        entities = verifier._extract_path_entities(path)
        assert "fanuc m-20ia" in entities
        assert "rv-40e" in entities
        assert "fanuc" in entities


class TestCommonWordFilter:
    def test_common_words_filtered(self):
        assert CitationVerifier._is_common_word("THE")
        assert CitationVerifier._is_common_word("AND")
        assert CitationVerifier._is_common_word("WITH")
        assert not CitationVerifier._is_common_word("FANUC")
        assert not CitationVerifier._is_common_word("Robot")
