from __future__ import annotations

import pytest

from extractors.llm_extractor import ExtractedEntity, ExtractionResult
from pipeline.ingest import IngestPipeline


class FakePDFLoader:
    def __init__(self, *args, **kwargs):
        pass

    def load_and_chunk(self, file_path: str):
        return ["first chunk", "second chunk"]


class FakeFunnel:
    def __init__(self):
        self.seen = []

    async def extract(self, text: str, use_llm: bool = True) -> ExtractionResult:
        self.seen.append(text)
        return ExtractionResult(entities=[
            ExtractedEntity(name=text, type="Robot", confidence=0.9),
        ])


@pytest.mark.asyncio
async def test_ingest_pdf_extracts_each_chunk(monkeypatch):
    monkeypatch.setattr("pipeline.ingest.PDFLoader", FakePDFLoader)
    funnel = FakeFunnel()
    pipeline = IngestPipeline(neo4j_client=object())
    pipeline._funnel = funnel
    pipeline._write_result = lambda result: None

    result = await pipeline.ingest_pdf("manual.pdf")

    assert funnel.seen == ["first chunk", "second chunk"]
    assert {entity.name for entity in result.entities} == {"first chunk", "second chunk"}
