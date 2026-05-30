from __future__ import annotations

import pytest

from api.routes import ask


class TestAskFallback:
    def test_build_fallback_answer_uses_structured_paths(self):
        paths = [
            {
                "nodes": [
                    {"name": "FANUC"},
                    {"name": "M-20iA"},
                ],
                "edges": [
                    {"type": "manufactures"},
                ],
            }
        ]

        answer = ask._build_fallback_answer("FANUC 生产什么？", "ctx", paths)

        assert "LLM 服务暂时不可用" in answer
        assert "[P1]" in answer
        assert "FANUC" in answer
        assert "manufactures" in answer
        assert "M-20iA" in answer

    @pytest.mark.asyncio
    async def test_ask_question_returns_degraded_response_when_llm_fails(self, monkeypatch):
        raw_paths = [
            {
                "nodes": [
                    {"labels": ["Manufacturer"], "properties": {"name": "FANUC"}},
                    {"labels": ["Robot"], "properties": {"name": "M-20iA"}},
                ],
                "edges": [
                    {"type": "manufactures", "start": "FANUC", "end": "M-20iA"},
                ],
            }
        ]

        monkeypatch.setattr(
            ask,
            "_build_context",
            lambda db, question, top_k, max_hops: ("P1: FANUC manufactures M-20iA", [], raw_paths),
        )

        async def failing_llm_chat(*args, **kwargs):
            raise RuntimeError("LLM down")

        import extractors.llm_utils as llm_utils
        monkeypatch.setattr(llm_utils, "llm_chat", failing_llm_chat)

        response = await ask.ask_question(ask.AskRequest(question="FANUC 生产什么？"), db=object())

        assert response.status == "degraded"
        assert response.degraded is True
        assert "LLM 服务暂时不可用" in response.answer
        assert response.reasoning_paths
        assert response.context_used == "P1: FANUC manufactures M-20iA"

    @pytest.mark.asyncio
    async def test_stream_returns_degraded_answer_when_llm_fails(self, monkeypatch):
        raw_paths = [
            {
                "nodes": [
                    {"labels": ["Manufacturer"], "properties": {"name": "FANUC"}},
                    {"labels": ["Robot"], "properties": {"name": "M-20iA"}},
                ],
                "edges": [
                    {"type": "manufactures", "start": "FANUC", "end": "M-20iA"},
                ],
            }
        ]

        monkeypatch.setattr(
            ask,
            "_build_context",
            lambda db, question, top_k, max_hops: ("P1: FANUC manufactures M-20iA", [], raw_paths),
        )

        class FailingCompletions:
            async def create(self, *args, **kwargs):
                raise RuntimeError("LLM stream down")

        class FailingClient:
            def __init__(self, *args, **kwargs):
                self.chat = type("Chat", (), {"completions": FailingCompletions()})()

        monkeypatch.setattr("openai.AsyncOpenAI", FailingClient)

        response = await ask.ask_question_stream(ask.AskRequest(question="FANUC 生产什么？"), db=object())
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        body = "".join(chunks)

        assert "degraded" in body
        assert "LLM 服务暂时不可用" in body
        assert "FANUC" in body
        assert "M-20iA" in body
