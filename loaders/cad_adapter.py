"""CAD Adapter — transforms DXF/STEP metadata into ontology-aligned
graph entities (Drawing, Part, Assembly, Dimension, CADLayer).

Produces structured output that can be fed directly to GraphWriter
without an intermediate LLM extraction step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class CADGraphData:
    """Structured graph data extracted from CAD files."""
    entities: List[Dict[str, Any]] = field(default_factory=list)
    relations: List[Dict[str, Any]] = field(default_factory=list)
    file_path: str = ""
    file_format: str = ""


class CADAdapter:
    """Transforms CAD metadata (DXF/STEP) into ontology graph entities.

    Maps:
      - DXF blocks → Part / Assembly
      - STEP products → Part
      - STEP assembly refs → Assembly + assembly_contains_part
      - DXF/STEP dimensions → Dimension
      - DXF layers → CADLayer
      - Materials → Material
      - File → Drawing
    """

    _DIM_VALUE_RE = re.compile(r"([\d.]+)\s*(mm|cm|m|inch|deg|°)?")

    def from_dxf_metadata(
        self, meta: Any, file_path: str = ""
    ) -> CADGraphData:
        """Convert DXFMetadata to CAD graph data."""
        data = CADGraphData(file_path=file_path, file_format="DXF")

        file_stem = Path(file_path).stem if file_path else "unnamed"
        drawing_name = self._sanitize(file_stem)

        # 1. Drawing entity
        drawing_entity = {
            "name": drawing_name,
            "type": "Drawing",
            "properties": {
                "name": drawing_name,
                "file_format": "DXF",
                "drawing_type": self._infer_drawing_type(meta),
                "version": getattr(meta, "file_version", ""),
            },
            "confidence": 0.95,
            "source": file_path,
        }
        data.entities.append(drawing_entity)

        # 2. CADLayer entities + drawing_has_layer relations
        for layer_name in getattr(meta, "layers", []) or []:
            layer_entity = {
                "name": layer_name,
                "type": "CADLayer",
                "properties": {"name": layer_name},
                "confidence": 0.95,
                "source": file_path,
            }
            data.entities.append(layer_entity)
            data.relations.append({
                "source": {"name": drawing_name, "type": "Drawing"},
                "target": {"name": layer_name, "type": "CADLayer"},
                "relation_type": "drawing_has_layer",
                "properties": {},
                "confidence": 0.95,
            })

        # 3. Block → Part / Assembly
        block_names = getattr(meta, "block_names", []) or []
        for bname in block_names:
            is_assembly = self._is_assembly_name(bname)
            etype = "Assembly" if is_assembly else "Part"
            entity = {
                "name": bname,
                "type": etype,
                "properties": {
                    "name": bname,
                    "part_number": bname,
                },
                "confidence": 0.9,
                "source": file_path,
            }
            data.entities.append(entity)

            if is_assembly:
                data.relations.append({
                    "source": {"name": drawing_name, "type": "Drawing"},
                    "target": {"name": bname, "type": "Assembly"},
                    "relation_type": "drawing_defines_assembly",
                    "properties": {},
                    "confidence": 0.9,
                })
            else:
                data.relations.append({
                    "source": {"name": drawing_name, "type": "Drawing"},
                    "target": {"name": bname, "type": "Part"},
                    "relation_type": "drawing_defines",
                    "properties": {},
                    "confidence": 0.9,
                })

        # 4. INSERT references → assembly_contains_part
        inserts = getattr(meta, "inserts", []) or []
        for ins in inserts:
            ins_name = ins.get("name", "")
            if not ins_name or ins_name.startswith("*"):
                continue
            # INSERT is a reference to a block — it's a part within a drawing
            for bname in block_names:
                if ins_name == bname:
                    part_entity = {
                        "name": ins_name,
                        "type": "Part",
                        "properties": {"name": ins_name, "part_number": ins_name},
                        "confidence": 0.85,
                        "source": file_path,
                    }
                    _add_unique_entity(data.entities, part_entity)
                    data.relations.append({
                        "source": {"name": drawing_name, "type": "Drawing"},
                        "target": {"name": ins_name, "type": "Part"},
                        "relation_type": "drawing_defines",
                        "properties": {"layer": ins.get("layer", "")},
                        "confidence": 0.85,
                    })
                    break

        # 5. Dimensions → Dimension entities
        dim_texts = getattr(meta, "dimensions", []) or []
        for i, dim_text in enumerate(dim_texts[:50]):
            dim_name = self._sanitize_dim_name(dim_text, i)
            parsed = self._parse_dimension_value(dim_text)
            dim_entity: Dict[str, Any] = {
                "name": dim_name,
                "type": "Dimension",
                "properties": {
                    "name": dim_text[:80],
                    "dimension_type": parsed.get("type", ""),
                },
                "confidence": 0.85,
                "source": file_path,
            }
            if parsed.get("value") is not None:
                dim_entity["properties"]["value"] = parsed["value"]
            if parsed.get("unit"):
                dim_entity["properties"]["unit"] = parsed["unit"]
            data.entities.append(dim_entity)

            # Link dimension to a part if block_names exist
            if block_names:
                # Attach to first part as heuristic
                data.relations.append({
                    "source": {"name": block_names[0], "type": "Part"},
                    "target": {"name": dim_name, "type": "Dimension"},
                    "relation_type": "part_has_dimension",
                    "properties": {},
                    "confidence": 0.7,
                })

        # 6. Texts → attribute enrichment (attach keywords to drawing)
        texts = (getattr(meta, "texts", []) or []) + (getattr(meta, "mtexts", []) or [])
        material_candidates = self._extract_materials(texts)
        for mat in material_candidates:
            mat_entity = {
                "name": mat,
                "type": "Material",
                "properties": {"name": mat},
                "confidence": 0.7,
                "source": file_path,
            }
            _add_unique_entity(data.entities, mat_entity)
            # Link to first part
            if block_names:
                data.relations.append({
                    "source": {"name": block_names[0], "type": "Part"},
                    "target": {"name": mat, "type": "Material"},
                    "relation_type": "part_made_of",
                    "properties": {},
                    "confidence": 0.6,
                })

        logger.info(
            f"CADAdapter (DXF): {len(data.entities)} entities, "
            f"{len(data.relations)} relations from {file_path}"
        )
        return data

    def from_step_metadata(
        self, meta: Any, file_path: str = ""
    ) -> CADGraphData:
        """Convert STEPMetadata to CAD graph data."""
        data = CADGraphData(file_path=file_path, file_format="STEP")

        file_stem = Path(file_path).stem if file_path else "unnamed"
        drawing_name = self._sanitize(file_stem)

        # 1. Drawing entity
        drawing_entity = {
            "name": drawing_name,
            "type": "Drawing",
            "properties": {
                "name": drawing_name,
                "file_format": "STEP",
                "drawing_type": "装配图" if getattr(meta, "assembly_refs", []) else "零件图",
                "description": getattr(meta, "file_description", ""),
            },
            "confidence": 0.95,
            "source": file_path,
        }
        data.entities.append(drawing_entity)

        # 2. Products → Part entities
        products = getattr(meta, "products", []) or []
        part_numbers = getattr(meta, "part_numbers", []) or []
        for i, prod in enumerate(products):
            pname = prod.get("name", "")
            if not pname:
                continue
            pn = part_numbers[i] if i < len(part_numbers) else ""
            part_entity = {
                "name": pname,
                "type": "Part",
                "properties": {
                    "name": pname,
                    "part_number": pn or pname,
                    "description": prod.get("description", ""),
                },
                "confidence": 0.9,
                "source": file_path,
            }
            data.entities.append(part_entity)
            data.relations.append({
                "source": {"name": drawing_name, "type": "Drawing"},
                "target": {"name": pname, "type": "Part"},
                "relation_type": "drawing_defines",
                "properties": {},
                "confidence": 0.9,
            })

        # 3. Assembly refs → Assembly + relations
        assembly_refs = getattr(meta, "assembly_refs", []) or []
        if assembly_refs:
            top_assembly = {
                "name": drawing_name,
                "type": "Assembly",
                "properties": {
                    "name": drawing_name,
                    "assembly_type": "总成",
                    "level": 0,
                },
                "confidence": 0.9,
                "source": file_path,
            }
            data.entities.append(top_assembly)
            data.relations.append({
                "source": {"name": drawing_name, "type": "Drawing"},
                "target": {"name": drawing_name, "type": "Assembly"},
                "relation_type": "drawing_defines_assembly",
                "properties": {},
                "confidence": 0.9,
            })

            for ref in assembly_refs:
                ref_name = ref.get("name", "") or ref.get("id", "")
                if not ref_name:
                    continue
                sub_part = {
                    "name": ref_name,
                    "type": "Part",
                    "properties": {"name": ref_name, "part_number": ref_name},
                    "confidence": 0.85,
                    "source": file_path,
                }
                _add_unique_entity(data.entities, sub_part)
                data.relations.append({
                    "source": {"name": drawing_name, "type": "Assembly"},
                    "target": {"name": ref_name, "type": "Part"},
                    "relation_type": "assembly_contains_part",
                    "properties": {},
                    "confidence": 0.85,
                })

        # 4. Materials → part_made_of
        materials = getattr(meta, "materials", []) or []
        for mat in materials:
            mat_entity = {
                "name": mat,
                "type": "Material",
                "properties": {"name": mat},
                "confidence": 0.85,
                "source": file_path,
            }
            _add_unique_entity(data.entities, mat_entity)
            # Link to first part
            if products:
                data.relations.append({
                    "source": {"name": products[0].get("name", ""), "type": "Part"},
                    "target": {"name": mat, "type": "Material"},
                    "relation_type": "part_made_of",
                    "properties": {},
                    "confidence": 0.8,
                })

        # 5. Measures → Dimension entities
        measures = getattr(meta, "measures", {}) or {}
        for label, value in measures.items():
            dim_name = self._sanitize(label) if label else f"dim_{len(data.entities)}"
            dim_entity = {
                "name": dim_name,
                "type": "Dimension",
                "properties": {
                    "name": label or dim_name,
                    "dimension_type": "linear",
                    "value": value,
                    "unit": "mm",
                },
                "confidence": 0.9,
                "source": file_path,
            }
            data.entities.append(dim_entity)
            if products:
                data.relations.append({
                    "source": {"name": products[0].get("name", ""), "type": "Part"},
                    "target": {"name": dim_name, "type": "Dimension"},
                    "relation_type": "part_has_dimension",
                    "properties": {"is_critical": False},
                    "confidence": 0.7,
                })

        # 6. Part numbers not already captured
        for i, pn in enumerate(part_numbers):
            if i >= len(products):
                part_entity = {
                    "name": pn,
                    "type": "Part",
                    "properties": {"name": pn, "part_number": pn},
                    "confidence": 0.85,
                    "source": file_path,
                }
                _add_unique_entity(data.entities, part_entity)

        logger.info(
            f"CADAdapter (STEP): {len(data.entities)} entities, "
            f"{len(data.relations)} relations from {file_path}"
        )
        return data

    # ── helpers ──

    @staticmethod
    def _sanitize(name: str) -> str:
        return name.strip().replace(" ", "_").replace("/", "-")

    @staticmethod
    def _sanitize_dim_name(text: str, idx: int) -> str:
        short = text[:40].strip().replace(" ", "_")
        return short if short else f"dim_{idx}"

    @staticmethod
    def _infer_drawing_type(meta: Any) -> str:
        blocks = getattr(meta, "block_names", []) or []
        inserts = getattr(meta, "inserts", []) or []
        if len(inserts) > 2 or len(blocks) > 3:
            return "装配图"
        if blocks:
            return "零件图"
        return "电气图"

    @staticmethod
    def _is_assembly_name(name: str) -> bool:
        lower = name.lower()
        asm_keywords = ["asm", "assy", "assembly", "装配", "总成", "subasm", "subassembly"]
        return any(kw in lower for kw in asm_keywords)

    @staticmethod
    def _parse_dimension_value(text: str) -> Dict[str, Any]:
        """Parse dimension text like 'M10x1.5', '50±0.1', 'R5', '30mm'."""
        result: Dict[str, Any] = {"type": "linear"}
        text = text.strip()

        # Diameter: Ø50 or ⌀50
        if text.startswith("Ø") or text.startswith("⌀"):
            result["type"] = "diameter"
            m = CADAdapter._DIM_VALUE_RE.search(text[1:])
            if m:
                result["value"] = float(m.group(1))
                result["unit"] = m.group(2) or "mm"

        # Radius: R5
        elif text.upper().startswith("R") and len(text) > 1:
            result["type"] = "radius"
            m = CADAdapter._DIM_VALUE_RE.search(text[1:])
            if m:
                result["value"] = float(m.group(1))
                result["unit"] = m.group(2) or "mm"

        # Thread: M10, M10x1.5
        elif text.upper().startswith("M") and len(text) > 1:
            result["type"] = "thread"
            m = re.match(r"[Mm]([\d.]+)(?:[xX]([\d.]+))?", text)
            if m:
                result["value"] = float(m.group(1))
                result["unit"] = "mm"

        # Generic with unit
        else:
            m = CADAdapter._DIM_VALUE_RE.search(text)
            if m:
                result["value"] = float(m.group(1))
                result["unit"] = m.group(2) or "mm"

        return result

    @staticmethod
    def _extract_materials(texts: List[str]) -> List[str]:
        """Extract material names from annotation texts."""
        mat_keywords = [
            "钢", "铁", "铝", "铜", "不锈钢", "合金", "铸铁",
            "STEEL", "ALUMINUM", "ALUMINIUM", "COPPER", "IRON",
            "BRASS", "TITANIUM", "PLASTIC", "NYLON", "RUBBER",
            "Q235", "45#", "40Cr", "6061", "7075", "SUS304", "SUS316",
        ]
        found: List[str] = []
        seen = set()
        for t in texts:
            t_upper = t.upper()
            for kw in mat_keywords:
                if kw.upper() in t_upper and kw not in seen:
                    found.append(kw)
                    seen.add(kw)
        return found


def _add_unique_entity(entities: List[Dict[str, Any]], new_entity: Dict[str, Any]) -> None:
    """Add entity only if no existing entity has the same name+type."""
    name = new_entity.get("name", "")
    etype = new_entity.get("type", "")
    for existing in entities:
        if existing.get("name") == name and existing.get("type") == etype:
            return
    entities.append(new_entity)
