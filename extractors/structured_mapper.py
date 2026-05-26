from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml
from loguru import logger
from pydantic import BaseModel, Field

from extractors.llm_extractor import ExtractedEntity, ExtractedRelation, EntityRef, ExtractionResult


_MAPPING_DIR = Path(__file__).resolve().parent.parent / "config"


class ColumnMapping(BaseModel):
    column: Optional[str] = None
    type: str = "string"
    target: Optional[str] = None


class MappingRule(BaseModel):
    source_type: str
    file_pattern: Optional[str] = None
    connection_key: Optional[str] = None
    query: Optional[str] = None
    node_type: Optional[str] = None
    relation_type: Optional[str] = None
    source_node_type: Optional[str] = None
    target_node_type: Optional[str] = None
    key_column: Optional[str] = None
    source_key_column: Optional[str] = None
    target_key_column: Optional[str] = None
    column_mapping: Dict[str, Union[str, ColumnMapping]] = Field(default_factory=dict)


class MappingConfig(BaseModel):
    mappings: List[MappingRule] = Field(default_factory=list)


class StructuredMapper:
    def __init__(self, mapping_path: Optional[str] = None) -> None:
        if mapping_path is None:
            mapping_path = str(_MAPPING_DIR / "mapping_rules.yaml")
        self._mapping_path = mapping_path
        self._config = self._load_config()

    def _load_config(self) -> MappingConfig:
        path = Path(self._mapping_path)
        if not path.exists():
            logger.warning(f"Mapping file not found: {self._mapping_path}")
            return MappingConfig()
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return MappingConfig(**data)

    def _convert_value(self, value: Any, type_str: str) -> Any:
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return None
        try:
            if type_str == "integer":
                return int(float(str(value)))
            elif type_str == "float":
                return float(str(value))
            elif type_str == "boolean":
                return str(value).lower() in ("true", "1", "yes")
            else:
                return str(value)
        except (ValueError, TypeError) as e:
            logger.warning(f"Value conversion failed for {value} -> {type_str}: {e}")
            return None

    def _resolve_column_mapping(
        self, row: Dict[str, Any], mapping: Dict[str, Union[str, ColumnMapping]]
    ) -> Dict[str, Any]:
        properties: Dict[str, Any] = {}
        for target_key, col_def in mapping.items():
            if isinstance(col_def, str):
                value = row.get(col_def)
                if value is not None:
                    properties[target_key] = value
            elif isinstance(col_def, ColumnMapping):
                col_name = col_def.column or target_key
                value = row.get(col_name)
                if value is not None:
                    converted = self._convert_value(value, col_def.type)
                    if converted is not None:
                        output_key = col_def.target or target_key
                        properties[output_key] = converted
        return properties

    def map_rows_to_entities(
        self, rows: List[Dict[str, Any]], rule: MappingRule
    ) -> List[ExtractedEntity]:
        if not rule.node_type or not rule.key_column:
            logger.error("Mapping rule missing node_type or key_column")
            return []
        entities: List[ExtractedEntity] = []
        for row in rows:
            properties = self._resolve_column_mapping(row, rule.column_mapping)
            key_value = row.get(rule.key_column, "")
            if not key_value:
                continue
            properties["name"] = key_value
            entities.append(
                ExtractedEntity(name=str(key_value), type=rule.node_type, properties=properties)
            )
        logger.info(f"Mapped {len(entities)} entities from {rule.source_type}")
        return entities

    def map_rows_to_relations(
        self, rows: List[Dict[str, Any]], rule: MappingRule
    ) -> List[ExtractedRelation]:
        if not rule.relation_type or not rule.source_key_column or not rule.target_key_column:
            logger.error("Mapping rule missing relation fields")
            return []
        relations: List[ExtractedRelation] = []
        for row in rows:
            source_name = row.get(rule.source_key_column, "")
            target_name = row.get(rule.target_key_column, "")
            if not source_name or not target_name:
                continue
            rel_props = self._resolve_column_mapping(row, rule.column_mapping)
            relations.append(
                ExtractedRelation(
                    source=EntityRef(
                        name=str(source_name), type=rule.source_node_type or ""
                    ),
                    target=EntityRef(
                        name=str(target_name), type=rule.target_node_type or ""
                    ),
                    relation_type=rule.relation_type,
                    properties=rel_props,
                )
            )
        logger.info(f"Mapped {len(relations)} relations from {rule.source_type}")
        return relations

    def find_matching_rule(self, filename: str) -> Optional[MappingRule]:
        import re as _re

        for rule in self._config.mappings:
            if rule.file_pattern:
                if _re.search(rule.file_pattern, filename):
                    return rule
        return None

    def map_csv(self, file_path: str) -> ExtractionResult:
        import pandas as pd

        rule = self.find_matching_rule(Path(file_path).name)
        if not rule:
            logger.warning(f"No mapping rule found for {file_path}")
            return ExtractionResult()

        df = pd.read_csv(file_path)
        rows = df.where(df.notna(), None).to_dict("records")

        entities: List[ExtractedEntity] = []
        relations: List[ExtractedRelation] = []

        if rule.node_type:
            entities = self.map_rows_to_entities(rows, rule)
        elif rule.relation_type:
            relations = self.map_rows_to_relations(rows, rule)

        return ExtractionResult(entities=entities, relations=relations)

    def map_json(self, file_path: str, rule_override: Optional[MappingRule] = None) -> ExtractionResult:
        import json

        rule = rule_override or self.find_matching_rule(Path(file_path).name)
        if not rule:
            logger.warning(f"No mapping rule found for {file_path}")
            return ExtractionResult()

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            data = [data]

        entities: List[ExtractedEntity] = []
        relations: List[ExtractedRelation] = []

        if rule.node_type:
            entities = self.map_rows_to_entities(data, rule)
        elif rule.relation_type:
            relations = self.map_rows_to_relations(data, rule)

        return ExtractionResult(entities=entities, relations=relations)

    def map_sql_query(
        self, rows: List[Dict[str, Any]], rule: MappingRule
    ) -> ExtractionResult:
        entities: List[ExtractedEntity] = []
        relations: List[ExtractedRelation] = []

        if rule.node_type:
            entities = self.map_rows_to_entities(rows, rule)
        elif rule.relation_type:
            relations = self.map_rows_to_relations(rows, rule)

        return ExtractionResult(entities=entities, relations=relations)
