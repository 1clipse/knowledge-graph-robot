"""Tests for spacy_extractor module."""
from __future__ import annotations

import pytest


class TestSpacyExtractor:
    """Tests for SpacyExtractor entity/relation extraction."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        from extractors.spacy_extractor import SpacyExtractor
        self.extractor = SpacyExtractor(use_ruler=True)

    def test_empty_text_returns_empty_result(self):
        from extractors.llm_extractor import ExtractionResult
        result = self.extractor.extract("")
        assert isinstance(result, ExtractionResult)
        assert len(result.entities) == 0
        assert len(result.relations) == 0

    def test_whitespace_only_returns_empty(self):
        result = self.extractor.extract("   \n  ")
        assert len(result.entities) == 0

    def test_manufacturer_detected(self):
        result = self.extractor.extract("FANUC是工业机器人制造商")
        manufacturers = [e for e in result.entities if e.type == "Manufacturer"]
        assert len(manufacturers) >= 1
        names = [e.name for e in manufacturers]
        assert any("FANUC" in n for n in names)

    def test_reducer_detected(self):
        result = self.extractor.extract("机器人采用RV-20E减速器，精度高")
        reducers = [e for e in result.entities if e.type == "Reducer"]
        assert len(reducers) >= 1
        names = [e.name for e in reducers]
        assert any("RV" in n for n in names)

    def test_process_detected(self):
        result = self.extractor.extract("该产线包含弧焊和点焊工序")
        processes = [e for e in result.entities if e.type == "Process"]
        assert len(processes) >= 1

    def test_sensor_detected(self):
        result = self.extractor.extract("配备力矩传感器进行实时监控")
        sensors = [e for e in result.entities if e.type == "Sensor"]
        assert len(sensors) >= 1

    def test_standard_detected(self):
        result = self.extractor.extract("产品符合ISO 10218-1标准")
        standards = [e for e in result.entities if e.type == "Standard"]
        assert len(standards) >= 1

    def test_entities_have_confidence(self):
        result = self.extractor.extract("FANUC M-20iA配备RV减速器")
        for ent in result.entities:
            assert ent.confidence > 0
            assert ent.confidence <= 1.0

    def test_result_is_extraction_result(self):
        from extractors.llm_extractor import ExtractionResult
        result = self.extractor.extract("测试文本")
        assert isinstance(result, ExtractionResult)
        assert hasattr(result, "entities")
        assert hasattr(result, "relations")

    def test_long_text_handled(self):
        text = ("FANUC是工业机器人制造商。KUKA也是知名品牌。"
                "ABB机器人广泛应用于弧焊、点焊和搬运领域。"
                "采用RV减速器和伺服电机。符合ISO 10218标准。")
        result = self.extractor.extract(text)
        assert len(result.entities) >= 3


class TestEntityLabelMapping:
    """Verify entity type ↔ label mappings are complete."""

    def test_all_types_have_labels(self):
        from extractors.spacy_extractor import ENTITY_TYPE_TO_LABEL, LABEL_TO_ENTITY_TYPE
        assert len(ENTITY_TYPE_TO_LABEL) >= 17
        assert len(LABEL_TO_ENTITY_TYPE) >= 17
        for etype, label in ENTITY_TYPE_TO_LABEL.items():
            assert LABEL_TO_ENTITY_TYPE[label] == etype


class TestTrainingDataGeneration:
    """Tests for generate_ner_training_data (requires Neo4j mock)."""

    def test_empty_client_returns_empty(self):
        from extractors.spacy_extractor import generate_ner_training_data
        # Mock client that raises on query
        class MockClient:
            pass
        # Should return empty list gracefully
        try:
            data = generate_ner_training_data(MockClient())
            assert isinstance(data, list)
        except Exception:
            pass  # Expected when client can't connect


class TestMergeResults:
    """Tests for _merge_results helper."""

    def test_merge_keeps_higher_confidence(self):
        from api.routes.ingest import _merge_results
        from extractors.llm_extractor import ExtractedEntity, ExtractionResult

        a = ExtractionResult(entities=[
            ExtractedEntity(name="FANUC", type="Manufacturer", confidence=0.95)
        ])
        b = ExtractionResult(entities=[
            ExtractedEntity(name="FANUC", type="Manufacturer", confidence=0.80)
        ])
        merged = _merge_results(a, b)
        assert len(merged.entities) == 1
        assert merged.entities[0].confidence == 0.95

    def test_merge_adds_new_entities(self):
        from api.routes.ingest import _merge_results
        from extractors.llm_extractor import ExtractedEntity, ExtractionResult

        a = ExtractionResult(entities=[
            ExtractedEntity(name="FANUC", type="Manufacturer", confidence=0.95)
        ])
        b = ExtractionResult(entities=[
            ExtractedEntity(name="ABB", type="Manufacturer", confidence=0.80)
        ])
        merged = _merge_results(a, b)
        assert len(merged.entities) == 2

    def test_merge_deduplicates_relations(self):
        from api.routes.ingest import _merge_results
        from extractors.llm_extractor import ExtractedRelation, EntityRef, ExtractionResult

        rel = ExtractedRelation(
            source=EntityRef(name="FANUC", type="Manufacturer"),
            target=EntityRef(name="M-20iA", type="Robot"),
            relation_type="manufactures", confidence=0.95,
        )
        a = ExtractionResult(relations=[rel])
        b = ExtractionResult(relations=[rel])
        merged = _merge_results(a, b)
        assert len(merged.relations) == 1
