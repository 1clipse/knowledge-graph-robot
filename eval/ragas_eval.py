"""RAGAS-style evaluation metrics using LLM-as-judge.

Metrics:
- Faithfulness: Are all claims in the answer supported by the context?
- Answer Relevancy: Does the answer address the question?
- Context Precision: Is the retrieved context precisely relevant to the question?
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class EvalSample:
    question: str
    answer: str
    context: str
    ground_truth: str = ""


@dataclass
class EvalResult:
    faithfulness: float = 0.0
    answer_relevancy: float = 0.0
    context_precision: float = 0.0
    overall: float = 0.0
    details: Dict[str, str] = field(default_factory=dict)


FAITHFULNESS_TMPL = """你是一个评估助手。判断一个回答中的陈述是否都能从给定的上下文中推导出来。

请按以下步骤操作：
1. 将回答分解为独立的陈述句
2. 判断每个陈述是否可以从上下文中得到支持
3. 评分：所有陈述都有上下文支持得10分，完全无支持得0分

请只输出一个JSON格式：{"score": <0-10的整数>, "reason": "<简要理由>"}

上下文：
__CONTEXT__

回答：
__ANSWER__"""

RELEVANCY_TMPL = """你是一个评估助手。判断一个回答是否直接回应了用户的问题。

请按以下步骤操作：
1. 检查回答是否直接针对问题
2. 检查回答中是否包含与问题无关的内容
3. 评分：完全相关且不冗余得10分，完全不相关得0分

请只输出一个JSON格式：{"score": <0-10的整数>, "reason": "<简要理由>"}

问题：
__QUESTION__

回答：
__ANSWER__"""

PRECISION_TMPL = """你是一个评估助手。判断检索到的上下文是否精确地帮助回答问题。

请按以下步骤操作：
1. 检查上下文中的每条信息是否与问题相关
2. 判断是否有大量无关信息
3. 评分：所有上下文都直接相关得10分，含有大量无关信息得0分

请只输出一个JSON格式：{"score": <0-10的整数>, "reason": "<简要理由>"}

问题：
__QUESTION__

上下文：
__CONTEXT__"""

_SCORE_RE = re.compile(r'"score"\s*:\s*(\d+)')


def _parse_score(text: str) -> float:
    m = _SCORE_RE.search(text)
    if m:
        return float(m.group(1)) / 10.0
    return 0.0


def _parse_reason(text: str) -> str:
    m = re.search(r'"reason"\s*:\s*"([^"]*)"', text)
    return m.group(1) if m else ""


class RagasEvaluator:
    """Evaluate Q&A quality with LLM-as-judge."""

    def __init__(self, client, model: str):
        self._client = client
        self._model = model

    async def evaluate(self, sample: EvalSample) -> EvalResult:
        """Run all three metrics on a single sample."""
        from extractors.llm_utils import llm_chat

        # Faithfulness
        faith_score = 0.0
        faith_reason = ""
        try:
            msg = (FAITHFULNESS_TMPL
                   .replace("__CONTEXT__", sample.context[:2000])
                   .replace("__ANSWER__", sample.answer[:1000]))
            content, _, _ = await llm_chat(
                client=self._client, model=self._model,
                messages=[{"role": "user", "content": msg}],
                temperature=0.0, max_tokens=256,
            )
            faith_score = _parse_score(content or "")
            faith_reason = _parse_reason(content or "")
        except Exception as e:
            logger.warning(f"Faithfulness eval failed: {e}")

        # Answer Relevancy
        rel_score = 0.0
        rel_reason = ""
        try:
            msg = (RELEVANCY_TMPL
                   .replace("__QUESTION__", sample.question)
                   .replace("__ANSWER__", sample.answer[:1000]))
            content, _, _ = await llm_chat(
                client=self._client, model=self._model,
                messages=[{"role": "user", "content": msg}],
                temperature=0.0, max_tokens=256,
            )
            rel_score = _parse_score(content or "")
            rel_reason = _parse_reason(content or "")
        except Exception as e:
            logger.warning(f"Relevancy eval failed: {e}")

        # Context Precision
        ctx_score = 0.0
        ctx_reason = ""
        try:
            msg = (PRECISION_TMPL
                   .replace("__QUESTION__", sample.question)
                   .replace("__CONTEXT__", sample.context[:2000]))
            content, _, _ = await llm_chat(
                client=self._client, model=self._model,
                messages=[{"role": "user", "content": msg}],
                temperature=0.0, max_tokens=256,
            )
            ctx_score = _parse_score(content or "")
            ctx_reason = _parse_reason(content or "")
        except Exception as e:
            logger.warning(f"Precision eval failed: {e}")

        overall = (faith_score + rel_score + ctx_score) / 3.0

        return EvalResult(
            faithfulness=round(faith_score, 3),
            answer_relevancy=round(rel_score, 3),
            context_precision=round(ctx_score, 3),
            overall=round(overall, 3),
            details={
                "faithfulness_reason": faith_reason,
                "relevancy_reason": rel_reason,
                "precision_reason": ctx_reason,
            },
        )

    async def evaluate_batch(self, samples: List[EvalSample]) -> List[EvalResult]:
        results = []
        for sample in samples:
            r = await self.evaluate(sample)
            results.append(r)
        return results
