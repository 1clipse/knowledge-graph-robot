from __future__ import annotations

import asyncio
from typing import Tuple

from openai import AsyncOpenAI


async def llm_chat(
    client: AsyncOpenAI,
    model: str,
    messages: list,
    temperature: float = 0.3,
    max_tokens: int = 2048,
) -> Tuple[str, int, int]:
    """Call LLM with streaming, return (content, prompt_tokens, completion_tokens).

    Qwen/vLLM always returns SSE streaming regardless of stream=False,
    so we must use stream=True and collect chunks.
    Adds a hard 120s total timeout since httpx timeout is per-chunk on streams.
    """
    async def _stream() -> Tuple[str, int, int]:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )

        content_parts: list[str] = []
        prompt_tokens = 0
        completion_tokens = 0

        async for chunk in response:
            if chunk.usage:
                prompt_tokens = chunk.usage.prompt_tokens or 0
                completion_tokens = chunk.usage.completion_tokens or 0
            for choice in chunk.choices:
                if choice.delta and choice.delta.content:
                    content_parts.append(choice.delta.content)

        return "".join(content_parts), prompt_tokens, completion_tokens

    return await asyncio.wait_for(_stream(), timeout=120.0)
