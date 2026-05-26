from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class CSVLoader:
    def __init__(self, encoding: str = "utf-8") -> None:
        self._encoding = encoding

    def load(self, file_path: str) -> List[Dict[str, Any]]:
        try:
            import pandas as pd
        except ImportError:
            logger.error("pandas is required. Run: pip install pandas")
            raise

        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"CSV file not found: {file_path}")

        df = pd.read_csv(path, encoding=self._encoding)
        df = df.where(df.notna(), None)
        rows = df.to_dict("records")
        logger.info(f"Loaded CSV: {file_path} ({len(rows)} rows, {len(df.columns)} columns)")
        return rows

    def load_multiple(self, file_paths: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        results: Dict[str, List[Dict[str, Any]]] = {}
        for fp in file_paths:
            try:
                results[fp] = self.load(fp)
            except Exception as e:
                logger.error(f"Failed to load CSV {fp}: {e}")
                results[fp] = []
        return results
