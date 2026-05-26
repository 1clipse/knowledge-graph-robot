"""Evaluation API — RAGAS-style quality metrics for Q&A."""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import neo4j_client
from config.settings import get_config
from graph.query import GraphQuery

router = APIRouter()


class EvalItem(BaseModel):
    question: str = Field(..., description="测试问题")
    ground_truth: str = Field(default="", description="参考答案（可选）")


class EvalRequest(BaseModel):
    items: List[EvalItem] = Field(..., min_length=1, description="评估项列表")
    top_k: int = Field(default=5, description="检索实体数量")
    max_hops: int = Field(default=3, description="多跳推理跳数")


class MetricResult(BaseModel):
    faithfulness: float = 0.0
    answer_relevancy: float = 0.0
    context_precision: float = 0.0
    overall: float = 0.0


class EvalItemResult(BaseModel):
    question: str
    answer: str = ""
    ground_truth: str = ""
    metrics: MetricResult = Field(default_factory=MetricResult)
    details: Dict[str, str] = Field(default_factory=dict)


class EvalResponse(BaseModel):
    status: str
    model: str = ""
    results: List[EvalItemResult] = Field(default_factory=list)
    avg_faithfulness: float = 0.0
    avg_relevancy: float = 0.0
    avg_precision: float = 0.0
    avg_overall: float = 0.0


_EVAL_QA_PAIRS: List[Dict[str, str]] = [
    {"question": "FANUC M-20iA的负载是多少？", "ground_truth": "FANUC M-20iA的额定负载为20kg。"},
    {"question": "ABB是哪个国家的公司？", "ground_truth": "ABB是瑞士公司。"},
    {"question": "RV-40E是什么类型的减速器？", "ground_truth": "RV-40E是RV减速器。"},
    {"question": "FANUC的竞争对手有哪些？", "ground_truth": "FANUC的竞争对手包括ABB、KUKA等。"},
    {"question": "汽车白车身焊接使用什么类型的末端执行器？", "ground_truth": "点焊使用伺服焊枪。"},
]


def _build_context(question: str, top_k: int, max_hops: int) -> str:
    from api.routes.ask import _build_context as ask_build_context
    ctx, _, _ = ask_build_context(question, top_k, max_hops)
    return ctx


@router.post("/eval/run", response_model=EvalResponse)
async def run_evaluation(request: EvalRequest) -> EvalResponse:
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")

    config = get_config()

    from openai import AsyncOpenAI
    from extractors.llm_utils import llm_chat
    from eval.ragas_eval import RagasEvaluator, EvalSample

    client = AsyncOpenAI(base_url=config.llm.base_url, api_key=config.llm.api_key)
    evaluator = RagasEvaluator(client=client, model=config.llm.model)

    results: List[EvalItemResult] = []

    for item in request.items:
        # Step 1: Generate answer via the same QA flow
        context = _build_context(item.question, request.top_k, request.max_hops)

        if not context:
            results.append(EvalItemResult(
                question=item.question,
                answer="未找到相关信息",
                metrics=MetricResult(),
            ))
            continue

        answer = ""
        try:
            from api.routes.ask import QA_SYSTEM_PROMPT
            content, _, _ = await llm_chat(
                client=client, model=config.llm.model,
                messages=[
                    {"role": "system", "content": QA_SYSTEM_PROMPT},
                    {"role": "user", "content": f"知识图谱推理路径：\n{context}\n\n用户问题：{item.question}"},
                ],
                temperature=0.3, max_tokens=512,
            )
            answer = content or ""
        except Exception as e:
            logger.error(f"QA failed for '{item.question}': {e}")
            answer = f"生成失败: {e}"

        # Step 2: Evaluate
        sample = EvalSample(
            question=item.question,
            answer=answer,
            context=context,
            ground_truth=item.ground_truth,
        )
        er = await evaluator.evaluate(sample)

        results.append(EvalItemResult(
            question=item.question,
            answer=answer,
            ground_truth=item.ground_truth,
            metrics=MetricResult(
                faithfulness=er.faithfulness,
                answer_relevancy=er.answer_relevancy,
                context_precision=er.context_precision,
                overall=er.overall,
            ),
            details=er.details,
        ))

    # Aggregate
    n = len(results) or 1
    avg_f = sum(r.metrics.faithfulness for r in results) / n
    avg_r = sum(r.metrics.answer_relevancy for r in results) / n
    avg_p = sum(r.metrics.context_precision for r in results) / n
    avg_o = sum(r.metrics.overall for r in results) / n

    return EvalResponse(
        status="success",
        model=config.llm.model,
        results=results,
        avg_faithfulness=round(avg_f, 3),
        avg_relevancy=round(avg_r, 3),
        avg_precision=round(avg_p, 3),
        avg_overall=round(avg_o, 3),
    )


@router.get("/eval/dataset")
async def get_default_dataset() -> Dict[str, Any]:
    """Return the built-in eval QA pairs for manual inspection."""
    return {
        "count": len(_EVAL_QA_PAIRS),
        "items": _EVAL_QA_PAIRS,
    }
