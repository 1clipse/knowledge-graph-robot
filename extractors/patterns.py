"""Shared regex entity and relation patterns — single source of truth.

Derived from schema/industrial_robot.yaml entity types.
Both rule_extractor and spacy_extractor should import from here.
"""
from __future__ import annotations

from typing import Any, Dict, List

_ENTITY_PATTERNS: List[Dict[str, Any]] = [
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
            r"(?P<name>[一-鿿]+(?:公司|集团|股份|有限))\s*(?:推出|发布|研制|生产|制造)",
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
            r"(?:应用于|用于|适用|广泛应用于)\s*(?P<name>[一-鿿]+(?:焊接|搬运|装配|喷涂|打磨|抛光|码垛|切割|检测|包装|上下料))",
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
            r"(?P<name>[一-鿿]*(?:焊枪|夹爪|吸盘|喷枪|打磨头|切割头|夹具|抓手))",
            r"(?:末端执行器|末端工具|末端夹具)\s*(?:型号|类型)[：:]?\s*(?P<name>[\w\-]+)",
        ],
        "property_map": {},
    },
    {
        "type": "Sensor",
        "patterns": [
            r"(?P<name>[一-鿿]*(?:力矩传感器|力传感器|视觉传感器|碰撞传感器|位置传感器|安全传感器|2D视觉|3D视觉))",
            r"(?P<sensor_type>力矩|力|视觉|碰撞|位置|安全)\s*传感器\s*(?:型号)[：:]?\s*(?P<name>[\w\-]+)",
        ],
        "property_map": {
            "sensor_type": ("sensor_type", str),
        },
    },
    {
        "type": "Standard",
        "patterns": [
            r"(?P<name>(?:ISO|GB|IEC|EN|DIN|JIS)\s*[\d\-\.]+(?:[一-鿿\w]*))",
        ],
        "property_map": {},
    },
]

_RELATION_PATTERNS: List[Dict[str, Any]] = [
    {
        "relation_type": "manufactures",
        "pattern": r"(?P<source_name>FANUC|ABB|KUKA|安川|Yaskawa|川崎|爱普生|埃斯顿|汇川|新松|[一-鿿]+(?:公司|集团))\s*(?:推出|发布|生产|制造|研制)\s*(?:了?\s*)?(?P<target_name>[\w\-]+(?:机器人)?)",
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
        "pattern": r"(?P<source_name>[\w\-]+(?:机器人)?)\s*(?:应用于|用于|适用|广泛应用于)\s*(?P<target_name>[一-鿿]+(?:焊接|搬运|装配|喷涂|打磨|检测|包装|上下料))",
        "source_type": "Robot",
        "target_type": "ApplicationScenario",
    },
    {
        "relation_type": "uses_sensor",
        "pattern": r"(?P<source_name>[\w\-]+(?:机器人)?)\s*(?:配备|搭载|集成)\s*(?P<target_name>[一-鿿]*(?:传感器|视觉))",
        "source_type": "Robot",
        "target_type": "Sensor",
    },
    {
        "relation_type": "uses_end_effector",
        "pattern": r"(?P<source_name>[\w\-]+(?:机器人)?)\s*(?:配备|搭配|使用)\s*(?P<target_name>[一-鿿]*(?:焊枪|夹爪|吸盘|喷枪|打磨头|切割头|夹具|抓手))",
        "source_type": "Robot",
        "target_type": "EndEffector",
    },
    {
        "relation_type": "complies_with",
        "pattern": r"(?P<source_name>[\w\-]+(?:机器人)?)\s*(?:符合|满足|通过|获得)\s*(?P<target_name>(?:ISO|GB|IEC|EN|DIN|JIS)\s*[\d\-\.]+(?:[一-鿿\w]*))",
        "source_type": "Robot",
        "target_type": "Standard",
    },
]


def get_entity_patterns() -> List[Dict[str, Any]]:
    return _ENTITY_PATTERNS


def get_relation_patterns() -> List[Dict[str, Any]]:
    return _RELATION_PATTERNS
