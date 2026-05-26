from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import neo4j_client
from config.settings import get_config
from graph.query import GraphQuery

router = APIRouter()


class AskRequest(BaseModel):
    question: str = Field(..., description="用户问题")
    top_k: int = Field(default=5, description="检索相关实体数量")
    max_hops: int = Field(default=3, description="多跳推理跳数")


class AskResponse(BaseModel):
    status: str
    question: str
    answer: str = ""
    relevant_entities: List[Dict[str, Any]] = Field(default_factory=list)
    reasoning_paths: List[Dict[str, Any]] = Field(default_factory=list)
    context_used: str = ""


QA_SYSTEM_PROMPT = """你是一个工业机器人领域的知识问答助手。基于提供的知识图谱上下文信息，回答用户的问题。

上下文包含从知识图谱中检索到的"推理路径"，每条路径展示了实体之间的多跳关系链。
请利用这些关系链进行推理回答。

规则：
1. 优先使用提供的上下文信息回答，利用多跳关系链进行推理
2. 如果上下文信息不足以回答问题，明确说明并给出你了解的相关信息
3. 回答要准确、专业、有条理，说明推理过程
4. 涉及具体参数时请给出数值
5. 用中文回答
"""


def _build_context(question: str, top_k: int, max_hops: int) -> tuple:
    graph_query = GraphQuery(neo4j_client)

    # Step 1: hybrid search → seed entities
    search_results = graph_query.hybrid_search(question, top_k)

    if not search_results:
        return "", [], []

    # Step 2: get multi-hop paths from seeds
    seed_labels = []
    seed_names = []
    for item in search_results:
        labels = item.get("labels", [])
        node = item.get("node", {})
        name = node.get("name", "")
        if labels and name:
            seed_labels.append(labels[0])
            seed_names.append(name)

    paths = graph_query.multi_hop_paths(seed_labels, seed_names, max_hops=max_hops)

    # Step 3: build context from paths
    context_parts: List[str] = []
    for i, path in enumerate(paths, 1):
        nodes = path.get("nodes", [])
        edges = path.get("edges", [])
        if not nodes:
            continue

        chain_parts = []
        for j, node in enumerate(nodes):
            label = "/".join(node.get("labels", ["?"]))
            props = node.get("properties", {})
            name = props.get("name", "?")
            key_props = []
            for k, v in props.items():
                if k not in ("name", "file") and v is not None and v != "":
                    key_props.append(f"{k}={v}")
            prop_str = ", ".join(key_props[:3])
            node_desc = f"[{label}] {name}"
            if prop_str:
                node_desc += f"({prop_str})"
            chain_parts.append(node_desc)

            if j < len(edges):
                rel_type = edges[j].get("type", "?")
                rel_props = edges[j].get("properties", {})
                rel_extra = ""
                for k, v in rel_props.items():
                    if v is not None and v != "":
                        rel_extra += f" {k}={v}"
                chain_parts.append(f" --[{rel_type}{rel_extra}]--> ")

        context_parts.append(f"路径{i}: " + " ".join(chain_parts))

    context_used = "\n".join(context_parts)

    return context_used, search_results, paths


@router.post("/ask", response_model=AskResponse)
async def ask_question(request: AskRequest) -> AskResponse:
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")

    context_used, relevant_entities, reasoning_paths = _build_context(
        request.question, request.top_k, request.max_hops
    )

    if not context_used:
        return AskResponse(
            status="success",
            question=request.question,
            answer="知识图谱中未找到相关信息。请尝试用更具体的术语提问，或先录入相关数据。",
        )

    try:
        from openai import AsyncOpenAI
        from extractors.llm_utils import llm_chat

        config = get_config()
        client = AsyncOpenAI(base_url=config.llm.base_url, api_key=config.llm.api_key)

        content, _, _ = await llm_chat(
            client=client,
            model=config.llm.model,
            messages=[
                {"role": "system", "content": QA_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"知识图谱推理路径：\n{context_used}\n\n用户问题：{request.question}",
                },
            ],
            temperature=0.3,
            max_tokens=1024,
        )
        answer = content or ""
    except Exception as e:
        logger.error(f"LLM QA failed: {e}")
        answer = f"无法生成回答（LLM服务不可用）。相关知识路径：\n{context_used}"

    return AskResponse(
        status="success",
        question=request.question,
        answer=answer,
        relevant_entities=relevant_entities,
        reasoning_paths=[_simplify_path(p) for p in reasoning_paths],
        context_used=context_used,
    )


def _simplify_path(path: Dict[str, Any]) -> Dict[str, Any]:
    """Strip verbose properties from path nodes/edges for API response."""
    simple_nodes = []
    for n in path.get("nodes", []):
        props = n.get("properties", {})
        simple_nodes.append({
            "labels": n.get("labels", []),
            "name": props.get("name", ""),
            "key_props": {k: v for k, v in props.items() if k not in ("name", "file") and v},
        })
    simple_edges = []
    for e in path.get("edges", []):
        simple_edges.append({
            "type": e.get("type", ""),
            "start": e.get("start", ""),
            "end": e.get("end", ""),
        })
    return {"nodes": simple_nodes, "edges": simple_edges}


@router.post("/ask/stream")
async def ask_question_stream(request: AskRequest):
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")

    config = get_config()
    context_used, relevant_entities, reasoning_paths = _build_context(
        request.question, request.top_k, request.max_hops
    )

    if not context_used:
        async def empty_generate():
            yield f"data: {json.dumps({'type': 'error', 'message': '未找到相关信息'}, ensure_ascii=False)}\n\n"
        return StreamingResponse(empty_generate(), media_type="text/event-stream")

    from openai import AsyncOpenAI
    client = AsyncOpenAI(base_url=config.llm.base_url, api_key=config.llm.api_key)

    async def generate():
        yield f"data: {json.dumps({'type': 'meta', 'context': context_used, 'entities': len(relevant_entities), 'paths': len(reasoning_paths)}, ensure_ascii=False)}\n\n"

        try:
            response = await client.chat.completions.create(
                model=config.llm.model,
                messages=[
                    {"role": "system", "content": QA_SYSTEM_PROMPT},
                    {"role": "user", "content": f"知识图谱推理路径：\n{context_used}\n\n用户问题：{request.question}"},
                ],
                temperature=0.3,
                max_tokens=1024,
                stream=True,
            )

            async for chunk in response:
                for choice in chunk.choices:
                    if choice.delta and choice.delta.content:
                        yield f"data: {json.dumps({'type': 'token', 'content': choice.delta.content}, ensure_ascii=False)}\n\n"

            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.error(f"LLM stream failed: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Compare two entities ────────────────────────────────────


class CompareRequest(BaseModel):
    entity_a: str = Field(..., description="第一个实体名称")
    entity_b: str = Field(..., description="第二个实体名称")
    type_a: str = Field(default="", description="实体A类型(可选)")
    type_b: str = Field(default="", description="实体B类型(可选)")


class CompareResponse(BaseModel):
    status: str
    entity_a: Dict[str, Any] = Field(default_factory=dict)
    entity_b: Dict[str, Any] = Field(default_factory=dict)
    common_relations: List[Dict[str, Any]] = Field(default_factory=list)
    comparison: str = ""


COMPARE_PROMPT = """你是一个工业机器人领域的专家。请基于提供的两个实体的属性信息，生成结构化的对比分析。

要求：
1. 逐项对比两个实体的关键属性
2. 指出各自的优势和差异
3. 如果某些属性一方有而另一方没有，明确标注
4. 用中文输出，格式为Markdown"""


@router.post("/compare", response_model=CompareResponse)
async def compare_entities(request: CompareRequest) -> CompareResponse:
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")

    graph_query = GraphQuery(neo4j_client)

    # Fetch entity A
    entity_a = _fetch_entity(graph_query, request.entity_a, request.type_a)
    if not entity_a:
        raise HTTPException(status_code=404, detail=f"Entity '{request.entity_a}' not found")

    # Fetch entity B
    entity_b = _fetch_entity(graph_query, request.entity_b, request.type_b)
    if not entity_b:
        raise HTTPException(status_code=404, detail=f"Entity '{request.entity_b}' not found")

    # Find common relations (same relation type to same target type)
    common = _find_common_relations(graph_query, entity_a, entity_b)

    # Build comparison context
    context = _build_comparison_context(entity_a, entity_b, common)

    # LLM comparison
    try:
        from openai import AsyncOpenAI
        from extractors.llm_utils import llm_chat

        config = get_config()
        client = AsyncOpenAI(base_url=config.llm.base_url, api_key=config.llm.api_key)

        comparison, _, _ = await llm_chat(
            client=client,
            model=config.llm.model,
            messages=[
                {"role": "system", "content": COMPARE_PROMPT},
                {"role": "user", "content": f"请对比以下两个实体：\n\n{context}"},
            ],
            temperature=0.3,
            max_tokens=1024,
        )
    except Exception as e:
        logger.error(f"Compare LLM failed: {e}")
        comparison = f"LLM对比服务不可用。原始数据：\n{context}"

    return CompareResponse(
        status="success",
        entity_a={"name": entity_a["name"], "labels": entity_a["labels"], "properties": entity_a["properties"]},
        entity_b={"name": entity_b["name"], "labels": entity_b["labels"], "properties": entity_b["properties"]},
        common_relations=common,
        comparison=comparison or "",
    )


def _fetch_entity(graph_query: GraphQuery, name: str, label: str = "") -> Dict[str, Any]:
    """Search for entity by name, optionally with type filter."""
    results = graph_query.fulltext_search(name, limit=5)
    for item in results:
        node = item.get("node", {})
        n_name = node.get("name", "")
        n_labels = item.get("labels", [])
        if n_name == name or name.lower() in n_name.lower():
            if not label or label in n_labels:
                return {
                    "name": n_name,
                    "labels": n_labels,
                    "properties": {k: v for k, v in node.items() if not k.startswith("_") and v is not None},
                }
    return {}


def _find_common_relations(graph_query: GraphQuery, entity_a: Dict, entity_b: Dict) -> List[Dict[str, Any]]:
    """Find relation types that both entities share (to the same type of target)."""
    try:
        label_a = entity_a["labels"][0] if entity_a["labels"] else ""
        label_b = entity_b["labels"][0] if entity_b["labels"] else ""
        na = entity_a["name"]
        nb = entity_b["name"]

        # Neighbors of A
        neighbors_a = graph_query.neighbors(label_a, na, limit=50)
        neighbors_b = graph_query.neighbors(label_b, nb, limit=50)

        # Build sets of (relation_type, target_label)
        a_rels = {}
        for n in neighbors_a:
            rel = n.get("relation_type", "")
            tgt = n.get("node", {}).get("name", "")
            if rel and tgt:
                a_rels.setdefault(rel, []).append(tgt)

        b_rels = {}
        for n in neighbors_b:
            rel = n.get("relation_type", "")
            tgt = n.get("node", {}).get("name", "")
            if rel and tgt:
                b_rels.setdefault(rel, []).append(tgt)

        common = []
        for rel_type in set(a_rels.keys()) & set(b_rels.keys()):
            common.append({
                "relation_type": rel_type,
                "entity_a_targets": a_rels[rel_type][:5],
                "entity_b_targets": b_rels[rel_type][:5],
            })
        return common
    except Exception:
        return []


def _build_comparison_context(entity_a: Dict, entity_b: Dict, common: List[Dict]) -> str:
    parts = []

    def fmt_entity(label: str, e: Dict) -> str:
        props = e.get("properties", {})
        prop_lines = "\n".join(f"  {k}: {v}" for k, v in props.items() if k != "name")
        return f"{label}: {e['name']} ({'/'.join(e.get('labels', []))})\n{prop_lines}"

    parts.append(fmt_entity("实体A", entity_a))
    parts.append("")
    parts.append(fmt_entity("实体B", entity_b))

    if common:
        parts.append("\n共同关系类型：")
        for c in common:
            parts.append(f"  {c['relation_type']}: A→{c['entity_a_targets']}, B→{c['entity_b_targets']}")

    return "\n".join(parts)
