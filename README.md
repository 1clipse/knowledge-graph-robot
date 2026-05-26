# Industrial Robot Knowledge Graph

工业机器人知识图谱系统 — 基于 Neo4j + LLM 的制造知识管理与智能问答平台。

> Created by **Heart_ziyi**

## 架构

```
                            ╔══════════════════════════════════════╗
                            ║         FRONTEND  (Static UI)        ║
                            ║  ┌────────┐ ┌────────┐ ┌─────────┐  ║
                            ║  │ 图谱视图│ │ 问答面板│ │ 质量面板 │  ║
                            ║  │ D3.js  │ │ RAG    │ │ 统计/日志│  ║
                            ║  └───┬────┘ └───┬────┘ └────┬────┘  ║
                            ╚══════╪═══════════╪═══════════╪═══════╝
                                   │           │           │
                              ┌────┴───────────┴───────────┴────┐
                              │        FastAPI  :8100            │
                              │                                  │
     Data Sources             │  ┌──────────────────────────┐   │
     ┌──────────┐             │  │      INGEST 路由          │   │
     │ PDF/CAD  │──┐          │  │  /text  /file  /batch     │   │
     │ DWG/DXF  │  │          │  │  /url   /files  /logs     │   │
     │ STEP/IGES│  │  ┌───────┤  ├──────────────────────────┤   │
     │ CSV      │──┼──┤ 提取器 │  │      QUERY 路由          │   │
     │ Text     │  │  │       │  │  /search  /stats  /path   │   │
     └──────────┘  │  │ ┌────────────┐ ┌──────────────┐     │   │
                   │  │ │ Rule引擎   │ │ LLM提取器    │     │   │
                   └──┤ │ (Regex+)   │ │ (Qwen2.5-7B) │     │   │
                      │ └────────────┘ └──────────────┘     │   │
                      └──────────────────────────────────────┘   │
                              │                                  │
                              ▼                                  │
                      ┌───────────────┐                          │
                      │ 混合检索层     │                          │
                      │ ┌───────────┐ │    ┌───────────────┐    │
                      │ │向量检索    │ │    │ 实体链接       │    │
                      │ │BGE-M3 ONNX│ │    │ Span+Fuzzy    │    │
                      │ │1024d ANN  │ │    └───────────────┘    │
                      │ ├───────────┤ │                          │
                      │ │关键词检索  │ │                          │
                      │ │Lucene FT  │ │                          │
                      │ └───────────┘ │                          │
                      └───────┬───────┘                          │
                              │                                  │
              ┌───────────────┼───────────────┐                  │
              │               ▼               │                  │
              │    ┌──────────────────┐       │                  │
              │    │    Neo4j 图谱     │       │                  │
              │    │  141 节点 175 边  │       │                  │
              │    └────────┬─────────┘       │                  │
              │             │                 │                  │
              │    ┌────────┴─────────┐       │                  │
              │    │   推理 & 评估     │       │                  │
              │    │ ┌──────────────┐ │       │                  │
              │    │ │ GraphRAG     │ │       │    ┌───────────┐ │
              │    │ │ Louvain 社区  │ │       │    │  LLM 服务  │ │
              │    │ ├──────────────┤ │       │    │ Qwen2.5-7B │ │
              │    │ │ OWL 语义推理 │ │       │    │ :5200/v1   │ │
              │    │ ├──────────────┤ │       │    │ (P100 GPU) │ │
              │    │ │ RAGAS 评估   │ │       │    └───────────┘ │
              │    │ └──────────────┘ │       │                  │
              │    └─────────────────┘       │                  │
              └──────────────────────────────┘                  │
                              │                                  │
                              ▼                                  │
                     ┌────────────────┐                          │
                     │  Q&A 问答引擎   │                          │
                     │ ┌────────────┐ │                          │
                     │ │ 引用溯源    │ │                          │
                     │ │ [P1][P2]   │ │                          │
                     │ │ Citations  │ │                          │
                     │ └────────────┘ │                          │
                     └────────────────┘                          │
                              │                                  │
                              └──────────────────────────────────┘
```

## 功能

### 数据摄入
- **多格式支持** — PDF、CSV、DWG/DXF (CAD)、STEP/IGES、TXT、URL 网页抓取
- **CAD 智能解析** — ODA FileConverter DWG→DXF 自动转换 + SolidWorks 符号保留
- **批量上传** — 多文件同时上传 + 去重确认
- **AI 提取** — 可选 LLM 深度提取（更准确但更慢）+ 规则引擎兜底

### 知识检索
- **混合检索** — 向量语义 (BGE-M3 1024d ONNX) + 关键词全文 (Lucene)，双路合并去重
- **实体链接** — Span 提取 + Fuzzy 模糊评分，查询→实体精确映射
- **多跳推理** — 1~3 跳关系链探索

### 智能问答
- **RAG 问答** — 基于图谱路径的上下文增强生成
- **引用溯源** — 回答强制标注数据来源 [P1][P2]...，可追溯每条事实的图谱路径
- **社区上下文** — GraphRAG Louvain 社区检测 + LLM 摘要，注入全局语义背景

### 推理 & 质量
- **OWL 语义推理** — 对称关系 (competitor_of)、传递关系 (part_of)、子类继承推理
- **RAGAS 评估** — Faithfulness / Relevancy / ContextPrecision 三维自动评分
- **质量面板** — 孤立节点检测、属性缺失扫描、重复实体发现、置信度分析
- **实体对比** — 两个实体并排对比，生成差异报告

## 快速启动

### 本地开发

```bash
pip install -r requirements.txt

# 前置条件: 运行本地 Neo4j (bolt://localhost:7687)
# 前置条件: LLM 服务可达 (默认 http://10.117.29.24:5200/v1)
# 配置 config/.env（参考下方配置说明）

python -m uvicorn api.app:app --host 0.0.0.0 --port 8100
```

访问：
- 管理界面: http://localhost:8100
- API 文档: http://localhost:8100/docs
- Neo4j 浏览器: http://localhost:7474

## API 端点

| 端点 | 说明 |
|------|------|
| `POST /api/v1/ingest/text` | 文本摄入 |
| `POST /api/v1/ingest/file` | 单文件摄入 |
| `POST /api/v1/ingest/batch` | 批量文件摄入 |
| `POST /api/v1/ingest/url` | URL 网页摄入 |
| `GET /api/v1/ingest/files` | 已上传文件列表 |
| `GET /api/v1/ingest/logs` | 摄入日志 |
| `DELETE /api/v1/ingest/file/{name}` | 按文件删除数据 |
| `GET /api/v1/query/search?q=&limit=` | 实体搜索 |
| `GET /api/v1/query/stats` | 图谱统计 |
| `GET /api/v1/query/node/{label}/{name}` | 节点详情 |
| `GET /api/v1/query/shortest-path` | 最短路径查询 |
| `POST /api/v1/ask` | 知识问答 (RAG) |
| `GET /api/v1/communities` | 社区检测结果 |
| `GET /api/v1/subgraph/search/{keyword}` | 子图搜索 (可视化) |
| `GET /api/v1/subgraph/{label}/{name}` | 单节点子图 |
| `GET /api/v1/quality` | 数据质量报告 |
| `GET /api/v1/reasoner/rules` | 推理规则列表 |
| `POST /api/v1/reasoner/run` | 执行语义推理 |
| `POST /api/v1/eval/run` | 运行 RAGAS 评估 |
| `GET /api/v1/eval/dataset` | 评估数据集 |

## 配置

复制 `config/.env.example` → `config/.env`，主要配置项：

```ini
# Neo4j 本地连接
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=z12345678
NEO4J_DATABASE=neo4j

# LLM 服务 (P100 GPU)
LLM_BASE_URL=http://10.117.29.24:5200/v1
LLM_API_KEY=local
LLM_MODEL=qwen2.5-7b
LLM_TEMPERATURE=0.1
LLM_MAX_TOKENS=4096

# BGE-M3 嵌入模型
EMBEDDING_MODEL_PATH=E:/huggingface_cache/BAAI/bge-m3

# DWG 转换器 (可选)
ODA_CONVERTER_PATH=E:/ODA/ODAFileConverter.exe
```

## 技术栈

- **图数据库**: Neo4j Community 2025.04 (bolt://localhost:7687)
- **后端**: FastAPI 0.115 + Uvicorn (port 8100)
- **LLM**: Qwen2.5-7B @ P100 GPU（OpenAI 兼容 API）
- **嵌入模型**: BGE-M3 1024d (ONNX Runtime / CPU)
- **图算法**: NetworkX Louvain 社区检测
- **向量检索**: Neo4j 原生向量索引 + 余弦相似度回退
- **前端**: Vanilla JS + D3.js v7（力导向图谱）
- **CAD 解析**: ezdxf + ODA FileConverter
