# Industrial Robot Knowledge Graph

工业机器人知识图谱系统 — 基于 Neo4j + LLM + spaCy 的制造知识管理与智能问答平台。4 级提取漏斗（规则 → spaCy NER → LLM）+ GraphRAG 检索 + 质量评估 + 语义推理。

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
     │ PDF/DOCX │──┐          │  │  /text  /file  /batch     │   │
     │ DWG/DXF  │  │          │  │  /url   /files  /logs     │   │
     │ STEP/IGES│  │  ┌───────┤  ├──────────────────────────┤   │
     │ TXT/CSV  │──┼──┤ 提取器 │  │      QUERY 路由          │   │
     └──────────┘  │  │       │  │  /search  /stats  /path   │   │
                   │  │ ┌────────────┬───────────┬──────────┐│   │
                   │  │ │ Rule引擎   │spaCy NER  │LLM提取器 ││   │
                   └──┤ │ (Tier 2)   │(Tier 3)   │(Tier 4)  ││   │
                      │ └────────────┴───────────┴──────────┘│   │
                      │          4-tier extraction funnel     │   │
                      └──────────────────────────────────────┘   │
                              │                                  │
                              ▼                                  │
                      ┌───────────────┐                          │
                      │ 混合检索层     │                          │
                      │ ┌───────────┐ │    ┌───────────────┐    │
                      │ │向量检索    │ │    │ 实体消歧+链接  │    │
                      │ │BGE-M3 ONNX│ │    │ Resolver+Span │    │
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
              │    │ ┌──────────────┐ │       │    ┌───────────┐ │
              │    │ │ GraphRAG     │ │       │    │  LLM 服务  │ │
              │    │ │ Louvain 社区  │ │       │    │ Qwen2.5-7B │ │
              │    │ ├──────────────┤ │       │    │ :5200/v1   │ │
              │    │ │ OWL 语义推理 │ │       │    │ (P100 GPU) │ │
              │    │ │ 对称+传递+逆 │ │       │    └───────────┘ │
              │    │ ├──────────────┤ │       │                  │
              │    │ │ 引用验证     │ │       │                  │
              │    │ │ Citation    │ │       │                  │
              │    │ └──────────────┘ │       │                  │
              │    └─────────────────┘       │                  │
              └──────────────────────────────┘                  │
                              │                                  │
                              ▼                                  │
                     ┌────────────────┐                          │
                     │  Q&A 问答引擎   │                          │
                     │ ┌────────────┐ │                          │
                     │ │ 引用溯源    │ │   ┌──────────────┐      │
                     │ │ [P1][P2]   │ │   │ 导出服务      │      │
                     │ │ Citations  │ │   │ PDF + DOCX   │      │
                     │ └────────────┘ │   └──────────────┘      │
                     └────────────────┘                          │
                              │                                  │
                              └──────────────────────────────────┘
```

## v2.3 更新亮点

### 一键启动与本地可用性修复
- 新增 `start_all.ps1` / `start_all.bat`，自动解析项目根目录、项目虚拟环境 Python 和 Neo4j 安装目录，依次启动 Neo4j、FastAPI 后端和前端静态页面。
- 修复回滚领域 schema 后 `active_domain_key` 缺失导致 FastAPI 无法导入的问题，默认领域恢复为 `industrial_robot`。
- 修复 embedding 启动链路：补充 `onnxruntime` 依赖，优先使用 BGE-M3 ONNX 后端；当 ONNX 不可用时不会默认加载超大 `sentence-transformers` 模型，避免后端启动崩溃。

### 工业 CAD / DWG 摄入增强
- DWG 文件经 ODA FileConverter 转换为 DXF 后进入结构化解析流程，保留图纸版本、图元数量、图层、直线/圆/圆弧等 CAD 元数据。
- 摄入完成后清理临时 DXF 文件，避免批量导入产生垃圾文件。
- CAD、CSV 等结构化输入进入高置信度 Tier 1 映射，再统一交给 `GraphWriter` 写入，保证来源、置信度、时间字段和领域标记一致。

### GraphWriter 与领域隔离
- 节点和关系写入统一带 `_domain=industrial_robot`，查询、质量检查、时间线和删除路径按领域过滤，减少跨数据集污染。
- 默认启用 `EntityResolver`，写入前先对制造商等实体别名归一化，例如“发那科”统一为 `FANUC`。
- 写入失败时 embedding 自动降级为无向量写入，不阻塞普通图谱构建。

### 问答、评估与降级体验
- RAG 问答在本地 LLM 不可用时返回明确的降级信息，不再让前端误以为系统无响应。
- 对比分析在 LLM 服务不可用时展示原始属性和共同关系，仍可完成实体比对。
- 新增评估上下文与退化启动测试，覆盖数据库/LLM/embedding 部分不可用时的启动行为。

### UI 与安全优化
- 前端图谱交互、批量导入、对比导出和状态展示继续优化，避免大字段 `_embedding` 进入 API 响应。
- Cypher 只读校验、安全中间件、审计日志和 CORS 配置进一步完善。

## v2.2 更新亮点

### 4 级提取漏斗
文本进入后依次经过规则正则 (Tier 2, confidence 0.90-0.95) → spaCy NER (Tier 3, 0.75-0.90) → 合并去重 → LLM (Tier 4) 仅补低置信度/稀疏部分。结构化数据（CSV/CAD/DXF）直接走 Tier 1 高置信度映射。提取结果由集中的 `GraphWriter` 负责 batch embedding + schema 校验 + 批量写入 Neo4j。

### GraphRAG 检索 + 引用验证
`GraphRagRetriever` 统一检索入口，整合向量检索、关键词检索、实体消歧和多跳路径探索。`CitationVerifier` 对 LLM 回答做事实核查，强制每条结论有图谱路径支撑。

### 推理引擎增强
新增逆关系推理（如 HAS_COMPONENT → COMPONENT_OF）。推理规则从 `schema/industrial_robot.yaml` 动态加载，不再硬编码。

### PDF / DOCX 导出
对比报告可导出为 PDF（fpdf2 + 微软雅黑）和 DOCX（python-docx），包含属性对比表、共同关系表和 AI 分析。

### 安全防护
Cypher 查询注入拦截，阻止所有写操作关键字。

### 新增模块
质量检查器 `quality/checker.py`、实体消歧 `graph/entity_resolver.py` + `config/entity_aliases.yaml`、CAD 适配器 `loaders/cad_adapter.py`、DOCX/TXT 加载器。

## 功能

### 数据摄入
- **多格式支持** — PDF、DOCX、CSV、DWG/DXF (CAD)、STEP/IGES、TXT、URL 网页抓取
- **CAD 智能解析** — ODA FileConverter DWG→DXF 自动转换 + SolidWorks 符号保留
- **批量上传** — 多文件同时上传 + 去重确认
- **4 级提取** — Rule → spaCy NER → merge → LLM 兜底

### 知识检索
- **混合检索** — 向量语义 (BGE-M3 1024d ONNX) + 关键词全文 (Lucene)，双路合并去重
- **实体消歧** — EntityResolver 别名映射 + EntityLinker Span/Fuzzy 评分
- **多跳推理** — 1~3 跳关系链探索

### 智能问答
- **RAG 问答** — 基于图谱路径的上下文增强生成
- **引用溯源** — 回答强制标注数据来源 [P1][P2]...，可追溯每条事实的图谱路径
- **社区上下文** — GraphRAG Louvain 社区检测 + LLM 摘要，注入全局语义背景
- **引用验证** — CitationVerifier 事实验证

### 推理 & 质量
- **OWL 语义推理** — 对称关系、传递关系、逆关系、子类继承推理
- **质量面板** — 孤立节点检测、属性缺失扫描、重复实体发现、置信度分析
- **实体对比** — 两个实体并排对比 + PDF/DOCX 导出

## 快速启动

### Windows 一键启动（推荐）

```powershell
E:
cd "E:\Knowledge Graph_robot"
.\start_all.ps1
```

或双击 / 运行：

```bat
start_all.bat
```

脚本会自动：
1. 解析项目根目录和 `.venv\Scripts\python.exe`
2. 启动或复用 Neo4j Bolt `7687`
3. 启动 FastAPI + 前端静态页面 `8100`
4. 执行 `/health` 健康检查并打开浏览器

常用参数：

```powershell
.\start_all.ps1 -NoReload -OpenDocs -OpenNeo4j -NoBrowser -BackendPort 8100
```

### 本地开发

```bash
pip install -r requirements.txt

# 前置条件: 运行本地 Neo4j (bolt://localhost:7687)
# 前置条件: LLM 服务可达 (默认 http://localhost:5200/v1)
# 前置条件: spaCy 中文模型 (zh_core_web_lg)
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
| `POST /api/v1/ingest/text` | 文本摄入（4 级漏斗） |
| `POST /api/v1/ingest/file` | 单文件摄入（支持 PDF/DOCX/DXF/TXT/CSV/STEP） |
| `POST /api/v1/ingest/batch` | 批量文件摄入 |
| `POST /api/v1/ingest/url` | URL 网页摄入 |
| `GET /api/v1/ingest/files` | 已上传文件列表 |
| `GET /api/v1/ingest/logs` | 摄入日志 |
| `DELETE /api/v1/ingest/file/{name}` | 按文件删除数据 |
| `GET /api/v1/query/search?q=&limit=` | 实体搜索 |
| `GET /api/v1/query/stats` | 图谱统计 |
| `GET /api/v1/query/node/{label}/{name}` | 节点详情 |
| `GET /api/v1/query/shortest-path` | 最短路径查询 |
| `POST /api/v1/ask` | 知识问答 (RAG + 引用) |
| `POST /api/v1/compare` | 实体对比分析 |
| `POST /api/v1/compare/export/pdf` | 对比报告导出 PDF |
| `POST /api/v1/compare/export/docx` | 对比报告导出 DOCX |
| `GET /api/v1/communities` | 社区检测结果 |
| `GET /api/v1/subgraph` | 全量图谱（limit≤5000） |
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

# LLM 服务（OpenAI 兼容 API；不启动时问答/对比会降级显示原始数据）
LLM_BASE_URL=http://localhost:5200/v1
LLM_API_KEY=local
LLM_MODEL=qwen2.5-7b
LLM_TEMPERATURE=0.1
LLM_MAX_TOKENS=4096

# BGE-M3 嵌入模型（推荐 ONNX Runtime）
EMBEDDING_MODEL_PATH=E:/huggingface_cache/BAAI/bge-m3
HF_HOME=E:/huggingface_cache
HF_HUB_OFFLINE=true

# spaCy 模型
# pip install zh-core-web-lg @ https://github.com/explosion/spacy-models/releases/download/zh_core_web_lg-3.7.0/zh_core_web_lg-3.7.0-py3-none-any.whl

# DWG 转换器 (可选)
ODA_CONVERTER_PATH=E:/ODA/ODAFileConverter.exe
```

### spaCy 配置 (`config/default.yaml`)

```yaml
spacy:
  model_path: "models/kg_robot_ner"
  base_model: "zh_core_web_lg"
  confidence_threshold: 0.7
  enabled: true
```

## 测试

```bash
pytest tests/ -v

# 快速回归（启动、embedding、写入、安全、问答降级）
pytest tests/test_app_degraded_startup.py tests/test_embeddings.py tests/test_graph_writer.py tests/test_security.py tests/test_ask_fallback.py -q
```

## 技术栈

- **图数据库**: Neo4j Community 2025.04 (bolt://localhost:7687)
- **后端**: FastAPI 0.115 + Uvicorn (port 8100)
- **LLM**: Qwen2.5-7B @ P100 GPU（OpenAI 兼容 API）
- **嵌入模型**: BGE-M3 1024d (ONNX Runtime / CPU)
- **NLP**: spaCy 3.7 + zh_core_web_lg + 自定义 NER 模型
- **图算法**: NetworkX Louvain 社区检测
- **向量检索**: Neo4j 原生向量索引 + 余弦相似度回退
- **前端**: Vanilla JS + D3.js v7（力导向图谱，关系中文标签）
- **CAD 解析**: ezdxf + ODA FileConverter
- **文档生成**: fpdf2 + python-docx
- **质量评估**: RAGAS Faithfulness / Relevancy / ContextPrecision
