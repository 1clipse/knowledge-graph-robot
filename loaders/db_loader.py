from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger


class DBLoader:
    def __init__(self, connection_string: Optional[str] = None) -> None:
        self._connection_string = connection_string
        self._engines: Dict[str, Any] = {}

    def _get_engine(self, connection_string: Optional[str] = None) -> Any:
        try:
            from sqlalchemy import create_engine
        except ImportError:
            logger.error("sqlalchemy is required. Run: pip install sqlalchemy")
            raise

        conn_str = connection_string or self._connection_string
        if not conn_str:
            raise ValueError("No database connection string provided")

        if conn_str not in self._engines:
            self._engines[conn_str] = create_engine(conn_str)
        return self._engines[conn_str]

    def load(self, query: str, connection_string: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            import pandas as pd
        except ImportError:
            logger.error("pandas is required. Run: pip install pandas")
            raise

        engine = self._get_engine(connection_string)
        try:
            df = pd.read_sql(query, engine)
            df = df.where(df.notna(), None)
            rows = df.to_dict("records")
            logger.info(f"Loaded SQL query: {query[:50]}... ({len(rows)} rows)")
            return rows
        except Exception as e:
            logger.error(f"SQL query failed: {e}")
            raise

    def load_table(
        self, table_name: str, connection_string: Optional[str] = None, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        query = f"SELECT * FROM {table_name}"
        if limit:
            query += f" LIMIT {limit}"
        return self.load(query, connection_string)

    def get_table_names(self, connection_string: Optional[str] = None) -> List[str]:
        engine = self._get_engine(connection_string)
        try:
            from sqlalchemy import inspect
            inspector = inspect(engine)
            return inspector.get_table_names()
        except Exception as e:
            logger.error(f"Failed to get table names: {e}")
            raise

    def close(self) -> None:
        for engine in self._engines.values():
            try:
                engine.dispose()
            except Exception as e:
                logger.warning(f"Error disposing engine: {e}")
        self._engines.clear()
