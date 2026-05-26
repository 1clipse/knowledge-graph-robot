from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field


_SCHEMA_DIR = Path(__file__).resolve().parent


class PropertyDef(BaseModel):
    type: str = "string"
    required: bool = False
    description: str = ""


class EntityType(BaseModel):
    label_zh: str
    label_en: str
    description: str = ""
    properties: Dict[str, PropertyDef] = Field(default_factory=dict)


class RelationType(BaseModel):
    label_zh: str
    label_en: str
    source: str
    target: str
    description: str = ""
    properties: Dict[str, PropertyDef] = Field(default_factory=dict)


class DomainSchema(BaseModel):
    domain: str
    version: str = "1.0.0"
    description: str = ""
    entity_types: Dict[str, EntityType] = Field(default_factory=dict)
    relation_types: Dict[str, RelationType] = Field(default_factory=dict)


_schema_cache: Optional[DomainSchema] = None


def load_schema(schema_path: Optional[str] = None) -> DomainSchema:
    global _schema_cache
    if _schema_cache is not None and schema_path is None:
        return _schema_cache
    if schema_path is None:
        schema_path = str(_SCHEMA_DIR / "industrial_robot.yaml")
    with open(schema_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    sample_data = data.pop("sample_data", None)
    schema = DomainSchema(**data)
    if schema_path is None:
        _schema_cache = schema
    return schema


def get_entity_types() -> Dict[str, EntityType]:
    return load_schema().entity_types


def get_relation_types() -> Dict[str, RelationType]:
    return load_schema().relation_types


def get_entity_type_names() -> List[str]:
    return list(load_schema().entity_types.keys())


def get_relation_type_names() -> List[str]:
    return list(load_schema().relation_types.keys())


def build_schema_prompt_context() -> str:
    schema = load_schema()
    lines: List[str] = []
    lines.append("## 实体类型 (Entity Types)")
    for name, et in schema.entity_types.items():
        props = ", ".join(
            f"{pname}({p.type}{'*' if p.required else ''})"
            for pname, p in et.properties.items()
        )
        lines.append(f"- {name}({et.label_zh}): [{props}]")
    lines.append("")
    lines.append("## 关系类型 (Relation Types)")
    for name, rt in schema.relation_types.items():
        props_str = ""
        if rt.properties:
            props_str = ", ".join(
                f"{pname}({p.type})"
                for pname, p in rt.properties.items()
            )
            props_str = f" [{props_str}]"
        lines.append(f"- {name}({rt.label_zh}): {rt.source} -> {rt.target}{props_str}")
    return "\n".join(lines)
