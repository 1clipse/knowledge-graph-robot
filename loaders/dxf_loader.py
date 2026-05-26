from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from loguru import logger


class DXFMetadata:
    """DXF 解析结果"""
    def __init__(self) -> None:
        self.file_version: str = ""
        self.layers: List[str] = []
        self.layer_count: int = 0
        self.block_names: List[str] = []
        self.block_count: int = 0
        self.texts: List[str] = []
        self.mtexts: List[str] = []
        self.dimensions: List[str] = []
        self.inserts: List[Dict[str, Any]] = []
        self.attributes: List[Dict[str, str]] = []
        self.line_count: int = 0
        self.circle_count: int = 0
        self.arc_count: int = 0
        self.polyline_count: int = 0
        self.entity_count: int = 0
        self.extents: Dict[str, float] = {}
        # Extracted keywords for knowledge graph
        self.keywords: Set[str] = set()


class DXFLoader:
    """DXF 文件解析器 — 提取元数据和标注文字，转为自然语言供 LLM 抽取知识图谱"""

    SUPPORTED_EXTENSIONS = {".dxf"}

    def load_metadata(self, file_path: str) -> DXFMetadata:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"DXF file not found: {file_path}")

        try:
            import ezdxf
        except ImportError:
            logger.error("ezdxf is not installed. Run: pip install ezdxf")
            raise

        meta = DXFMetadata()

        try:
            doc = ezdxf.readfile(str(path))
        except Exception as e:
            logger.error(f"Failed to read DXF file: {e}")
            raise

        # File version
        meta.file_version = doc.dxfversion

        # Layers
        try:
            for layer in doc.layers:
                meta.layers.append(layer.dxf.name)
            meta.layer_count = len(meta.layers)
        except Exception as e:
            logger.warning(f"Failed to read layers: {e}")

        # Blocks (components / sub-assemblies)
        try:
            for block in doc.blocks:
                if not block.name.startswith("*"):  # skip special blocks
                    meta.block_names.append(block.name)
            meta.block_count = len(meta.block_names)
        except Exception as e:
            logger.warning(f"Failed to read blocks: {e}")

        # Entities in modelspace
        try:
            msp = doc.modelspace()
            for entity in msp:
                meta.entity_count += 1
                etype = entity.dxftype()

                if etype == "TEXT":
                    txt = entity.dxf.text.strip()
                    if txt and len(txt) > 1:
                        meta.texts.append(txt)
                        meta.keywords.update(self._tokenize(txt))
                elif etype == "MTEXT":
                    txt = self._strip_mtext(entity.plain_text() if hasattr(entity, "plain_text") else entity.dxf.text)
                    if txt and len(txt) > 1:
                        meta.mtexts.append(txt)
                        meta.keywords.update(self._tokenize(txt))
                elif etype == "DIMENSION":
                    try:
                        dim_text = entity.dxf.text.strip() if entity.dxf.hasattr("text") else ""
                        if dim_text:
                            meta.dimensions.append(dim_text)
                            meta.keywords.update(self._tokenize(dim_text))
                    except Exception:
                        pass
                elif etype == "INSERT":
                    try:
                        ins = {
                            "name": entity.dxf.name,
                            "layer": entity.dxf.layer,
                        }
                        if entity.dxf.hasattr("insert"):
                            ins["x"] = round(entity.dxf.insert.x, 2)
                            ins["y"] = round(entity.dxf.insert.y, 2)
                        meta.inserts.append(ins)
                        meta.keywords.add(entity.dxf.name)
                    except Exception:
                        pass
                elif etype == "ATTRIB":
                    try:
                        meta.attributes.append({
                            "tag": entity.dxf.tag or "",
                            "text": entity.dxf.text or "",
                        })
                        meta.keywords.update(self._tokenize(entity.dxf.text or ""))
                        meta.keywords.update(self._tokenize(entity.dxf.tag or ""))
                    except Exception:
                        pass
                elif etype == "LINE":
                    meta.line_count += 1
                elif etype == "CIRCLE":
                    meta.circle_count += 1
                elif etype == "ARC":
                    meta.arc_count += 1
                elif etype in ("LWPOLYLINE", "POLYLINE"):
                    meta.polyline_count += 1
        except Exception as e:
            logger.warning(f"Failed to read entities: {e}")

        # Extent
        try:
            header = doc.header
            extmin = header.get("$EXTMIN", None)
            extmax = header.get("$EXTMAX", None)
            if extmin and extmax:
                meta.extents = {
                    "x_min": round(extmin.x, 2), "y_min": round(extmin.y, 2),
                    "x_max": round(extmax.x, 2), "y_max": round(extmax.y, 2),
                    "width": round(extmax.x - extmin.x, 2),
                    "height": round(extmax.y - extmin.y, 2),
                }
        except Exception:
            pass

        logger.info(
            f"DXF loaded: {meta.entity_count} entities, "
            f"{meta.layer_count} layers, {meta.block_count} blocks, "
            f"{len(meta.texts)} texts, {len(meta.dimensions)} dims"
        )
        return meta

    def load_as_text(self, file_path: str) -> str:
        """转为自然语言描述，供 LLM 抽取入库"""
        meta = self.load_metadata(file_path)
        return self._to_text(meta)

    def _to_text(self, meta: DXFMetadata) -> str:
        parts: List[str] = []

        parts.append(f"DXF 文件版本: {meta.file_version}")

        if meta.block_names:
            parts.append(f"包含 {meta.block_count} 个图块/组件:")
            for name in meta.block_names[:30]:
                parts.append(f"  - {name}")

        if meta.layers:
            parts.append(f"包含 {meta.layer_count} 个图层:")
            for name in meta.layers[:20]:
                parts.append(f"  - {name}")

        if meta.inserts:
            insert_names: Dict[str, int] = {}
            for ins in meta.inserts:
                n = ins.get("name", "")
                if n:
                    insert_names[n] = insert_names.get(n, 0) + 1
            parts.append(f"引用的图块 ({len(meta.inserts)} 个实例):")
            for name, count in sorted(insert_names.items(), key=lambda x: -x[1])[:15]:
                parts.append(f"  - {name}: {count} 个")

        # Text annotations — the most valuable for knowledge graph
        all_texts = meta.texts + meta.mtexts
        if all_texts:
            # Deduplicate
            unique_texts = list(dict.fromkeys(t for t in all_texts if len(t) > 2))
            parts.append(f"标注文字 ({len(unique_texts)} 条):")
            for t in unique_texts[:40]:
                if len(t) > 80:
                    t = t[:77] + "..."
                parts.append(f"  - {t}")

        if meta.dimensions:
            unique_dims = list(dict.fromkeys(meta.dimensions))
            parts.append(f"尺寸标注 ({len(unique_dims)} 条):")
            for d in unique_dims[:20]:
                parts.append(f"  - {d}")

        if meta.attributes:
            parts.append(f"属性定义 ({len(meta.attributes)} 条):")
            for a in meta.attributes[:15]:
                parts.append(f"  - {a.get('tag', '')} = {a.get('text', '')}")

        if meta.extents:
            e = meta.extents
            parts.append(
                f"图纸范围: {e.get('width', '?')} x {e.get('height', '?')} "
                f"(X: {e.get('x_min', '?')}~{e.get('x_max', '?')}, "
                f"Y: {e.get('y_min', '?')}~{e.get('y_max', '?')})"
            )

        parts.append(
            f"图形统计: {meta.line_count} 条线段, {meta.circle_count} 个圆, "
            f"{meta.arc_count} 个圆弧, {meta.polyline_count} 条多段线, "
            f"共 {meta.entity_count} 个实体"
        )

        return "\n".join(parts)

    def _strip_mtext(self, text: str) -> str:
        """去除 MText 的格式代码"""
        import re
        text = re.sub(r'\\[A-Za-z]+[;\s]*', '', text)
        text = re.sub(r'\{[^}]*\}', '', text)
        text = re.sub(r'\\[PXQWHTS][^;]*;', '', text)
        return text.strip()

    @staticmethod
    def _tokenize(text: str) -> Set[str]:
        """提取关键词"""
        tokens: Set[str] = set()
        for part in text.replace(",", " ").replace(";", " ").replace("/", " ").split():
            part = part.strip("()（）【】[]{}，。；：：. ")
            if len(part) >= 2 and not part.isdigit():
                tokens.add(part)
        return tokens
