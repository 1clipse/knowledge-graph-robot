from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from loguru import logger


class PDFLoader:
    def __init__(self, chunk_size: int = 2000, chunk_overlap: int = 200) -> None:
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    def load(self, file_path: str) -> str:
        try:
            import pdfplumber
        except ImportError:
            logger.error("pdfplumber is not installed. Run: pip install pdfplumber")
            raise

        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF file not found: {file_path}")

        text_parts: List[str] = []
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text() or ""
                if page_text.strip():
                    text_parts.append(page_text)
                tables = page.extract_tables()
                for table in tables:
                    table_text = self._table_to_text(table)
                    if table_text.strip():
                        text_parts.append(table_text)

        full_text = "\n\n".join(text_parts)
        logger.info(f"Loaded PDF: {file_path} ({len(full_text)} chars)")
        return full_text

    def load_and_chunk(self, file_path: str) -> List[str]:
        full_text = self.load(file_path)
        return self._chunk_text(full_text)

    def _table_to_text(self, table: List[List[Optional[str]]]) -> str:
        if not table:
            return ""
        rows: List[str] = []
        for row in table:
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
