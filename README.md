# Industrial Robot Knowledge Graph

工业机器人知识图谱系统 — 基于 Neo4j + LLM 的制造知识管理与智能问答平台。

> Created by **Heart_ziyi**

## 架构

```
┌──────────┐    ┌──────────┐    ┌──────────┐
│  Nginx   │───▶│  FastAPI │───▶│  Neo4j   │
│  (UI)    │    │  (API)   │    │  (Graph) │
└──────────┘    └────┬─────┘    └──────────┘
                     │
              ┌──────┴──────┐
              │  LLM Service │
              │ (Qwen2.5-7B) │
              └─────────────┘
```

## 功能

- **多格式数据摄入** — PDF、CSV、DXF、STEP、数据库、网页抓取
- **实体关系抽取** — 规则引擎 + LLM 混合抽取，支持结构化映射
- **知识图谱查询** — 多跳关系查询、语义向量检索、子图可视化
- **智能问答** — 基于图谱的 RAG 问答，支持溯源定位
- **数据质量** — 实体去重、关系一致性校验、质量面板

## 快速启动

### Docker（推荐）

```bash
# 1. 修改 docker-compose.yml 中 Neo4j 密码
# 2. 配置 LLM 地址
export LLM_BASE_URL=http://your-llm-host:5200/v1

# 3. 启动
docker-compose up -d
```

访问：
- 管理界面: http://localhost:3000
- 对话界面: http://localhost:3000/chat
- API 文档: http://localhost:8000/docs

### 本地开发

```bash
pip install -r requirements.txt

# 启动 Neo4j（Docker 或本地）
# 复制 config/.env.example 为 config/.env 并填写配置

python -m uvicorn api.app:app --reload --port 8000
```

## API 端点

| 端点 | 说明 |
|------|------|
| `POST /api/v1/ingest` | 数据摄入（文件/文本/URL） |
| `POST /api/v1/query` | 图谱查询（Cypher + 语义） |
| `POST /api/v1/ask` | 知识问答（RAG） |
| `GET /api/v1/subgraph` | 子图数据（可视化） |
| `GET /api/v1/quality` | 数据质量报告 |

## 配置

复制 `config/.env.example` → `config/.env`，主要配置项：

```ini
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=changeme
LLM_BASE_URL=http://localhost:5200/v1
LLM_MODEL=qwen2.5-7b
KG_API_KEY=          # 可选，设置后 API 需 Bearer Token
```

## 技术栈

- **图数据库**: Neo4j 5.15 + APOC
- **后端**: FastAPI / Uvicorn
- **LLM**: Qwen2.5-7B（OpenAI 兼容 API）
- **向量**: Sentence Transformers
- **前端**: Nginx + Vanilla JS
- **部署**: Docker Compose
