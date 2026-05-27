from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger

from extractors.llm_extractor import (
    ExtractedEntity,
    ExtractedRelation,
    EntityRef,
    ExtractionResult,
)

_MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "kg_robot_ner"
_BASE_MODEL = "zh_core_web_lg"
_nlp_cache: Any = None


def _get_nlp(model_path: str = ""):
    """Lazy-load spaCy model. Tries custom trained model first, falls back to base."""
    global _nlp_cache
    if _nlp_cache is not None:
        return _nlp_cache

    import spacy

    path = model_path or str(_MODEL_PATH)
    if path and Path(path).exists():
        try:
            _nlp_cache = spacy.load(path)
            logger.info(f"SpacyExtractor loaded custom model: {path}")
            return _nlp_cache
        except Exception as e:
            logger.warning(f"Failed to load custom model {path}: {e}, falling back to {_BASE_MODEL}")

    try:
        _nlp_cache = spacy.load(_BASE_MODEL)
    except Exception:
        logger.warning(f"{_BASE_MODEL} not found, downloading...")
        spacy.cli.download(_BASE_MODEL)
        _nlp_cache = spacy.load(_BASE_MODEL)

    logger.info(f"SpacyExtractor using base model: {_BASE_MODEL}")
    return _nlp_cache


# ── Entity type → spaCy NER label mapping ──

ENTITY_TYPE_TO_LABEL: Dict[str, str] = {
    "Robot": "ROBOT",
    "Manufacturer": "MANUF",
    "Component": "COMP",
    "Reducer": "REDUCER",
    "ServoMotor": "SERVO",
    "Controller": "CTRL",
    "Sensor": "SENSOR",
    "ApplicationScenario": "APP",
    "Process": "PROC",
    "EndEffector": "EFFECTOR",
    "Standard": "STANDARD",
    "Material": "MATERIAL",
    "Software": "SOFTWARE",
    "Drawing": "DRAWING",
    "Part": "PART",
    "Assembly": "ASSY",
    "Dimension": "DIM",
    "CADLayer": "LAYER",
}

LABEL_TO_ENTITY_TYPE: Dict[str, str] = {v: k for k, v in ENTITY_TYPE_TO_LABEL.items()}


# ── EntityRuler token patterns (converted from RuleExtractor regex) ──

_ENTITY_RULER_PATTERNS: List[Dict[str, Any]] = [
    # Manufacturer names
    {
        "label": "MANUF",
        "pattern": [
            {"LOWER": {"IN": [
                "fanuc", "abb", "kuka", "yaskawa", "kawasaki", "epson",
                "stäubli", "staubli", "comau", "nachi", "ur",
                "埃斯顿", "estun", "汇川", "新松", "发那科", "安川", "库卡",
                "川崎", "爱普生", "史陶比尔", "柯马", "那智不二越", "优傲",
            ]}}
        ],
    },
    # Manufacturer patterns: Chinese company names
    {
        "label": "MANUF",
        "pattern": [
            {"TEXT": {"REGEX": r"[一-鿿]{2,6}"}},
            {"TEXT": {"REGEX": r"(公司|集团|股份|有限|机器人|智能|科技|机电|自动化|精密|机械|电气|电机|传动|控制|技术|装备|重工)"}},
        ],
    },
    # Robot patterns
    {
        "label": "ROBOT",
        "pattern": [
            {"TEXT": {"REGEX": r"[A-Z]{2,6}"}},
            {"TEXT": {"REGEX": r"[\w\-\+]+"}},
            {"LOWER": {"IN": ["机器人"]}},
        ],
    },
    # Reducer type keywords
    {
        "label": "REDUCER",
        "pattern": [
            {"LOWER": {"REGEX": r"(rv|shg|csg|谐波|行星|摆线)[\-\d]*[a-z]*"}},
            {"LOWER": {"IN": ["减速器", "减速机"]}},
        ],
    },
    {
        "label": "REDUCER",
        "pattern": [
            {"LOWER": {"REGEX": r"^(rv|shg|csg|谐波|行星|摆线)[\-\d]*[a-z]*$"}},
        ],
    },
    # Servo motor
    {
        "label": "SERVO",
        "pattern": [
            {"TEXT": {"REGEX": r"[\w\-]+"}},
            {"LOWER": {"IN": ["伺服电机", "伺服马达", "伺服驱动器", "伺服"]}},
        ],
    },
    # Controller
    {
        "label": "CTRL",
        "pattern": [
            {"TEXT": {"REGEX": r"R[\-\d]+[A-Za-z]*"}},
            {"LOWER": {"IN": ["控制器", "控制系统"]}},
        ],
    },
    {
        "label": "CTRL",
        "pattern": [{"TEXT": {"REGEX": r"R[\-\d]+[A-Za-z]*"}}],
    },
    # Application scenarios (Chinese)
    {
        "label": "APP",
        "pattern": [
            {"TEXT": {"REGEX": r"[一-鿿]{2,4}"}},
            {"TEXT": {"REGEX": r"(焊接|搬运|装配|喷涂|打磨|抛光|码垛|切割|检测|包装|上下料|分拣|涂胶|冲压|注塑)"}},
        ],
    },
    # Process names
    {
        "label": "PROC",
        "pattern": [{"LOWER": {"IN": [
            "点焊", "弧焊", "激光焊", "螺柱焊", "涂胶", "喷涂", "搬运", "码垛",
            "装配", "打磨", "抛光", "切割", "冲压", "注塑", "机加工", "检测", "包装",
        ]}}],
    },
    # End effectors (tools)
    {
        "label": "EFFECTOR",
        "pattern": [
            {"TEXT": {"REGEX": r"[一-鿿]{1,4}"}},
            {"LOWER": {"IN": ["焊枪", "夹爪", "吸盘", "喷枪", "打磨头", "切割头", "夹具", "抓手", "末端执行器"]}},
        ],
    },
    # Sensor
    {
        "label": "SENSOR",
        "pattern": [
            {"TEXT": {"REGEX": r"[一-鿿]{1,4}"}},
            {"LOWER": {"REGEX": r"(力矩|力|视觉|碰撞|位置|安全|激光|超声波|红外|扭矩|温度|压力)"}},
            {"LOWER": {"IN": ["传感器"]}},
        ],
    },
    # Standards (multi-token: "ISO", "10218", "-", "1")
    {
        "label": "STANDARD",
        "pattern": [
            {"TEXT": {"REGEX": r"^(ISO|GB|IEC|EN|DIN|JIS)$"}},
            {"TEXT": {"REGEX": r"^[\d]+$"}},
        ],
    },
    # Materials
    {
        "label": "MATERIAL",
        "pattern": [
            {"LOWER": {"IN": ["铝合金", "不锈钢", "碳钢", "钛合金", "铜合金", "铸铁", "铸钢", "工程塑料", "复合材料", "陶瓷"]}},
        ],
    },
    # Software
    {
        "label": "SOFTWARE",
        "pattern": [
            {"TEXT": {"REGEX": r"[A-Z][\w\-]+"}},
            {"LOWER": {"IN": ["软件", "系统", "平台", "os", "suite"]}},
        ],
    },
]


# ── Dependency-based relation extraction verbs ──

_RELATION_VERB_PATTERNS: List[Dict[str, Any]] = [
    {
        "relation_type": "manufactures",
        "verbs": ["推出", "发布", "生产", "制造", "研制", "量产"],
        "source_type": "Manufacturer",
        "target_type": "Robot",
    },
    {
        "relation_type": "uses_component",
        "verbs": ["采用", "使用", "配备", "搭载", "集成", "搭配"],
        "source_type": "Robot",
        "target_type": "Component",
    },
    {
        "relation_type": "uses_reducer",
        "verbs": ["采用", "使用", "配备", "搭载"],
        "source_type": "Robot",
        "target_type": "Reducer",
    },
    {
        "relation_type": "uses_servo",
        "verbs": ["采用", "使用", "配备", "搭载"],
        "source_type": "Robot",
        "target_type": "ServoMotor",
    },
    {
        "relation_type": "uses_controller",
        "verbs": ["搭配", "配合", "使用", "配备"],
        "source_type": "Robot",
        "target_type": "Controller",
    },
    {
        "relation_type": "uses_sensor",
        "verbs": ["配备", "搭载", "集成"],
        "source_type": "Robot",
        "target_type": "Sensor",
    },
    {
        "relation_type": "uses_end_effector",
        "verbs": ["配备", "搭配", "使用", "集成"],
        "source_type": "Robot",
        "target_type": "EndEffector",
    },
    {
        "relation_type": "applied_in",
        "verbs": ["应用于", "用于", "适用", "广泛应用于", "投入"],
        "source_type": "Robot",
        "target_type": "ApplicationScenario",
    },
    {
        "relation_type": "complies_with",
        "verbs": ["符合", "满足", "通过", "获得"],
        "source_type": "Robot",
        "target_type": "Standard",
    },
    {
        "relation_type": "performs_process",
        "verbs": ["执行", "完成", "实现", "进行"],
        "source_type": "Robot",
        "target_type": "Process",
    },
]


# ── Regex pre-filter patterns (bypasses spaCy tokenization for multi-token entities) ──

_REGEX_ENTITY_PATTERNS: List[Dict[str, Any]] = [
    {
        "type": "Manufacturer",
        "patterns": [
            r"(?P<name>FANUC|ABB|KUKA|安川|Yaskawa|川崎|Kawasaki|爱普生|Epson|史陶比尔|Stäubli|柯马|Comau|那智不二越|Nachi|优傲|Universal\s*Robots|UR|埃斯顿|Estun|汇川|新松|发那科)",
            r"(?P<name>[一-鿿]{2,4}(?:公司|集团|股份|有限|机器人|智能|科技|机电|自动化|精密|机械|电气|电机|传动|控制|技术|装备|重工))",
        ],
    },
    {
        "type": "Robot",
        "patterns": [
            r"(?P<name>[A-Z]{2,8}\s*[\w\-\+\.]+(?:机器人)?)\s*(?:是|为|是一款|是一台|是一型)",
            r"(?P<name>[A-Z]{2,8}\s*[\w\-\+\.]+)\s*(?:负载|额定负载|有效负载|臂展|重复定位)",
        ],
    },
    {
        "type": "Reducer",
        "patterns": [
            r"(?P<name>(?:RV|SHG|CSG|諧波|谐波|行星|摆线)[\-\d]*[A-Za-z]*(?:\s*(?:减速器|减速机))?)",
            r"(?P<name>(?:RV|SHG|CSG)[\-\d]+[A-Za-z]*)",
        ],
    },
    {
        "type": "ServoMotor",
        "patterns": [
            r"(?P<name>[\w\-]+(?:伺服电机|伺服马达))",
            r"(?:伺服电机|伺服马达)\s*(?:型号|规格)[：:]?\s*(?P<name>[\w\-]+)",
        ],
    },
    {
        "type": "Controller",
        "patterns": [
            r"(?P<name>R[\-\d]+[A-Za-z]*(?:\s*(?:控制器|控制系统|Plus|iB|Mate))?)",
        ],
    },
    {
        "type": "Sensor",
        "patterns": [
            r"(?P<name>[一-鿿]{1,4}(?:力矩|力|视觉|碰撞|位置|安全|激光|超声波|红外|扭矩|温度|压力)传感器)",
        ],
    },
    {
        "type": "EndEffector",
        "patterns": [
            r"(?P<name>[一-鿿]{1,4}(?:焊枪|夹爪|吸盘|喷枪|打磨头|切割头|夹具|抓手))",
        ],
    },
    {
        "type": "ApplicationScenario",
        "patterns": [
            r"(?P<name>[一-鿿]{2,4}(?:焊接|搬运|装配|喷涂|打磨|抛光|码垛|切割|检测|包装|上下料|分拣|涂胶|冲压|注塑))",
        ],
    },
    {
        "type": "Process",
        "patterns": [
            r"(?P<name>点焊|弧焊|激光焊|螺柱焊|涂胶|喷涂|搬运|码垛|装配|打磨|抛光|切割|冲压|注塑|机加工|检测|包装)",
        ],
    },
    {
        "type": "Standard",
        "patterns": [
            r"(?P<name>(?:ISO|GB|IEC|EN|DIN|JIS)\s*[\d\-\.]+(?:\s*(?:标准|规范))?)",
        ],
    },
    {
        "type": "Material",
        "patterns": [
            r"(?P<name>铝合金|不锈钢|碳钢|钛合金|铜合金|铸铁|铸钢|工程塑料|复合材料|陶瓷)",
        ],
    },
]


class SpacyExtractor:
    """Hybrid entity/relation extractor using regex pre-filter + spaCy NER + EntityRuler.

    Design:
      - Regex pre-filter: catches multi-token entities that spaCy tokenizer splits
        (e.g. "RV-20E" → R/V/-/20/E in Chinese tokenizer, but regex sees "RV-20E减速器")
      - EntityRuler: catches single-token industrial patterns
      - spaCy NER: catches novel entities (better recall)
      - Dependency parsing: extracts relations between recognized entities
      - Confidence: regex=0.90, EntityRuler=0.85, NER=0.75

    Usage:
        ex = SpacyExtractor()
        result = ex.extract("FANUC M-20iA是六轴工业机器人，负载20kg，配备RV-20E减速器")
    """

    def __init__(self, model_path: str = "", use_ruler: bool = True) -> None:
        self._model_path = model_path
        self._use_ruler = use_ruler
        self._nlp: Any = None

    @property
    def nlp(self):
        if self._nlp is None:
            self._nlp = _get_nlp(self._model_path)
            if self._use_ruler:
                self._add_ruler()
        return self._nlp

    def _add_ruler(self) -> None:
        """Add EntityRuler with industrial robot patterns to the pipeline."""
        nlp = self._nlp
        if "entity_ruler" not in nlp.pipe_names:
            ruler = nlp.add_pipe("entity_ruler", before="ner", config={"validate": True})
        else:
            ruler = nlp.get_pipe("entity_ruler")
        ruler.add_patterns(_ENTITY_RULER_PATTERNS)
        logger.info(f"EntityRuler added with {len(_ENTITY_RULER_PATTERNS)} patterns")

    def extract(self, text: str) -> ExtractionResult:
        if not text or not text.strip():
            return ExtractionResult()

        # Run regex pre-filter on raw text (before spaCy tokenization)
        regex_entities = self._regex_extract_entities(text)

        # Run spaCy pipeline (EntityRuler + NER)
        doc = self.nlp(text)
        spacy_entities = self._spacy_extract_entities(doc)

        # Merge: regex entities take priority, spaCy fills gaps
        entities = self._merge_entity_lists(regex_entities, spacy_entities)
        relations = self._extract_relations(doc, entities)
        logger.debug(
            f"SpacyExtractor: {len(entities)} entities ({len(regex_entities)} regex + {len(spacy_entities)} spaCy), "
            f"{len(relations)} relations"
        )
        return ExtractionResult(entities=entities, relations=relations)

    def _regex_extract_entities(self, text: str) -> List[ExtractedEntity]:
        """Use regex on raw text to find entities, bypassing tokenizer fragmentation."""
        seen: Dict[str, ExtractedEntity] = {}
        for pattern_group in _REGEX_ENTITY_PATTERNS:
            entity_type = pattern_group["type"]
            for pattern in pattern_group["patterns"]:
                for match in re.finditer(pattern, text, re.IGNORECASE):
                    name = match.group("name").strip()
                    if not name or len(name) < 2:
                        continue
                    key = f"{entity_type}::{name}"
                    if key not in seen:
                        seen[key] = ExtractedEntity(
                            name=name,
                            type=entity_type,
                            properties={},
                            confidence=0.90,
                        )
        return list(seen.values())

    def _spacy_extract_entities(self, doc) -> List[ExtractedEntity]:
        """Extract entities from spaCy Doc (EntityRuler + NER)."""
        seen: Dict[str, ExtractedEntity] = {}

        for ent in doc.ents:
            entity_type = LABEL_TO_ENTITY_TYPE.get(ent.label_)
            if entity_type is None:
                continue

            name = ent.text.strip()
            if not name or len(name) < 2:
                continue

            # Determine confidence: EntityRuler > NER
            is_ruler = bool(ent.ent_id_) and ent.ent_id_.startswith("ruler")
            confidence = 0.85 if is_ruler else max(0.75, getattr(ent, "_confidence", 0.75) or 0.75)

            key = f"{entity_type}::{name}"
            if key in seen:
                existing = seen[key]
                existing.confidence = max(existing.confidence, confidence)
            else:
                seen[key] = ExtractedEntity(
                    name=name, type=entity_type, properties={}, confidence=confidence,
                )

        return list(seen.values())

    @staticmethod
    def _merge_entity_lists(
        regex_entities: List[ExtractedEntity],
        spacy_entities: List[ExtractedEntity],
    ) -> List[ExtractedEntity]:
        """Merge regex and spaCy entities, keeping highest confidence and deduplicating.

        Fuzzy dedup: if spaCy entity name is a substring of regex entity (or vice versa),
        keep the longer one with higher confidence.
        """
        merged: Dict[str, ExtractedEntity] = {}

        # Add regex entities first (higher priority)
        for e in regex_entities:
            key = f"{e.type}::{e.name}"
            merged[key] = e

        # Add spaCy entities, checking for substring overlaps
        for e in spacy_entities:
            key = f"{e.type}::{e.name}"
            if key in merged:
                merged[key].confidence = max(merged[key].confidence, e.confidence)
                continue

            # Check if this entity is a substring of an existing regex entity
            found = False
            for m_key, m_ent in list(merged.items()):
                if m_ent.type != e.type:
                    continue
                if e.name in m_ent.name or m_ent.name in e.name:
                    # Keep the one with higher confidence; prefer longer name
                    if len(e.name) > len(m_ent.name):
                        merged.pop(m_key)
                        merged[key] = e
                    else:
                        merged[m_key].confidence = max(merged[m_key].confidence, e.confidence)
                    found = True
                    break

            if not found:
                merged[key] = e

        return list(merged.values())

    def _extract_relations(
        self, doc, entities: List[ExtractedEntity]
    ) -> List[ExtractedRelation]:
        entity_names_by_type: Dict[str, Set[str]] = {}
        for e in entities:
            entity_names_by_type.setdefault(e.type, set()).add(e.name)

        relations: List[ExtractedRelation] = []

        for token in doc:
            if token.pos_ != "VERB":
                continue

            verb_lemma = token.lemma_ or token.text
            for vp in _RELATION_VERB_PATTERNS:
                if verb_lemma not in vp["verbs"]:
                    continue

                source_type = vp["source_type"]
                target_type = vp["target_type"]
                rel_type = vp["relation_type"]

                # Find subject (nsubv) and object (dobj, obj)
                subj_name = self._find_dependent(token, {"nsubj", "nsubjpass"})
                obj_name = self._find_dependent(token, {"dobj", "obj", "obl:arg"})

                if not subj_name or not obj_name:
                    continue

                # Match against extracted entities by type
                subj_type = self._resolve_type(subj_name, entity_names_by_type, source_type)
                obj_type = self._resolve_type(obj_name, entity_names_by_type, target_type)

                if subj_type and obj_type:
                    relations.append(
                        ExtractedRelation(
                            source=EntityRef(name=subj_name, type=subj_type),
                            target=EntityRef(name=obj_name, type=obj_type),
                            relation_type=rel_type,
                            confidence=0.80,
                        )
                    )

        return self._deduplicate_relations(relations)

    @staticmethod
    def _find_dependent(token, dep_labels: Set[str]) -> str:
        """Find the text of a child token with one of the given dependency labels."""
        for child in token.children:
            if child.dep_ in dep_labels:
                # Get the full noun chunk for this child
                chunk = " ".join(t.text for t in child.subtree
                                 if t.pos_ in ("NOUN", "PROPN", "ADJ", "NUM", "X", "PART"))
                return chunk.strip() or child.text.strip()
        return ""

    @staticmethod
    def _resolve_type(
        name: str,
        entity_names_by_type: Dict[str, Set[str]],
        preferred_type: str,
    ) -> str:
        """Check if name matches entities of a given type, return the type or empty."""
        # Exact match
        for etype, names in entity_names_by_type.items():
            if name in names or any(n in name or name in n for n in names):
                return etype
        # Fallback: if name seems plausible for preferred type
        if preferred_type in entity_names_by_type:
            return preferred_type
        return ""

    @staticmethod
    def _deduplicate_relations(
        relations: List[ExtractedRelation],
    ) -> List[ExtractedRelation]:
        seen: Set[Tuple[str, str, str]] = set()
        unique: List[ExtractedRelation] = []
        for r in relations:
            key = (r.source.name, r.relation_type, r.target.name)
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return unique


def _parse_labeled_json(file_path: str) -> List[Tuple[str, Dict]]:
    """Parse a labeled JSON file in instruction/input/output format.

    Returns list of (text, {"entities": [(start, end, label), ...]}) tuples.
    """
    import json as _json

    examples: List[Tuple[str, Dict]] = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = _json.load(f)
    except Exception as e:
        logger.warning(f"Failed to parse {file_path}: {e}")
        return examples

    if not isinstance(data, list):
        return examples

    for entry in data:
        text = entry.get("input", "")
        output_str = entry.get("output", "")
        if not text or not output_str or output_str == "[]":
            continue

        try:
            output = _json.loads(output_str)
        except Exception:
            continue

        entities_raw = output.get("entities", [])
        if not entities_raw:
            continue

        spans = []
        for ent in entities_raw:
            name = ent.get("name", "")
            etype = ent.get("type", "")
            if not name or len(name) < 2:
                continue
            label = ENTITY_TYPE_TO_LABEL.get(etype)
            if not label:
                continue

            # Find all occurrences of the entity name in text
            start = 0
            while True:
                pos = text.find(name, start)
                if pos == -1:
                    break
                # Avoid overlapping spans
                end = pos + len(name)
                if not any(s < end and pos < e for s, e, _ in spans):
                    spans.append((pos, end, label))
                start = pos + 1

        if spans:
            examples.append((text, {"entities": spans}))

    return examples


def _label_raw_text_with_regex(file_path: str) -> List[Tuple[str, Dict]]:
    """Auto-label raw text files using regex patterns.

    Each non-empty line becomes a training example with regex-matched entity spans.
    """
    examples: List[Tuple[str, Dict]] = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        logger.warning(f"Failed to read {file_path}: {e}")
        return examples

    # Split into paragraphs/lines
    lines = [l.strip() for l in content.split("\n") if l.strip()]
    for text in lines:
        if len(text) < 10:
            continue
        spans = []
        for pattern_group in _REGEX_ENTITY_PATTERNS:
            etype = pattern_group["type"]
            label = ENTITY_TYPE_TO_LABEL.get(etype)
            if not label:
                continue
            for pattern in pattern_group["patterns"]:
                for match in re.finditer(pattern, text, re.IGNORECASE):
                    name = match.group("name").strip()
                    if not name or len(name) < 2:
                        continue
                    s, e = match.span("name")
                    if not any(ps < e and s < pe for ps, pe, _ in spans):
                        spans.append((s, e, label))
        if spans:
            examples.append((text, {"entities": spans}))

    return examples


def _query_kg_entities(neo4j_client) -> Dict[str, List[str]]:
    """Query KG for all entity names, grouped by type."""
    from graph.query import GraphQuery

    gq = GraphQuery(neo4j_client)
    entity_names: Dict[str, List[str]] = {}

    for etype in ENTITY_TYPE_TO_LABEL:
        try:
            results = gq.fulltext_search(etype, limit=500)
            names = []
            for item in results:
                node = item.get("node", {})
                name = node.get("name", "")
                if name and len(name) >= 2:
                    names.append(name)
            if names:
                entity_names[etype] = names
        except Exception:
            continue

    return entity_names


def _label_raw_text_with_kg(file_path: str, kg_entities: Dict[str, List[str]]) -> List[Tuple[str, Dict]]:
    """Auto-label raw text files by searching for KG entity names."""
    examples: List[Tuple[str, Dict]] = []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        logger.warning(f"Failed to read {file_path}: {e}")
        return examples

    lines = [l.strip() for l in content.split("\n") if l.strip()]
    for text in lines:
        if len(text) < 10:
            continue
        spans = []
        for etype, names in kg_entities.items():
            label = ENTITY_TYPE_TO_LABEL.get(etype)
            if not label:
                continue
            for name in names:
                start = 0
                while True:
                    pos = text.find(name, start)
                    if pos == -1:
                        break
                    end = pos + len(name)
                    # Avoid overlapping and sub-word matches
                    if not any(s < end and pos < e for s, e, _ in spans):
                        spans.append((pos, end, label))
                    start = pos + 1
        if spans:
            examples.append((text, {"entities": spans}))

    return examples


def generate_ner_training_data(
    neo4j_client=None,
    data_dir: str = "",
) -> List[Tuple[str, Dict]]:
    """Generate spaCy NER training data from real labeled documents.

    Priority order:
      1. Labeled JSON files (auto_labeled, handcrafted, enhanced, train_final)
      2. Raw text files auto-labeled with regex patterns
      3. Raw text files auto-labeled with KG entity names (remote supervision)
      4. KG template generation (fallback when all above are empty)

    Args:
        neo4j_client: Optional Neo4j client for KG queries (fallback only)
        data_dir: Path to directory containing labeled JSON and raw text files

    Returns:
        List of (text, {"entities": [(start, end, label), ...]}) tuples
    """
    import json as _json
    from pathlib import Path as _Path

    examples: List[Tuple[str, Dict]] = []
    seen: Set[str] = set()

    # Determine data directory
    if data_dir:
        data_path = _Path(data_dir)
    else:
        data_path = _Path(__file__).resolve().parent.parent / "scripts" / "finetune" / "data"

    if data_path.exists():
        # Step 1: Parse labeled JSON files
        json_files = sorted(data_path.glob("*.json"))
        for jf in json_files:
            parsed = _parse_labeled_json(str(jf))
            for text, ann in parsed:
                # Use hash of full text + entity types for dedup
                key = text.strip() + "|" + ",".join(sorted(l for _, _, l in ann["entities"]))
                if key not in seen:
                    seen.add(key)
                    examples.append((text, ann))
            if parsed:
                logger.info(f"  {jf.name}: {len(parsed)} examples")

        # Step 2: Auto-label raw text files with regex
        txt_files = sorted(data_path.glob("*.txt"))
        for tf in txt_files:
            parsed = _label_raw_text_with_regex(str(tf))
            for text, ann in parsed:
                key = text.strip() + "|re"
                if key not in seen:
                    seen.add(key)
                    examples.append((text, ann))
            if parsed:
                logger.info(f"  {tf.name} (regex): {len(parsed)} examples")

        # Step 3: Auto-label with KG entities if client available
        if neo4j_client is not None:
            kg_entities = _query_kg_entities(neo4j_client)
            if kg_entities:
                for tf in txt_files:
                    parsed = _label_raw_text_with_kg(str(tf), kg_entities)
                    for text, ann in parsed:
                        key = text.strip() + "|kg"
                        if key not in seen:
                            seen.add(key)
                            examples.append((text, ann))
                    if parsed:
                        logger.info(f"  {tf.name} (KG): {len(parsed)} examples")

    # Step 4: Fallback to KG template generation
    if not examples and neo4j_client is not None:
        logger.info("No real-document data found, falling back to KG template generation")
        examples = _generate_template_examples(neo4j_client)

    logger.info(f"Total NER training examples: {len(examples)}")

    # Log per-label statistics
    label_counts: Dict[str, int] = {}
    for _, ann in examples:
        for _, _, label in ann["entities"]:
            label_counts[label] = label_counts.get(label, 0) + 1
    if label_counts:
        for label, count in sorted(label_counts.items()):
            etype = LABEL_TO_ENTITY_TYPE.get(label, label)
            logger.info(f"  {etype} ({label}): {count}")

    return examples


def _generate_template_examples(neo4j_client) -> List[Tuple[str, Dict]]:
    """Fallback: generate training examples from KG via template injection."""
    from graph.query import GraphQuery

    gq = GraphQuery(neo4j_client)

    templates: Dict[str, List[str]] = {
        "Robot": [
            "{name}是{props}工业机器人",
            "{name}负载为{props}",
            "选用{name}进行自动化作业",
        ],
        "Manufacturer": [
            "{name}是工业机器人制造商",
            "{name}推出了新型机器人",
        ],
        "Reducer": [
            "{name}是高精度减速器",
            "机器人采用{name}作为核心传动部件",
        ],
        "ServoMotor": [
            "{name}是伺服电机型号",
            "关节驱动使用{name}",
        ],
        "Controller": [
            "{name}是机器人控制器",
            "搭配{name}控制系统",
        ],
        "Sensor": [
            "{name}是工业传感器",
            "配备{name}进行实时监控",
        ],
        "ApplicationScenario": [
            "该机器人应用于{name}领域",
        ],
        "Process": [
            "该产线包含{name}工序",
        ],
        "EndEffector": [
            "机器人配备{name}完成任务",
        ],
        "Standard": [
            "产品符合{name}要求",
        ],
        "Material": [
            "零件材质为{name}",
        ],
        "Software": [
            "使用{name}进行离线编程",
        ],
    }

    examples: List[Tuple[str, Dict]] = []
    seen_texts: Set[str] = set()

    for entity_type, label in ENTITY_TYPE_TO_LABEL.items():
        tmpls = templates.get(entity_type, ["{name}"])
        try:
            results = gq.fulltext_search(entity_type, limit=200)
        except Exception:
            continue

        for item in results:
            node = item.get("node", {})
            name = node.get("name", "")
            if not name or len(name) < 2:
                continue

            for tmpl in tmpls:
                props_str = ""
                for k, v in node.items():
                    if k not in ("name", "_source", "_confidence", "_embedding") and v:
                        props_str = str(v)
                        break
                text = tmpl.format(name=name, props=props_str or name)
                if text in seen_texts:
                    continue
                seen_texts.add(text)

                start = text.find(name)
                if start >= 0:
                    examples.append((text, {"entities": [(start, start + len(name), label)]}))

    logger.info(f"Generated {len(examples)} template-based training examples")
    return examples


def train_spacy_model(
    train_data: List[Tuple[str, Dict]],
    output_path: str = "",
    iterations: int = 30,
    base_model: str = _BASE_MODEL,
) -> Any:
    """Fine-tune a spaCy NER model on the given training data.

    Args:
        train_data: List of (text, {"entities": [...]}) tuples
        output_path: Where to save the trained model
        iterations: Number of training iterations
        base_model: Base spaCy model to fine-tune

    Returns:
        The trained spaCy nlp object
    """
    import random
    import spacy
    from spacy.training import Example

    output_path = output_path or str(_MODEL_PATH)

    nlp = spacy.load(base_model)

    # Add NER if not present
    if "ner" not in nlp.pipe_names:
        ner = nlp.add_pipe("ner", last=True)
    else:
        ner = nlp.get_pipe("ner")

    # Add all entity labels
    for label in ENTITY_TYPE_TO_LABEL.values():
        ner.add_label(label)

    # Disable other pipes during training
    other_pipes = [p for p in nlp.pipe_names if p != "ner"]
    with nlp.disable_pipes(*other_pipes):
        optimizer = nlp.resume_training()
        for i in range(iterations):
            random.shuffle(train_data)
            losses: Dict[str, float] = {}
            for text, annotations in train_data:
                doc = nlp.make_doc(text)
                example = Example.from_dict(doc, annotations)
                nlp.update([example], drop=0.3, losses=losses, sgd=optimizer)
            logger.info(f"Iteration {i + 1}/{iterations}, NER loss: {losses.get('ner', 0):.4f}")

    # Save model
    Path(output_path).mkdir(parents=True, exist_ok=True)
    nlp.to_disk(output_path)
    logger.info(f"Model saved to {output_path}")

    return nlp
