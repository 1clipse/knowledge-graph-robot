from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from graph.reasoner import Reasoner, DEFAULT_RULES


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.execute_query.return_value = []
    return client


@pytest.fixture
def reasoner(mock_client):
    rules = {
        "subclass_of": DEFAULT_RULES["subclass_of"],
        "transitive_relations": ["contains"],
        "symmetric_relations": ["competitor_of", "component_compatible"],
        "inverse_pairs": [["manufactures", "manufactured_by"]],
    }
    return Reasoner(mock_client, rules=rules)


class TestReasonerInit:
    def test_loads_default_rules_when_none_provided(self, mock_client):
        r = Reasoner(mock_client, rules=None)
        assert "subclass_of" in r._rules
        assert "transitive_relations" in r._rules
        assert "symmetric_relations" in r._rules

    def test_uses_explicit_rules(self, mock_client):
        custom = {
            "subclass_of": {},
            "transitive_relations": ["custom_rel"],
            "symmetric_relations": [],
            "inverse_pairs": [],
        }
        r = Reasoner(mock_client, rules=custom)
        assert r._rules["transitive_relations"] == ["custom_rel"]


class TestReasonerInfer:
    def test_infer_returns_stats_dict(self, reasoner):
        stats = reasoner.infer(dry_run=True)
        assert isinstance(stats, dict)
        assert "symmetric_inferred" in stats
        assert "transitive_inferred" in stats
        assert "inverse_inferred" in stats
        assert "subclass_inferences" in stats

    def test_dry_run_does_not_execute_writes(self, reasoner, mock_client):
        mock_client.execute_query.return_value = [
            {"source": "FANUC", "target": "ABB"},
        ]
        reasoner.infer(dry_run=True)
        mock_client.execute_write.assert_not_called()

    def test_real_run_executes_writes(self, mock_client):
        mock_client.execute_query.return_value = [
            {"source": "FANUC", "target": "ABB", "name": "FANUC"},
        ]
        rules = {
            "subclass_of": DEFAULT_RULES["subclass_of"],
            "transitive_relations": [],
            "symmetric_relations": ["competitor_of"],
            "inverse_pairs": [],
        }
        r = Reasoner(mock_client, rules=rules)
        r.infer(dry_run=False)
        assert mock_client.execute_write.call_count >= 1


class TestReasonerSymmetric:
    def test_symmetric_inference_count(self, reasoner, mock_client):
        # 2 schema-backed symmetric relations × 1 record each = 2
        mock_client.execute_query.return_value = [
            {"source": "A", "target": "B"},
        ]
        count = reasoner._infer_symmetric(dry_run=True)
        assert count == 2

    def test_symmetric_no_rows(self, reasoner, mock_client):
        mock_client.execute_query.return_value = []
        count = reasoner._infer_symmetric(dry_run=True)
        assert count == 0


class TestReasonerTransitive:
    def test_transitive_inference_count(self, reasoner, mock_client):
        mock_client.execute_query.return_value = [
            {"source": "A", "mid": "B", "target": "C"},
        ]
        count = reasoner._infer_transitive(dry_run=True)
        assert count == 1


class TestReasonerSubclass:
    def test_subclass_inference_count(self, reasoner, mock_client):
        mock_client.execute_query.return_value = [
            {"name": "M-20iA"},
            {"name": "IRB 6700"},
        ]
        count = reasoner._infer_subclass(dry_run=True)
        assert count >= 0


class TestReasonerInverse:
    def test_inverse_inference_count(self, reasoner, mock_client):
        mock_client.execute_query.return_value = [
            {"source": "FANUC", "target": "M-20iA"},
        ]
        count = reasoner._infer_inverse(dry_run=True)
        assert count == 1


class TestReasonerLabelScopedMatching:
    def test_symmetric_write_uses_schema_labels(self, mock_client):
        mock_client.execute_query.return_value = [
            {"source": "FANUC", "target": "ABB"},
        ]
        r = Reasoner(
            mock_client,
            rules={
                "subclass_of": {},
                "transitive_relations": [],
                "symmetric_relations": ["competitor_of"],
                "inverse_pairs": [],
            },
        )

        count = r._infer_symmetric(dry_run=False)

        assert count == 1
        query = mock_client.execute_write.call_args[0][0]
        params = mock_client.execute_write.call_args[0][1]
        assert "MATCH (a:`Manufacturer`" in query
        assert "(b:`Manufacturer`" in query
        assert "MATCH (a {name: $source})" not in query
        assert params == {"source": "FANUC", "target": "ABB"}

    def test_transitive_write_uses_schema_labels(self, mock_client):
        mock_client.execute_query.return_value = [
            {"source": "AssemblyA", "mid": "AssemblyB", "target": "AssemblyC"},
        ]
        r = Reasoner(
            mock_client,
            rules={
                "subclass_of": {},
                "transitive_relations": ["assembly_contains_sub"],
                "symmetric_relations": [],
                "inverse_pairs": [],
            },
        )

        count = r._infer_transitive(dry_run=False)

        assert count == 1
        query = mock_client.execute_write.call_args[0][0]
        params = mock_client.execute_write.call_args[0][1]
        assert "MATCH (a:`Assembly`" in query
        assert "(c:`Assembly`" in query
        assert "MATCH (a {name: $source})" not in query
        assert params == {"source": "AssemblyA", "target": "AssemblyC"}

    def test_inverse_write_uses_forward_relation_schema_labels(self, mock_client):
        mock_client.execute_query.return_value = [
            {"source": "FANUC", "target": "M-20iA"},
        ]
        r = Reasoner(
            mock_client,
            rules={
                "subclass_of": {},
                "transitive_relations": [],
                "symmetric_relations": [],
                "inverse_pairs": [["manufactures", "manufactured_by"]],
            },
        )

        count = r._infer_inverse(dry_run=False)

        assert count == 1
        query = mock_client.execute_write.call_args[0][0]
        params = mock_client.execute_write.call_args[0][1]
        assert "MATCH (a:`Manufacturer`" in query
        assert "(b:`Robot`" in query
        assert "MATCH (a {name: $source})" not in query
        assert params == {"source": "FANUC", "target": "M-20iA"}

    def test_relation_without_schema_endpoint_is_skipped(self, mock_client):
        mock_client.execute_query.return_value = [
            {"source": "A", "target": "B"},
        ]
        r = Reasoner(
            mock_client,
            rules={
                "subclass_of": {},
                "transitive_relations": ["part_of"],
                "symmetric_relations": ["same_as"],
                "inverse_pairs": [],
            },
        )

        assert r._infer_symmetric(dry_run=False) == 0
        assert r._infer_transitive(dry_run=False) == 0
        mock_client.execute_write.assert_not_called()


class TestGetInferableRelations:
    def test_returns_dict_with_keys(self, reasoner):
        info = reasoner.get_inferable_relations()
        assert "subclass_of" in info
        assert "transitive_count" in info
        assert "symmetric_count" in info
        assert "inverse_pair_count" in info


class TestDefaultRules:
    def test_default_rules_structure(self):
        assert "subclass_of" in DEFAULT_RULES
        assert "transitive_relations" in DEFAULT_RULES
        assert "symmetric_relations" in DEFAULT_RULES

    def test_default_subclass_maps_to_component(self):
        for child in ["ServoMotor", "Reducer", "Controller", "Sensor", "EndEffector"]:
            assert child in DEFAULT_RULES["subclass_of"]
            assert DEFAULT_RULES["subclass_of"][child] == "Component"
