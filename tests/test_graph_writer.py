from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from extractors.llm_extractor import (
    ExtractedEntity,
    ExtractedRelation,
    EntityRef,
    ExtractionResult,
)
from graph.writer import GraphWriter, WriteSummary


# ── Fixtures ────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _mock_embeddings():
    """Prevent real embedding model from loading during unit tests."""
    with patch("graph.writer.GraphWriter._generate_embeddings", return_value={}):
        yield


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.execute_write_batch.return_value = 2
    return client


@pytest.fixture
def mock_schema():
    schema = MagicMock()
    # All entity types from industrial_robot.yaml
    schema.validate_entity_type.side_effect = lambda t: t in {
        "Robot", "Manufacturer", "Component", "Reducer", "ServoMotor",
        "Controller", "Sensor", "ApplicationScenario", "Process",
        "EndEffector", "Standard", "Material", "Software",
    }
    # All relation types from schema
    valid_relations = {
        "manufactures", "supplies_component", "uses_component",
        "uses_reducer", "uses_servo", "uses_controller", "uses_sensor",
        "uses_end_effector", "applied_in", "performs_process",
        "process_requires", "process_material", "scenario_includes",
        "complies_with", "uses_software", "component_compatible",
        "contains", "competitor_of", "subsidiary_of",
    }
    schema.validate_relation_type.side_effect = lambda r: r in valid_relations

    # validate_relation checks source/target match
    relation_endpoints = {
        "manufactures": ("Manufacturer", "Robot"),
        "uses_reducer": ("Robot", "Reducer"),
        "uses_servo": ("Robot", "ServoMotor"),
        "uses_controller": ("Robot", "Controller"),
        "uses_sensor": ("Robot", "Sensor"),
        "uses_end_effector": ("Robot", "EndEffector"),
        "applied_in": ("Robot", "ApplicationScenario"),
        "performs_process": ("Robot", "Process"),
        "process_requires": ("Process", "EndEffector"),
        "process_material": ("Process", "Material"),
        "scenario_includes": ("ApplicationScenario", "Process"),
        "complies_with": ("Robot", "Standard"),
        "uses_software": ("Robot", "Software"),
        "uses_component": ("Robot", "Component"),
        "supplies_component": ("Manufacturer", "Component"),
        "component_compatible": ("Component", "Component"),
        "contains": ("Component", "Component"),
        "competitor_of": ("Manufacturer", "Manufacturer"),
        "subsidiary_of": ("Manufacturer", "Manufacturer"),
    }

    def _validate_rel(rel_type, source_type, target_type):
        if rel_type not in relation_endpoints:
            return False
        expected_src, expected_tgt = relation_endpoints[rel_type]
        return source_type == expected_src and target_type == expected_tgt

    schema.validate_relation.side_effect = _validate_rel

    # Mock get_relation_schema for error messages
    from schema.loader import RelationType
    def _get_rel_schema(rel_type):
        if rel_type in relation_endpoints:
            src, tgt = relation_endpoints[rel_type]
            return RelationType(label_zh="", label_en="", source=src, target=tgt)
        return None
    schema.get_relation_schema.side_effect = _get_rel_schema

    return schema


@pytest.fixture
def writer(mock_client, mock_schema):
    return GraphWriter(mock_client, mock_schema)


# ── Entity validation ───────────────────────────────────────────

class TestEntityValidation:
    def test_valid_entities_are_written(self, writer, mock_client):
        """合法实体可以生成写入计划并写入"""
        result = ExtractionResult(entities=[
            ExtractedEntity(name="FANUC", type="Manufacturer", confidence=0.9),
        ])
        summary = writer.write(result)
        assert summary.entities_received == 1
        assert summary.entities_written == 1
        assert summary.entities_skipped == 0
        mock_client.execute_write_batch.assert_called()

    def test_unknown_entity_type_is_skipped(self, writer, mock_client):
        """非 schema entity type 被跳过"""
        result = ExtractionResult(entities=[
            ExtractedEntity(name="FooBar", type="UnknownType", confidence=0.9),
        ])
        summary = writer.write(result)
        assert summary.entities_received == 1
        assert summary.entities_written == 0
        assert summary.entities_skipped == 1
        assert len(summary.validation_errors) == 1
        assert "UnknownType" in summary.validation_errors[0]["reason"]

    def test_mixed_valid_and_invalid(self, writer, mock_client):
        """混合实体：合法写入，非法跳过"""
        result = ExtractionResult(entities=[
            ExtractedEntity(name="Valid", type="Robot", confidence=0.9),
            ExtractedEntity(name="Invalid", type="BogusType", confidence=0.9),
        ])
        summary = writer.write(result)
        assert summary.entities_written == 1
        assert summary.entities_skipped == 1


# ── Relation validation ─────────────────────────────────────────

class TestRelationValidation:
    def test_valid_relation_is_written(self, writer, mock_client):
        """合法 relation 写入"""
        result = ExtractionResult(entities=[
            ExtractedEntity(name="FANUC", type="Manufacturer", confidence=0.9),
            ExtractedEntity(name="M-20iA", type="Robot", confidence=0.9),
        ], relations=[
            ExtractedRelation(
                source=EntityRef(name="FANUC", type="Manufacturer"),
                target=EntityRef(name="M-20iA", type="Robot"),
                relation_type="manufactures",
                confidence=0.9,
            ),
        ])
        summary = writer.write(result)
        assert summary.relations_received == 1
        assert summary.relations_written == 1
        assert summary.relations_skipped == 0

    def test_unknown_relation_type_is_skipped(self, writer, mock_client):
        """非 schema relation type 被跳过"""
        result = ExtractionResult(entities=[
            ExtractedEntity(name="A", type="Robot", confidence=0.9),
            ExtractedEntity(name="B", type="Robot", confidence=0.9),
        ], relations=[
            ExtractedRelation(
                source=EntityRef(name="A", type="Robot"),
                target=EntityRef(name="B", type="Robot"),
                relation_type="bogus_relation",
                confidence=0.9,
            ),
        ])
        summary = writer.write(result)
        assert summary.relations_skipped == 1
        assert len(summary.validation_errors) >= 1
        assert any("bogus_relation" in e.get("relation", "") for e in summary.validation_errors)

    def test_endpoint_mismatch_is_skipped(self, writer, mock_client):
        """source/target 类型不匹配的 relation 被跳过"""
        result = ExtractionResult(entities=[
            ExtractedEntity(name="FANUC", type="Manufacturer", confidence=0.9),
            ExtractedEntity(name="SensorX", type="Sensor", confidence=0.9),
        ], relations=[
            ExtractedRelation(
                source=EntityRef(name="FANUC", type="Manufacturer"),
                target=EntityRef(name="SensorX", type="Sensor"),
                relation_type="uses_reducer",  # schema says Robot -> Reducer, NOT Manufacturer -> Sensor
                confidence=0.9,
            ),
        ])
        summary = writer.write(result)
        assert summary.relations_skipped == 1
        assert len(summary.validation_errors) >= 1
        err = summary.validation_errors[0]
        assert "endpoint mismatch" in err["reason"].lower()


# ── Provenance fields ───────────────────────────────────────────

class TestProvenanceFields:
    def test_source_confidence_temporal_fields_preserved(self, writer, mock_client):
        """_source、_confidence、valid_from、valid_to 被保留在写入查询中"""
        result = ExtractionResult(entities=[
            ExtractedEntity(
                name="M-20iA", type="Robot",
                source="test_source", confidence=0.85,
                valid_from="2020", valid_to="2025",
            ),
        ])
        writer.write(result)
        # Verify the query parameters contain provenance fields
        queries = mock_client.execute_write_batch.call_args[0][0]
        _, params = queries[0]
        assert params["_source"] == "test_source"
        assert params["_confidence"] == 0.85
        assert params["valid_from"] == "2020"
        assert params["valid_to"] == "2025"

    def test_relation_provenance_fields(self, writer, mock_client):
        """Relation 的 _source, _confidence 被保留"""
        result = ExtractionResult(entities=[
            ExtractedEntity(name="FANUC", type="Manufacturer", confidence=0.9),
            ExtractedEntity(name="M-20iA", type="Robot", confidence=0.9),
        ], relations=[
            ExtractedRelation(
                source=EntityRef(name="FANUC", type="Manufacturer"),
                target=EntityRef(name="M-20iA", type="Robot"),
                relation_type="manufactures",
                source_ref="my_file.pdf",
                confidence=0.88,
                valid_from="2021", valid_to="2024",
            ),
        ])
        writer.write(result)
        queries = mock_client.execute_write_batch.call_args[0][0]
        # Relation queries are the second batch, after entity queries
        rel_queries = [q for q in queries if "MATCH (s:" in q[0]]
        _, params = rel_queries[0]
        assert params["_source"] == "my_file.pdf"
        assert params["_confidence"] == 0.88
        assert params["valid_from"] == "2021"
        assert params["valid_to"] == "2024"


# ── Embedding failure ───────────────────────────────────────────

class TestEmbeddingFailure:
    def test_embedding_failure_does_not_block_write(self, writer, mock_client):
        """Embedding 失败时不阻塞写入"""
        result = ExtractionResult(entities=[
            ExtractedEntity(name="FANUC", type="Manufacturer", confidence=0.9),
        ])
        with patch("graph.writer.GraphWriter._generate_embeddings", return_value={}):
            summary = writer.write(result)
        assert summary.entities_written == 1


# ── Empty result ────────────────────────────────────────────────

class TestEmptyResult:
    def test_empty_entities_returns_early(self, writer, mock_client):
        """空 entities 直接返回"""
        summary = writer.write(ExtractionResult())
        assert summary.entities_received == 0
        assert summary.entities_written == 0
        mock_client.execute_write_batch.assert_not_called()


# ── WriteSummary ────────────────────────────────────────────────

class TestWriteSummary:
    def test_summary_counts(self):
        s = WriteSummary(
            entities_received=5, entities_written=4, entities_skipped=1,
            relations_received=3, relations_written=2, relations_skipped=1,
        )
        assert s.total_written == 6

    def test_validation_errors_accumulated(self, writer, mock_client):
        """validation_errors 累积实体和关系两阶段的错误"""
        result = ExtractionResult(entities=[
            ExtractedEntity(name="Bad", type="NotAType", confidence=0.9),
        ], relations=[
            ExtractedRelation(
                source=EntityRef(name="X", type="Robot"),
                target=EntityRef(name="Y", type="Sensor"),
                relation_type="uses_reducer",
                confidence=0.9,
            ),
        ])
        summary = writer.write(result)
        # Both entity and relation errors
        assert len(summary.validation_errors) >= 1
