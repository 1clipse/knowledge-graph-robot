from __future__ import annotations

import pytest

from extractors.funnel import ExtractionFunnel
from extractors.llm_extractor import ExtractedEntity, ExtractionResult


class EmptyRuleExtractor:
    def extract(self, text: str) -> ExtractionResult:
        return ExtractionResult()


class FakeLLMExtractor:
    async def extract(self, text: str) -> ExtractionResult:
        return ExtractionResult(entities=[
            ExtractedEntity(name="M-20iA", type="Robot", confidence=0.6),
        ])


class FailingLLMExtractor:
    async def extract(self, text: str) -> ExtractionResult:
        raise RuntimeError("local model unavailable")


@pytest.mark.asyncio
async def test_funnel_uses_llm_to_augment_missing_local_result(monkeypatch):
    monkeypatch.setattr("extractors.spacy_extractor.SpacyExtractor.extract", lambda self, text: ExtractionResult())

    result = await ExtractionFunnel(
        rule_extractor=EmptyRuleExtractor(),
        llm_extractor=FakeLLMExtractor(),
    ).extract("FANUC M-20iA", use_llm=True)

    assert [entity.name for entity in result.entities] == ["M-20iA"]
    assert result.entities[0].confidence == 0.70


@pytest.mark.asyncio
async def test_funnel_still_returns_rule_result_when_llm_fails(monkeypatch):
    monkeypatch.setattr("extractors.spacy_extractor.SpacyExtractor.extract", lambda self, text: ExtractionResult())

    class RuleExtractor:
        def extract(self, text: str) -> ExtractionResult:
            return ExtractionResult(entities=[
                ExtractedEntity(name="FANUC", type="Manufacturer", confidence=0.95),
            ])

    result = await ExtractionFunnel(
        rule_extractor=RuleExtractor(),
        llm_extractor=FailingLLMExtractor(),
    ).extract("FANUC", use_llm=True)

    assert [entity.name for entity in result.entities] == ["FANUC"]
