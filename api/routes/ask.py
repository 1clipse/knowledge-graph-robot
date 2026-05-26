from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import neo4j_client
from config.settings import get_config
from graph.entity_linker import EntityLinker
from graph.query import GraphQuery

router = APIRouter()

_entity_linker: Optional[EntityLinker] = None


def _get_linker() -> EntityLinker:
    global _entity_linker
    if _entity_linker is None and neo4j_client is not None:
        _entity_linker = EntityLinker(neo4j_client)
    return _entity_linker


class AskRequest(BaseModel):
    question: str = Field(..., description="用户问题")
    top_k: int = Field(default=5, description="检索相关实体数量")
    max_hops: int = Field(default=3, description="多跳推理跳数")


class Citation(BaseModel):
    marker: str = ""           # e.g. "P1"
    path_index: int = 0        # index into reasoning_paths
    snippet: str = ""          # the answer text around the citation


class AskResponse(BaseModel):
    status: str
    question: str
    answer: str = ""
    relevant_entities: List[Dict[str, Any]] = Field(default_factory=list)
    reasoning_paths: List[Dict[str, Any]] = Field(default_factory=list)
    citations: List[Citation] = Field(default_factory=list)
    context_used: str = ""


QA_SYSTEM_PROMPT = """你是一个工业机器人领域的知识问答助手。基于提供的知识图谱上下文信息，回答用户的问题。

上下文包含从知识图谱中检索到的"推理路径"（标记为P1、P2...），每条路径展示了实体之间的多跳关系链。

规则：
1. 优先使用提供的上下文信息回答，利用多跳关系链进行推理
2. 如果上下文信息不足以回答问题，明确说明
3. 回答要准确、专业、有条理，用中文
4. 涉及具体参数时请给出数值
5. 引用来源：只在陈述具体事实时标注，每条信息末尾标一次即可。例如"FANUC M-20iA的负载为20kg[P1]。"
6. 重要：每条路径最多引用1-2次，不要重复堆叠多个引用标记，不要在句子末尾堆砌[P1][P2]...[P10]
7. 如果无法从上下文中找到相关信息，不要编造引用"""

_CITATION_RE = None  # compiled lazily


def _get_citation_re():
    global _CITATION_RE
    if _CITATION_RE is None:
        _CITATION_RE = __import__("re").compile(r"\[P(\d+)\]")
    return _CITATION_RE


def _parse_citations(answer: str, reasoning_paths: List[Dict[str, Any]]) -> List[Citation]:
    """Extract [P1], [P2] markers from answer and map to reasoning_paths."""
    pat = _get_citation_re()
    matches = pat.finditer(answer)
    seen: set = set()
    citations: List[Citation] = []
    for m in matches:
        idx = int(m.group(1)) - 1  # convert to 0-based
        if idx not in seen and 0 <= idx < len(reasoning_paths):
            seen.add(idx)
            start = max(0, m.start() - 40)
            end = min(len(answer), m.end() + 40)
            snippet = answer[start:end].strip()
            citations.append(Citation(
                marker=f"P{idx + 1}",
                path_index=idx,
                snippet=snippet,
            ))
    return citations


_CTX_MAX_PATHS = 10       # Max paths to include
_CTX_MAX_TOKENS_EST = 2500  # Estimated max tokens for context (leaves room for prompt + answer)


def _estimate_tokens(text: str) -> int:
    """Rough token estimation: Chinese chars ≈ 1 token, English words ≈ 1.3 tokens."""
    import re
    chinese = len(re.findall(r'[一-鿿]', text))
    english_tokens = len(re.findall(r'[a-zA-Z]+', text)) * 1.3
    other = len(text) // 6  # rough estimate for other chars
    return int(chinese + english_tokens + other)


def _build_context(question: str, top_k: int, max_hops: int) -> tuple:
    graph_query = GraphQuery(neo4j_client)

    # Step 0: Entity linking — find precise entity mentions in query
    linked_entities: List[Dict[str, Any]] = []
    try:
        linker = _get_linker()
        if linker:
            linked = linker.extract_spans_and_link(question, min_score=70.0)
            linked_entities = [e for e, _, _ in linked]
            logger.debug(f"EntityLinker found {len(linked_entities)} entities")
    except Exception as e:
        logger.debug(f"Entity linker skipped: {e}")

    # Step 1: Hybrid search → seed entities
    search_results = graph_query.hybrid_search(question, top_k)

    # Merge linked entities into search results (prioritized at front)
    if linked_entities:
        linked_names = {e["name"] for e in linked_entities}
        existing_names = {r.get("node", {}).get("name", "") for r in search_results}
        for ent in linked_entities:
            if ent["name"] not in existing_names:
                search_results.insert(0, {
                    "labels": [l for l in ent.get("labels", []) if l != "Entity"],
                    "node": {"name": ent["name"]},
                    "score": 1.0,
                    "source": "entity_linker",
                })

    if not search_results:
        return "", [], []

    # Limit seeds
    search_results = search_results[:top_k]

    # Step 2: Get multi-hop paths from seeds
    seed_labels = []
    seed_names = []
    for item in search_results:
        labels = item.get("labels", [])
        node = item.get("node", {})
        name = node.get("name", "")
        if labels and name:
            seed_labels.append(labels[0])
            seed_names.append(name)

    paths = graph_query.multi_hop_paths(seed_labels, seed_names, max_hops=max_hops, max_paths=20)

    # Step 3: Build context — keep it concise, cap token count
    context_parts: List[str] = []
    total_est = 0

    for i, path in enumerate(paths, 1):
        if i > _CTX_MAX_PATHS:
            break
        if total_est > _CTX_MAX_TOKENS_EST:
            break

        nodes = path.get("nodes", [])
        edges = path.get("edges", [])
        if not nodes:
            continue

        chain_parts = []
        for j, node in enumerate(nodes):
            label = "/".join(node.get("labels", ["?"]))
            props = node.get("properties", {})
            name = props.get("name", "?")

            # Only show the most relevant properties
            key_props = []
            priority_keys = ("payload", "reach", "repeatability", "axes",
                           "rated_torque", "rated_power", "reducer_type",
                           "application_type", "process_type", "country")
            for pk in priority_keys:
                v = props.get(pk)
                if v is not None and v != "":
                    key_props.append(f"{pk}={v}")
                    if len(key_props) >= 2:
                        break

            node_desc = f"[{label}] {name}"
            if key_props:
                node_desc += f"({', '.join(key_props)})"
            chain_parts.append(node_desc)

            if j < len(edges):
                rel_type = edges[j].get("type", "?")
                chain_parts.append(f" --[{rel_type}]--> ")

        path_line = " ".join(chain_parts)
        path_tokens = _estimate_tokens(path_line)
        if total_est + path_tokens > _CTX_MAX_TOKENS_EST:
            break
        context_parts.append(f"P{i}: {path_line}")
        total_est += path_tokens

    # Step 3.5: Add community summaries (GraphRAG-style global context)
    try:
        from graph.communities import CommunityManager
        from api.deps import neo4j_client as db_client
        cm = CommunityManager(db_client)
        communities = cm.detect()
        if communities and _CTX_MAX_TOKENS_EST - total_est > 200:
            # Add summaries for communities containing retrieved entities
            entity_names = {n.get("node", {}).get("name", "") for n in search_results}
            relevant_community_ids = set()
            for c in communities:
                for nd in c["nodes"]:
                    if nd["name"] in entity_names:
                        relevant_community_ids.add(c["id"])
                        break

            if relevant_community_ids:
                comm_lines = []
                for c in communities:
                    if c["id"] in relevant_community_ids:
                        nodes_str = "、".join(n["name"] for n in c["nodes"][:5])
                        rels_str = "、".join(f"{e['source']} {e['relation']} {e['target']}" for e in c["internal_edges"][:5])
                        comm_lines.append(f"社区{c['id']}({c['size']}个实体): {nodes_str}; 关系: {rels_str}")
                comm_text = "\n".join(comm_lines)
                comm_tokens = _estimate_tokens(comm_text)
                if total_est + comm_tokens <= _CTX_MAX_TOKENS_EST + 500:  # allow small overflow for communities
                    context_parts.append("\n--- 社区背景 ---\n" + comm_text)
                    total_est += comm_tokens
                    logger.debug(f"Added community context: {len(comm_lines)} communities")
    except Exception as e:
        logger.debug(f"Community context skipped: {e}")

    context_used = "\n".join(context_parts)
    logger.info(f"Context: {len(context_parts)} paths, ~{total_est} tokens")

    return context_used, search_results, paths[:len(context_parts)]


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

    simplified_paths = [_simplify_path(p) for p in reasoning_paths]
    citations = _parse_citations(answer, simplified_paths)

    return AskResponse(
        status="success",
        question=request.question,
        answer=answer,
        relevant_entities=relevant_entities,
        reasoning_paths=simplified_paths,
        citations=citations,
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


@router.get("/communities")
async def get_communities():
    """Return graph communities detected by Louvain algorithm."""
    if neo4j_client is None:
        raise HTTPException(status_code=503, detail="Database not connected")

    from graph.communities import CommunityManager
    cm = CommunityManager(neo4j_client)
    communities = cm.detect()

    try:
        from openai import AsyncOpenAI
        config = get_config()
        client = AsyncOpenAI(base_url=config.llm.base_url, api_key=config.llm.api_key)
        summaries = await cm.summarize(client, config.llm.model)
        for c in communities:
            c["summary"] = summaries.get(c["id"], "")
    except Exception as e:
        logger.warning(f"Community summaries failed: {e}")
        for c in communities:
            c["summary"] = ""

    return {
        "status": "success",
        "count": len(communities),
        "communities": communities,
    }


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
