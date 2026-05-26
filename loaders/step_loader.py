from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


# STEP entity types we care about
_PRODUCT_RE = re.compile(
    r"#\d+=PRODUCT\s*\(\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'([^']*)'"
)
_PRODUCT_DEF_RE = re.compile(
    r"#\d+=PRODUCT_DEFINITION_FORMATION_WITH_SPECIFIED_SOURCE\s*\([^,]*,\s*'([^']*)'"
)
_PRODUCT_DEF_SHAPE_RE = re.compile(
    r"#\d+=PRODUCT_DEFINITION_SHAPE\s*\([^,]*,\s*#(\d+)\)"
    r"|#\d+=SHAPE_DEFINITION_REPRESENTATION\s*\(\s*#\d+\s*,\s*#(\d+)\)"
)
_MATERIAL_RE = re.compile(
    r"#\d+=MATERIAL_DESIGNATION\s*\([^,]*,\s*'([^']*)'"
    r"|#\d+=MATERIAL\s*\(\s*'([^']*)'"
)
_MEASURE_RE = re.compile(
    r"#\d+=(?:MEASURE_REPRESENTATION_ITEM|MEASURE_WITH_UNIT)\s*\([^,]*,\s*'([^']*)'\s*,\s*[^,]*,\s*(?:#\d+|([\d.]+))"
)
_PROPERTY_DEF_RE = re.compile(
    r"#\d+=PROPERTY_DEFINITION\s*\([^,]*,\s*'([^']*)'\s*,\s*#\d+\s*,\s*#(\d+)\)"
)
_PROPERTY_REPR_RE = re.compile(
    r"#\d+=PROPERTY_DEFINITION_REPRESENTATION\s*\([^)]*\)"
)
_DIMENSIONAL_RE = re.compile(
    r"#\d+=DIMENSIONAL_EXPONENTS\s*\(([\d.,\-Ee]+)\)"
)
_CARTESIAN_POINT_RE = re.compile(
    r"#\d+=CARTESIAN_POINT\s*\([^,]*,\s*\(([-\d.,\sEe]+)\)\)"
)
_NEXT_ASSEMBLY_RE = re.compile(
    r"#\d+=NEXT_ASSEMBLY_USAGE_OCCURRENCE\s*\(\s*[^,]*,\s*'([^']*)'\s*,\s*'([^']*)'"
)
_LENGTH_UNIT_RE = re.compile(
    r"#\d+=(?:LENGTH_UNIT|SI_UNIT)\s*\([^)]*\)[^#]*#\d+=(?:LENGTH_UNIT|NAMED_UNIT)\s*\(\s*\*\s*,\s*'([^']*)'"
)
_MASS_UNIT_RE = re.compile(
    r"#\d+=MEASURE_WITH_UNIT\s*\([^,]*,\s*[^,]*,\s*([\d.E+\-]+)\s*,\s*#(\d+)"
)
_UNIT_NAME_RE = re.compile(
    r"#(\d+)=(?:NAMED_UNIT|SI_UNIT|LENGTH_UNIT|MASS_UNIT)\s*\(\s*\*\s*,\s*'([^']*)'"
)
_FILE_NAME_RE = re.compile(
    r"FILE_NAME\s*\(\s*'([^']*)'\s*,"
)
_FILE_DESC_RE = re.compile(
    r"FILE_DESCRIPTION\s*\(\s*\(([^)]*)\)\s*,"
)


class STEPMetadata:
    """解析结果"""
    def __init__(self) -> None:
        self.products: List[Dict[str, str]] = []
        self.part_numbers: List[str] = []
        self.materials: List[str] = []
        self.measures: Dict[str, float] = {}
        self.points: List[Tuple[float, float, float]] = []
        self.assembly_refs: List[Dict[str, str]] = []
        self.file_name: str = ""
        self.file_description: str = ""
        self.units: Dict[int, str] = {}


class STEPLoader:
    """STEP/STP/IGES 文件解析器 — 提取元数据用于知识图谱"""

    SUPPORTED_EXTENSIONS = {".step", ".stp", ".igs", ".iges"}

    def load_metadata(self, file_path: str) -> STEPMetadata:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        text = path.read_text(encoding="utf-8", errors="ignore")
        return self._parse(text)

    def load_as_text(self, file_path: str) -> str:
        """提取STEP文件的结构化元数据 → 自然语言描述，供LLM抽取"""
        meta = self.load_metadata(file_path)
        return self._to_text(meta)

    def _parse(self, text: str) -> STEPMetadata:
        meta = STEPMetadata()

        # File header
        fn_match = _FILE_NAME_RE.search(text)
        if fn_match:
            meta.file_name = fn_match.group(1)
        fd_match = _FILE_DESC_RE.search(text)
        if fd_match:
            meta.file_description = fd_match.group(1)

        # Units (first pass)
        for m in _UNIT_NAME_RE.finditer(text):
            unit_id = int(m.group(1))
            unit_name = m.group(2)
            meta.units[unit_id] = unit_name

        # Products
        for m in _PRODUCT_RE.finditer(text):
            meta.products.append({
                "id": m.group(1),
                "name": m.group(2),
                "description": m.group(3),
            })

        # Part numbers
        for m in _PRODUCT_DEF_RE.finditer(text):
            meta.part_numbers.append(m.group(1))

        # Materials
        for m in _MATERIAL_RE.finditer(text):
            material = m.group(1) or m.group(2)
            if material:
                meta.materials.append(material.strip())

        # Assembly references
        for m in _NEXT_ASSEMBLY_RE.finditer(text):
            meta.assembly_refs.append({
                "id": m.group(1) or "",
                "name": m.group(2) or "",
            })

        # Measures with units
        # Pattern: MEASURE_WITH_UNIT(_, _, value, #unit_id)
        mwu_re = re.compile(
            r"#\d+=MEASURE_WITH_UNIT\s*\(\s*[^,]*,\s*([\d.E+\-]+)\s*,\s*#(\d+)\s*\)"
        )
        for m in mwu_re.finditer(text):
            try:
                value = float(m.group(1))
                unit_id = int(m.group(2))
                unit = meta.units.get(unit_id, "unit")
                meta.measures[unit] = value
            except ValueError:
                pass

        # Measure representation items
        for m in _MEASURE_RE.finditer(text):
            label = m.group(1)
            val_str = m.group(2)
            if label and val_str:
                try:
                    meta.measures[label] = float(val_str)
                except ValueError:
                    pass

        # Cartesian points → bounding box
        for m in _CARTESIAN_POINT_RE.finditer(text):
            try:
                coords = [float(x.strip()) for x in m.group(1).split(",")]
                if len(coords) == 3:
                    meta.points.append((coords[0], coords[1], coords[2]))
            except ValueError:
                pass

        return meta

    def _to_text(self, meta: STEPMetadata) -> str:
        """将STEP元数据转为自然语言，便于LLM抽取入库"""
        parts: List[str] = []

        if meta.file_description:
            parts.append(f"文件描述: {meta.file_description}")

        if meta.products:
            prod_names = ", ".join(p["name"] for p in meta.products)
            parts.append(f"包含产品/零件: {prod_names}")
            for p in meta.products:
                if p["description"]:
                    parts.append(f"  零件 {p['name']}: {p['description']}")

        if meta.part_numbers:
            for pn in meta.part_numbers:
                parts.append(f"图号/零件号: {pn}")

        if meta.materials:
            parts.append(f"材料: {', '.join(set(meta.materials))}")

        if meta.measures:
            for unit, value in meta.measures.items():
                parts.append(f"测量值 ({unit}): {value}")

        if meta.points:
            xs = [p[0] for p in meta.points]
            ys = [p[1] for p in meta.points]
            zs = [p[2] for p in meta.points]
            if xs:
                dims = f"X: {min(xs):.1f}-{max(xs):.1f}"
                if ys:
                    dims += f", Y: {min(ys):.1f}-{max(ys):.1f}"
                if zs:
                    dims += f", Z: {min(zs):.1f}-{max(zs):.1f}"
                parts.append(f"几何包围盒: {dims}")

        if meta.assembly_refs:
            parts.append(f"装配体引用: {len(meta.assembly_refs)} 个")
            for ref in meta.assembly_refs[:10]:
                parts.append(f"  子件: {ref.get('name', '')} (ID: {ref.get('id', '')})")

        return "\n".join(parts)
