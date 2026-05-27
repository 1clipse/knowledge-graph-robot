"""Plain text file loader with encoding detection."""

from __future__ import annotations

import os
from typing import List

from loguru import logger


class TXTLoader:
    """Load and extract text from .txt and other plain-text files.

    Attempts UTF-8 first, then falls back to other common Chinese encodings
    (GB18030, GBK) so files exported from legacy Windows tools are readable.
    """

    ENCODINGS = ("utf-8", "gb18030", "gbk", "latin-1")

    def load(self, file_path: str) -> str:
        """Read a text file with encoding detection, returning its full content."""
        for enc in self.ENCODINGS:
            try:
                with open(file_path, "r", encoding=enc) as f:
                    text = f.read()
                logger.debug(f"TXT loaded with {enc}: {len(text)} chars from {os.path.basename(file_path)}")
                return text
            except (UnicodeDecodeError, UnicodeError):
                continue
        # Last resort: binary read
        with open(file_path, "rb") as f:
            raw = f.read()
        text = raw.decode("utf-8", errors="ignore")
        logger.warning(f"TXT fallback decode: {len(text)} chars from {os.path.basename(file_path)}")
        return text

    def load_and_chunk(self, file_path: str, chunk_size: int = 2000) -> List[str]:
        """Load and optionally chunk the text (returns single-element list for small files)."""
        text = self.load(file_path)
        if len(text) <= chunk_size:
            return [text]
        chunks: List[str] = []
        for i in range(0, len(text), chunk_size):
            chunks.append(text[i : i + chunk_size])
        return chunks
