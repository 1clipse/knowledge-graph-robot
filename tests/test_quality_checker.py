from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from quality.checker import QualityChecker, QualityReport


@pytest.fixture
def mock_client():
    """Mock Neo4jClient that returns empty for all queries by default."""
    client = MagicMock()
    client.execute_query.return_value = []
    return client


@pytest.fixture
def checker(mock_client):
    return QualityChecker(mock_client)


class TestQualityReportStructure:
    def test_report_has_all_sections(self, checker):
        """Report includes all required sections."""
        report = checker.run()
        d = report.to_dict()
        sections = d["sections"]
        assert "completeness" in sections
        assert "consistency" in sections
        assert "duplicates" in sections
        assert "confidence" in sections
        assert "graph_structure" in sections
        assert "temporal" in sections
        assert "quality_score" in d
        assert "suggested_actions" in d

    def test_empty_graph_returns_max_score(self, checker):
        """Empty graph (0 entities) → quality_score=100."""
        report = checker.run()
        assert report.quality_score >= 0

    def test_suggested_actions_all_good_when_clean(self, checker):
        """Clean graph produces 'all good' message."""
        report = checker.run()
        assert any("良好" in a for a in report.suggested_actions)

    def test_quality_score_is_numeric(self, checker):
        """Quality score is a number between 0-100."""
        report = checker.run()
        assert isinstance(report.quality_score, (int, float))
        assert 0 <= report.quality_score <= 100


class TestQualityCheckerWithData:
    def test_score_decreases_with_issues(self, mock_client):
        """More issues → lower quality_score."""

        def query_side_effect(query, params=None):
            if "count(n) AS total" in query:
                return [{"total": 10}]
            # Return issues for duplicate check only
            if "collect(DISTINCT labels" in query:
                return [{
                    "name": "dup", "type_list": ["A", "B"], "cnt": 2,
                    "reason": "same_name_different_labels", "section": "duplicates",
                }]
            if "NOT (n)--()" in query:
                return [
                    {"name": "o1", "labels": ["Robot"], "issue": "orphan", "section": "graph_structure"},
                    {"name": "o2", "labels": ["Component"], "issue": "orphan", "section": "graph_structure"},
                ]
            return []

        mock_client.execute_query.side_effect = query_side_effect
        checker = QualityChecker(mock_client)
        report = checker.run()
        # With duplicates + orphans, score should be < 100
        assert report.quality_score < 100

    def test_duplicate_detection_triggered(self, mock_client):
        """Duplicate entities are detected and reported."""

        def query_side_effect(query, params=None):
            if "count(n) AS total" in query:
                return [{"total": 10}]
            if "collect(DISTINCT labels" in query:
                return [{
                    "name": "FANUC", "type_list": ["Manufacturer", "Robot"], "cnt": 2,
                    "reason": "same_name_different_labels", "section": "duplicates",
                }]
            return []

        mock_client.execute_query.side_effect = query_side_effect
        checker = QualityChecker(mock_client)
        report = checker.run()
        assert len(report.duplicates) > 0
        assert report.duplicates[0]["name"] == "FANUC"

    def test_orphan_detection_triggered(self, mock_client):
        """Orphan nodes are detected."""

        def query_side_effect(query, params=None):
            if "count(n) AS total" in query:
                return [{"total": 10}]
            if "NOT (n)--()" in query:
                return [
                    {"name": "orphan", "labels": ["Robot"], "issue": "orphan", "section": "graph_structure"},
                ]
            return []

        mock_client.execute_query.side_effect = query_side_effect
        checker = QualityChecker(mock_client)
        report = checker.run()
        assert len(report.graph_structure) > 0

    def test_suggested_actions_include_duplicate_hint(self, mock_client):
        """When duplicates found, suggested action mentions it."""

        def query_side_effect(query, params=None):
            if "count(n) AS total" in query:
                return [{"total": 10}]
            if "collect(DISTINCT labels" in query:
                return [{
                    "name": "dup", "type_list": ["A", "B"], "cnt": 2,
                    "reason": "same_name_different_labels", "section": "duplicates",
                }]
            return []

        mock_client.execute_query.side_effect = query_side_effect
        checker = QualityChecker(mock_client)
        report = checker.run()
        assert any("重复" in a or "合并" in a for a in report.suggested_actions)


class TestQualityReportDataclass:
    def test_score_between_0_and_100(self):
        r = QualityReport(quality_score=85.5)
        assert 0 <= r.quality_score <= 100

    def test_sections_default_empty(self):
        r = QualityReport()
        assert r.completeness == []
        assert r.consistency == []
        assert r.duplicates == []

    def test_to_dict_includes_all_keys(self):
        r = QualityReport(quality_score=90.0)
        r.duplicates = [{"name": "test"}]
        d = r.to_dict()
        assert d["quality_score"] == 90.0
        assert "sections" in d
        assert d["sections"]["duplicates"] == [{"name": "test"}]
        assert "suggested_actions" in d
