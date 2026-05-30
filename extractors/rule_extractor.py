from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from extractors.llm_extractor import ExtractedEntity, ExtractedRelation, EntityRef, ExtractionResult
from extractors.patterns import get_entity_patterns, get_relation_patterns

# Entity and relation patterns — single source of truth in extractors.patterns
_ROBOT_PATTERNS = get_entity_patterns()
_RELATION_PATTERNS = get_relation_patterns()

_NUMERIC_PATTERN = re.compile(r"[\d.]+")


class RuleExtractor:
    def __init__(self) -> None:
        self._entity_patterns = _ROBOT_PATTERNS
        self._relation_patterns = _RELATION_PATTERNS

    def extract(self, text: str) -> ExtractionResult:
        if not text.strip():
            return ExtractionResult()
        entities = self._extract_entities(text)
        relations = self._extract_relations(text)
        logger.info(
            f"Rule extraction: {len(entities)} entities, {len(relations)} relations"
        )
        return ExtractionResult(entities=entities, relations=relations)

    def _extract_entities(self, text: str) -> List[ExtractedEntity]:
        found: Dict[str, ExtractedEntity] = {}

        for pattern_group in self._entity_patterns:
            entity_type = pattern_group["type"]
            for pattern in pattern_group["patterns"]:
                for match in re.finditer(pattern, text, re.IGNORECASE):
                    groups = match.groupdict()
                    name = groups.get("name", "").strip()
                    if not name:
                        continue
                    properties: Dict[str, Any] = {}
                    for prop_key, (mapped_key, converter) in pattern_group.get(
                        "property_map", {}
                    ).items():
                        value = groups.get(prop_key, "")
                        if value:
                            try:
                                properties[mapped_key] = converter(value)
                            except (ValueError, TypeError):
                                properties[mapped_key] = value

                    key = f"{entity_type}::{name}"
                    if key in found:
                        existing = found[key]
                        for k, v in properties.items():
                            if k not in existing.properties:
                                existing.properties[k] = v
                    else:
                        found[key] = ExtractedEntity(
                            name=name, type=entity_type, properties=properties, confidence=0.95
                        )

        return list(found.values())

    def _extract_relations(self, text: str) -> List[ExtractedRelation]:
        relations: List[ExtractedRelation] = []

        for rel_pattern in self._relation_patterns:
            for match in re.finditer(rel_pattern["pattern"], text, re.IGNORECASE):
                groups = match.groupdict()
                source_name = groups.get("source_name", "").strip()
                target_name = groups.get("target_name", "").strip()
                if not source_name or not target_name:
                    continue
                relations.append(
                    ExtractedRelation(
                        source=EntityRef(
                            name=source_name, type=rel_pattern["source_type"]
                        ),
                        target=EntityRef(
                            name=target_name, type=rel_pattern["target_type"]
                        ),
                        relation_type=rel_pattern["relation_type"],
                        properties={},
                        confidence=0.95,
                    )
                )

        return relations

    def extract_spec_table(self, text: str) -> List[ExtractedEntity]:
        entities: List[ExtractedEntity] = []
        spec_patterns: Dict[str, List[Tuple[str, str, Any]]] = {
            "Robot": [
                (r"负载[：:]?\s*([\d.]+)\s*kg", "payload", float),
                (r"臂展[：:]?\s*([\d.]+)\s*mm", "reach", float),
                (r"重复定位精度[：:]?\s*[±+]?\s*([\d.]+)\s*mm", "repeatability", float),
                (r"(\d+)\s*轴", "axes", int),
                (r"重量[：:]?\s*([\d.]+)\s*kg", "weight", float),
                (r"防护等级[：:]?\s*(IP\d+)", "protection_class", str),
            ],
            "Reducer": [
                (r"减速比[：:]?\s*([\d:]+)", "reduction_ratio", str),
                (r"额定扭矩[：:]?\s*([\d.]+)\s*N·?m", "rated_torque", float),
                (r"回程间隙[：:]?\s*([\d.]+)\s*arcmin", "backlash", float),
            ],
            "ServoMotor": [
                (r"额定功率[：:]?\s*([\d.]+)\s*kW", "rated_power", float),
                (r"额定扭矩[：:]?\s*([\d.]+)\s*N·?m", "rated_torque", float),
                (r"额定转速[：:]?\s*([\d.]+)\s*r/min", "rated_speed", float),
            ],
        }

        for entity_type, patterns in spec_patterns.items():
            props: Dict[str, Any] = {}
            for pattern, prop_name, converter in patterns:
                match = re.search(pattern, text)
                if match:
                    try:
                        props[prop_name] = converter(match.group(1))
                    except (ValueError, TypeError):
                        pass
            if props:
                entities.append(
                    ExtractedEntity(name="", type=entity_type, properties=props)
                )

        return entities
