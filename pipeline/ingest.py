from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict, Optional

from loguru import logger

from config.settings import get_config
from extractors.llm_extractor import LLMExtractor, ExtractionResult
from extractors.funnel import ExtractionFunnel, merge_results
from extractors.structured_mapper import StructuredMapper
from graph.client import Neo4jClient
from graph.schema_manager import SchemaManager
from graph.writer import GraphWriter
from loaders.csv_loader import CSVLoader
from loaders.db_loader import DBLoader
from loaders.pdf_loader import PDFLoader
from loaders.web_loader import WebLoader


class IngestPipeline:
    def __init__(self, neo4j_client: Optional[Neo4jClient] = None) -> None:
        self._config = get_config()
        self._client = neo4j_client or Neo4jClient()
        self._schema_manager: Optional[SchemaManager] = None
        self._llm_extractor: Optional[LLMExtractor] = None
        self._funnel: Optional[ExtractionFunnel] = None
        self._mapper = StructuredMapper()

    def initialize(self) -> None:
        self._client.connect()
        self._schema_manager = SchemaManager(self._client)
        self._schema_manager.initialize_schema()
        self._llm_extractor = LLMExtractor()
        self._funnel = ExtractionFunnel(llm_extractor=self._llm_extractor)
        logger.info("IngestPipeline initialized")

    def _write_result(self, result: ExtractionResult) -> None:
        if not result.entities:
            return
        writer = GraphWriter(self._client, self._schema_manager)
        writer.write(result)

    async def ingest_text(self, text: str, use_llm: bool = True) -> ExtractionResult:
        funnel = self._funnel or ExtractionFunnel(llm_extractor=self._llm_extractor)
        result = await funnel.extract(text, use_llm=use_llm)
        self._write_result(result)
        return result

    async def ingest_pdf(self, file_path: str) -> ExtractionResult:
        loader = PDFLoader(
            chunk_size=self._config.extraction.chunk_size,
            chunk_overlap=self._config.extraction.chunk_overlap,
        )
        chunks = loader.load_and_chunk(file_path)
        logger.info(f"PDF loaded: {len(chunks)} chunks")

        funnel = self._funnel or ExtractionFunnel(llm_extractor=self._llm_extractor)
        chunk_results = [await funnel.extract(chunk, use_llm=True) for chunk in chunks]
        result = ExtractionResult()
        for chunk_result in chunk_results:
            result = merge_results(result, chunk_result)

        self._write_result(result)
        return result

    async def ingest_url(self, url: str, selector: Optional[str] = None) -> ExtractionResult:
        loader = WebLoader()
        text = loader.load(url, selector)
        return await self.ingest_text(text)

    def ingest_csv(self, file_path: str) -> ExtractionResult:
        result = self._mapper.map_csv(file_path)
        self._write_result(result)
        return result

    def ingest_directory(self, dir_path: str) -> Dict[str, ExtractionResult]:
        path = Path(dir_path)
        if not path.is_dir():
            raise ValueError(f"Not a directory: {dir_path}")

        results: Dict[str, ExtractionResult] = {}
        for file_path in path.rglob("*"):
            if file_path.is_file():
                suffix = file_path.suffix.lower()
                try:
                    if suffix == ".pdf":
                        results[str(file_path)] = asyncio.run(
                            self.ingest_pdf(str(file_path))
                        )
                    elif suffix == ".csv":
                        results[str(file_path)] = self.ingest_csv(str(file_path))
                    elif suffix in (".txt", ".md"):
                        text = file_path.read_text(encoding="utf-8")
                        results[str(file_path)] = asyncio.run(
                            self.ingest_text(text)
                        )
                    else:
                        logger.info(f"Skipping unsupported file: {file_path}")
                except Exception as e:
                    logger.error(f"Failed to ingest {file_path}: {e}")
                    results[str(file_path)] = ExtractionResult()

        return results

    def load_sample_data(self) -> None:
        if self._schema_manager:
            self._schema_manager.load_sample_data()

    def close(self) -> None:
        self._client.close()
