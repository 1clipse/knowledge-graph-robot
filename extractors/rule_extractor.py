from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from extractors.llm_extractor import ExtractedEntity, ExtractedRelation, EntityRef, ExtractionResult


_ROBOT_PATTERNS: List[Dict[str, Any]] = [
    {
        "type": "Robot",
        "patterns": [
            r"(?P<name>[A-Z][\w\-]+\s+[\w\-]+(?:机器人)?)\s*(?:是|为|是一款|是一台|是一型)\s*(?P<axes>\d+)\s*轴",
            r"(?P<name>[A-Z][\w\-]+\s*[\w\-]*)\s*(?:负载|额定负载|有效负载)[：:]?\s*(?P<payload>[\d.]+)\s*kg",
            r"(?P<name>[A-Z][\w\-]+\s*[\w\-]*)\s*(?:臂展|最大臂展|工作半径|reach)[：:]?\s*(?P<reach>[\d.]+)\s*mm",
        ],
        "property_map": {
            "axes": ("axes", int),
            "payload": ("payload", float),
            "reach": ("reach", float),
        },
    },
    {
        "type": "Manufacturer",
        "patterns": [
            r"(?P<name>FANUC|ABB|KUKA|安川|Yaskawa|川崎|Kawasaki|爱普生|Epson|史陶比尔|Stäubli|柯马|Comau|那智不二越|Nachi|优傲|Universal Robots|UR|埃斯顿|Estun|汇川|新松)",
            r"(?P<name>[\u4e00-\u9fff]+(?:公司|集团|股份|有限))\s*(?:推出|发布|研制|生产|制造)",
        ],
        "property_map": {},
    },
    {
        "type": "Reducer",
        "patterns": [
            r"(?P<name>RV[\-]?\d+[A-Z]*)\s*(?:减速器|RV减速器)",
            r"(?P<name>谐波减速器|SHG[\-]?\d+[A-Z]*)",
            r"(?P<reducer_type>RV减速器|谐波减速器|行星减速器)\s*(?:型号|规格)[：:]?\s*(?P<name>[\w\-]+)",
        ],
        "property_map": {
            "reducer_type": ("reducer_type", str),
        },
    },
    {
        "type": "ServoMotor",
        "patterns": [
            r"(?P<name>[\w\-]+(?:伺服电机|伺服马达))",
            r"(?:伺服电机|伺服马达)\s*(?:型号|规格)[：:]?\s*(?P<name>[\w\-]+)",
        ],
        "property_map": {},
    },
    {
        "type": "Controller",
        "patterns": [
            r"(?P<name>R[\-]?\d+[a-zA-Z]*\s*(?:Plus|iB|Mate)?)\s*(?:控制器|控制系统)",
            r"(?:控制器|控制系统)\s*(?:型号)[：:]?\s*(?P<name>[\w\-]+)",
        ],
        "property_map": {},
    },
    {
        "type": "ApplicationScenario",
        "patterns": [
            r"(?:应用于|用于|适用|广泛应用于)\s*(?P<name>[\u4e00-\u9fff]+(?:焊接|搬运|装配|喷涂|打磨|抛光|码垛|切割|检测|包装|上下料))",
        ],
        "property_map": {},
    },
    {
        "type": "Process",
        "patterns": [
            r"(?P<name>点焊|弧焊|激光焊|螺柱焊|涂胶|喷涂|搬运|码垛|装配|打磨|抛光|切割|冲压|注塑|机加工|检测|包装)",
        ],
        "property_map": {},
    },
    {
        "type": "EndEffector",
        "patterns": [
            r"(?P<name>[\u4e00-\u9fff]*(?:焊枪|夹爪|吸盘|喷枪|打磨头|切割头|夹具|抓手))",
            r"(?:末端执行器|末端工具|末端夹具)\s*(?:型号|类型)[：:]?\s*(?P<name>[\w\-]+)",
        ],
        "property_map": {},
    },
    {
        "type": "Sensor",
        "patterns": [
            r"(?P<name>[\u4e00-\u9fff]*(?:力矩传感器|力传感器|视觉传感器|碰撞传感器|位置传感器|安全传感器|2D视觉|3D视觉))",
            r"(?P<sensor_type>力矩|力|视觉|碰撞|位置|安全)\s*传感器\s*(?:型号)[：:]?\s*(?P<name>[\w\-]+)",
        ],
        "property_map": {
            "sensor_type": ("sensor_type", str),
        },
    },
    {
        "type": "Standard",
        "patterns": [
            r"(?P<name>(?:ISO|GB|IEC|EN|DIN|JIS)\s*[\d\-\.]+(?:[\u4e00-\u9fff\w]*))",
        ],
        "property_map": {},
    },
]

_RELATION_PATTERNS: List[Dict[str, Any]] = [
    {
        "relation_type": "manufactures",
        "pattern": r"(?P<source_name>FANUC|ABB|KUKA|安川|Yaskawa|川崎|爱普生|埃斯顿|汇川|新松|[\u4e00-\u9fff]+(?:公司|集团))\s*(?:推出|发布|生产|制造|研制)\s*(?:了?\s*)?(?P<target_name>[\w\-]+(?:机器人)?)",
        "source_type": "Manufacturer",
        "target_type": "Robot",
    },
    {
        "relation_type": "uses_reducer",
        "pattern": r"(?P<source_name>[\w\-]+(?:机器人)?)\s*(?:采用|使用|配备|搭载)\s*(?P<target_name>[\w\-]*(?:RV|谐波)?减速器[\w\-]*)",
        "source_type": "Robot",
        "target_type": "Reducer",
    },
    {
        "relation_type": "uses_servo",
        "pattern": r"(?P<source_name>[\w\-]+(?:机器人)?)\s*(?:采用|使用|配备|搭载)\s*(?P<target_name>[\w\-]*(?:伺服电机|伺服马达))",
        "source_type": "Robot",
        "target_type": "ServoMotor",
    },
    {
        "relation_type": "uses_controller",
        "pattern": r"(?P<source_name>[\w\-]+(?:机器人)?)\s*(?:搭配|配合|使用|配备)\s*(?P<target_name>[\w\-]*(?:控制器|控制系统))",
        "source_type": "Robot",
        "target_type": "Controller",
    },
    {
        "relation_type": "applied_in",
        "pattern": r"(?P<source_name>[\w\-]+(?:机器人)?)\s*(?:应用于|用于|适用|广泛应用于)\s*(?P<target_name>[\u4e00-\u9fff]+(?:焊接|搬运|装配|喷涂|打磨|检测|包装|上下料))",
        "source_type": "Robot",
        "target_type": "ApplicationScenario",
    },
    {
        "relation_type": "uses_sensor",
        "pattern": r"(?P<source_name>[\w\-]+(?:机器人)?)\s*(?:配备|搭载|集成)\s*(?P<target_name>[\u4e00-\u9fff]*(?:传感器|视觉))",
        "source_type": "Robot",
        "target_type": "Sensor",
    },
    {
        "relation_type": "uses_end_effector",
        "pattern": r"(?P<source_name>[\w\-]+(?:机器人)?)\s*(?:配备|搭配|使用)\s*(?P<target_name>[\u4e00-\u9fff]*(?:焊枪|夹爪|吸盘|喷枪|打磨头|切割头|夹具|抓手))",
        "source_type": "Robot",
        "target_type": "EndEffector",
    },
    {
        "relation_type": "complies_with",
        "pattern": r"(?P<source_name>[\w\-]+(?:机器人)?)\s*(?:符合|满足|通过|获得)\s*(?P<target_name>(?:ISO|GB|IEC|EN|DIN|JIS)\s*[\d\-\.]+(?:[\u4e00-\u9fff\w]*))",
        "source_type": "Robot",
        "target_type": "Standard",
    },
]

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
