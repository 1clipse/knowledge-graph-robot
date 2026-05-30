from __future__ import annotations

from typing import Dict, Set, Tuple

from loguru import logger

from extractors.llm_extractor import (
    LLMExtractor,
    ExtractionResult,
    ExtractedEntity,
    ExtractedRelation,
)
from extractors.rule_extractor import RuleExtractor


class ExtractionFunnel:
    """Shared 4-tier extraction funnel: Rule → spaCy → merge → LLM augment."""

    def __init__(
        self,
        rule_extractor: RuleExtractor | None = None,
        llm_extractor: LLMExtractor | None = None,
    ) -> None:
        self._rule_extractor = rule_extractor or RuleExtractor()
        self._llm_extractor = llm_extractor

    async def extract(self, text: str, use_llm: bool = True) -> ExtractionResult:
        result = ExtractionResult()

        # Tier 2: Rule-based extraction (always run — fast & precise)
        if text.strip():
            try:
                result = self._rule_extractor.extract(text)
                for entity in result.entities:
                    entity.confidence = entity.confidence or 0.95
                for relation in result.relations:
                    relation.confidence = relation.confidence or 0.95
            except Exception as e:
                logger.warning(f"Rule extraction failed: {e}")

        # Tier 3: spaCy extraction (adds recall, catches rule misses)
        if text.strip():
            try:
                from extractors.spacy_extractor import SpacyExtractor

                spacy_result = SpacyExtractor().extract(text)
                result = merge_results(result, spacy_result)
            except Exception as e:
                logger.warning(f"spaCy extraction failed: {e}, continuing without it")

        # Tier 4: LLM augments low-confidence / missing areas
        llm_text = text
        if len(text) > 4000:
            llm_text = text[:3500] + "\n...(文本过长已截断，完整内容由规则引擎处理)"
            logger.info(f"File text truncated for LLM: {len(text)} -> {len(llm_text)} chars")

        low_conf_entities = [entity for entity in result.entities if entity.confidence < 0.7]
        has_missing = len(result.entities) < 3

        if use_llm and llm_text.strip() and (low_conf_entities or has_missing):
            try:
                extractor = self._llm_extractor or LLMExtractor()
                llm_result = await extractor.extract(llm_text)
                result = augment_low_confidence(result, llm_result)
            except Exception as e:
                logger.error(f"LLM augmentation failed: {e}")

        return result


def merge_results(a: ExtractionResult, b: ExtractionResult) -> ExtractionResult:
    """Merge two extraction results, keeping highest-confidence entries."""
    entity_map: Dict[str, ExtractedEntity] = {}
    for entity in a.entities:
        key = f"{entity.type}::{entity.name}"
        entity_map[key] = entity
    for entity in b.entities:
        key = f"{entity.type}::{entity.name}"
        if key in entity_map:
            existing = entity_map[key]
            existing.confidence = max(existing.confidence, entity.confidence)
            for prop_key, prop_value in (entity.properties or {}).items():
                if prop_key not in (existing.properties or {}):
                    existing.properties[prop_key] = prop_value
        else:
            entity_map[key] = entity

    rel_set: Set[Tuple[str, str, str]] = set()
    merged_rels: list[ExtractedRelation] = []
    for relation in list(a.relations) + list(b.relations):
        key = (relation.source.name, relation.relation_type, relation.target.name)
        if key not in rel_set:
            rel_set.add(key)
            merged_rels.append(relation)

    return ExtractionResult(entities=list(entity_map.values()), relations=merged_rels)


def augment_low_confidence(
    merged: ExtractionResult,
    llm_result: ExtractionResult,
) -> ExtractionResult:
    """Use LLM entities to fill gaps in merged result (low-conf or missing types)."""
    merged_keys: Set[str] = {f"{entity.type}::{entity.name}" for entity in merged.entities}

    for entity in llm_result.entities:
        key = f"{entity.type}::{entity.name}"
        if key not in merged_keys:
            entity.confidence = max(entity.confidence, 0.70)
            merged.entities.append(entity)
            merged_keys.add(key)

    rel_set: Set[Tuple[str, str, str]] = {
        (relation.source.name, relation.relation_type, relation.target.name)
        for relation in merged.relations
    }
    for relation in llm_result.relations:
        key = (relation.source.name, relation.relation_type, relation.target.name)
        if key not in rel_set:
            relation.confidence = max(relation.confidence, 0.70)
            merged.relations.append(relation)
            rel_set.add(key)

    return merged
