from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from loguru import logger


class PDFLoader:
    def __init__(self, chunk_size: int = 2000, chunk_overlap: int = 200) -> None:
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    def load(self, file_path: str) -> str:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF file not found: {file_path}")

        text_parts: List[str] = []

        # Primary: pdfplumber for text + tables
        try:
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                for i, page in enumerate(pdf.pages):
                    page_text = page.extract_text() or ""
                    if page_text.strip():
                        text_parts.append(f"[Page {i + 1}]\n{page_text}")

                    # pdfplumber table extraction
                    tables = page.extract_tables()
                    for j, table in enumerate(tables):
                        table_text = self._table_to_text(table)
                        if table_text.strip():
                            text_parts.append(f"[Page {i + 1} Table {j + 1}]\n{table_text}")
        except ImportError:
            logger.error("pdfplumber not installed. Run: pip install pdfplumber")
            raise

        # Optional: Camelot for high-fidelity lattice-style tables
        camelot_tables = self._extract_camelot_tables(path)
        for ct in camelot_tables:
            text_parts.append(ct)

        full_text = "\n\n".join(text_parts)
        logger.info(f"Loaded PDF: {file_path} ({len(full_text)} chars, {len(text_parts)} parts)")
        return full_text

    def _extract_camelot_tables(self, path: Path) -> List[str]:
        """Try Camelot for better table extraction. Falls back silently."""
        try:
            import camelot
            tables = camelot.read_pdf(str(path), pages="all", flavor="lattice")
            if not tables:
                return []
            parts = []
            for i, t in enumerate(tables):
                df = t.df
                if df is not None and not df.empty:
                    header = " | ".join(str(c) for c in df.iloc[0])
                    rows = []
                    for r in range(1, len(df)):
                        rows.append(" | ".join(str(df.iloc[r, c]) for c in range(len(df.columns))))
                    parts.append(f"[Camelot Table {i + 1}]\n{header}\n" + "\n".join(rows))
            logger.info(f"Camelot extracted {len(parts)} tables")
            return parts
        except ImportError:
            return []
        except Exception as e:
            logger.debug(f"Camelot extraction skipped: {e}")
            return []

    def load_and_chunk(self, file_path: str) -> List[str]:
        full_text = self.load(file_path)
        return self._chunk_text(full_text)

    @staticmethod
    def _table_to_text(table: List[List[Optional[str]]]) -> str:
        """Convert a table to markdown-style text with header detection."""
        if not table:
            return ""
        rows: List[str] = []
        # First row as header
        header = [str(cell or "") for cell in table[0]]
        rows.append(" | ".join(header))
        rows.append(" | ".join(["---"] * len(header)))
        for row in table[1:]:
            cells = [str(cell or "") for cell in row]
            rows.append(" | ".join(cells))
        return "\n".join(rows)

    def _chunk_text(self, text: str) -> List[str]:
        if len(text) <= self._chunk_size:
            return [text] if text.strip() else []

        chunks: List[str] = []
        start = 0
        while start < len(text):
            end = start + self._chunk_size
            if end < len(text):
                search_start = max(end - 100, start)
                boundary = text.rfind("\n", search_start, end)
                if boundary > start:
                    end = boundary
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            start = end - self._chunk_overlap
            if start <= end - self._chunk_size:
                start = end

        logger.info(f"Split text into {len(chunks)} chunks")
        return chunks
