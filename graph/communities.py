"""GraphRAG community summarization via Louvain detection + LLM summaries.

Algorithm:
1. Export graph to NetworkX (undirected, unweighted)
2. Louvain community detection
3. LLM generates a structured summary per community
4. Summaries stored and injected into Q&A context
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import networkx as nx
from loguru import logger

from graph.client import Neo4jClient

_COMMUNITY_SUMMARY_TMPL = """你是一个工业机器人领域专家。请基于以下知识图谱社区中的信息，生成一段结构化的简要说明。

这个社区包含以下实体和关系：

__NODES__
__EDGES__

请生成一个100字以内的简明摘要，突出这个社区的主题（如"包含FANUC机器人及其零部件的子图"）。
只输出摘要本身，不要添加其他格式。"""


class CommunityManager:
    """Detect communities in KG and generate LLM summaries."""

    def __init__(self, client: Neo4jClient):
        self._client = client
        self._communities: List[Dict[str, Any]] = []
        self._summaries: Dict[int, str] = {}

    def detect(self) -> List[Dict[str, Any]]:
        """Run Louvain community detection on the KG. Returns list of communities."""
        # Use DiGraph to preserve relationship direction for display
        DG = nx.DiGraph()

        # Load all nodes
        nodes = self._client.execute_query(
            "MATCH (n) WHERE n.name IS NOT NULL "
            "RETURN elementId(n) AS id, labels(n) AS labels, n.name AS name"
        )
        for n in nodes:
            label = n["labels"][0] if n["labels"] else "Unknown"
            DG.add_node(n["id"], label=label, name=n["name"])

        # Load all edges with direction
        edges = self._client.execute_query(
            "MATCH (s)-[r]->(t) "
            "RETURN elementId(s) AS sid, elementId(t) AS tid, type(r) AS rel_type"
        )
        for e in edges:
            DG.add_edge(e["sid"], e["tid"], rel_type=e["rel_type"])

        logger.info(f"Community detection: {DG.number_of_nodes()} nodes, {DG.number_of_edges()} edges")

        if DG.number_of_edges() == 0:
            return []

        # Louvain requires undirected graph
        UG = DG.to_undirected()

        try:
            from networkx.algorithms.community import louvain_communities
            raw_communities = louvain_communities(UG, seed=42)
        except ImportError:
            raw_communities = list(nx.connected_components(UG))

        communities = []
        for i, node_set in enumerate(raw_communities):
            # Skip singletons — they provide no structural context
            if len(node_set) <= 1:
                continue

            nodes_data = []
            for nid in node_set:
                nodes_data.append({
                    "id": nid,
                    "name": DG.nodes[nid].get("name", ""),
                    "label": DG.nodes[nid].get("label", ""),
                })

            # Use DiGraph edges to preserve original direction (source→target)
            internal_edges = []
            for s, t in DG.edges():
                if s in node_set and t in node_set:
                    internal_edges.append({
                        "source": DG.nodes[s].get("name", ""),
                        "target": DG.nodes[t].get("name", ""),
                        "relation": DG.edges[s, t].get("rel_type", ""),
                    })

            communities.append({
                "id": i + 1,
                "size": len(node_set),
                "nodes": nodes_data,
                "internal_edges": internal_edges,
            })

        self._communities = communities
        logger.info(f"Detected {len(communities)} communities")
        return communities

    async def summarize(self, client, model: str) -> Dict[int, str]:
        """Generate LLM summaries for all detected communities."""
        if not self._communities:
            self.detect()

        from extractors.llm_utils import llm_chat

        for comm in self._communities:
            cid = comm["id"]
            if cid in self._summaries and self._summaries[cid]:
                continue

            # Format nodes and edges for prompt
            nodes_str = "\n".join(
                f"- [{n['label']}] {n['name']}" for n in comm["nodes"]
            )
            edges_str = "\n".join(
                f"- {e['source']} --[{e['relation']}]--> {e['target']}"
                for e in comm["internal_edges"]
            )

            msg = (_COMMUNITY_SUMMARY_TMPL
                   .replace("__NODES__", nodes_str[:2000])
                   .replace("__EDGES__", edges_str[:1000]))

            try:
                content, _, _ = await llm_chat(
                    client=client, model=model,
                    messages=[{"role": "user", "content": msg}],
                    temperature=0.3, max_tokens=256,
                )
                self._summaries[cid] = (content or "").strip()
                logger.debug(f"Community {cid} summary: {self._summaries[cid][:80]}...")
            except Exception as e:
                logger.warning(f"Community {cid} summary failed: {e}")
                self._summaries[cid] = f"社区{cid}: {comm['size']}个实体，{len(comm['internal_edges'])}条关系"

        return self._summaries

    def get_context(self) -> str:
        """Build a context string from community summaries for Q&A."""
        if not self._summaries:
            return ""
        parts = []
        for cid, summary in sorted(self._summaries.items()):
            parts.append(f"[Community {cid}] {summary}")
        return "\n".join(parts)

    def get_communities(self) -> List[Dict[str, Any]]:
        return self._communities
