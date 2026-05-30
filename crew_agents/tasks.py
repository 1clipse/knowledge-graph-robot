"""
CrewAI 开发任务模板。

所有任务都要求 Agent 先读取真实项目文件，再输出中文结构化结果。
不要让 Agent 只凭“项目印象”回答。
"""
from __future__ import annotations

from crewai import Task

_BASE_INSTRUCTIONS = """
硬性要求：
1. 必须先用 FileReadTool 读取相关项目文件。
2. 输出必须包含 files_read 清单。
3. 不允许编造文件、函数、行号、测试结果或运行结果。
4. 如果无法确认行号，说明“需继续读取确认”，不要猜。
5. 输出使用中文，文件路径使用项目相对路径。
""".strip()


def analyze_task(question: str, agent):
    return Task(
        description=f"""
{_BASE_INSTRUCTIONS}

需求：{question}

请分析：
- 涉及模块
- 关键调用链 / 数据流
- 可能改动点
- 主要风险
- 建议优先级
""".strip(),
        expected_output="""
## 分析结论

### files_read
- ...

### 涉及模块
...

### 调用链 / 数据流
...

### 改动点
...

### 风险
...

### 建议优先级
...
""".strip(),
        agent=agent,
    )


def develop_task(requirement: str, agent):
    return Task(
        description=f"""
{_BASE_INSTRUCTIONS}

开发需求：{requirement}

请先读取相关源码，输出 patch 级修改方案。不要声称已经修改文件；只输出建议改哪些文件、怎么改、关键代码片段和测试命令。
除非需求明确要求，否则不要引入新依赖。
""".strip(),
        expected_output="""
## 开发方案

### files_read
- ...

### 建议修改文件
- ...

### patch 级修改说明
1. ...

### 关键代码片段
```python
...
```

### 测试命令
...

### 风险与回滚
...
""".strip(),
        agent=agent,
    )


def review_task(target: str, agent):
    return Task(
        description=f"""
{_BASE_INSTRUCTIONS}

审查目标：{target}

请基于真实代码检查：
- 正确性 bug
- 安全风险，尤其是 Cypher 注入、认证授权、路径/文件输入
- 性能隐患，尤其是 Neo4j 查询、批量写入、LLM 调用
- 异常处理和资源释放
- 与项目风格不一致的地方

每个问题必须给严重级别、文件位置、触发条件和修复建议。
""".strip(),
        expected_output="""
## 代码审查结果

### files_read
- ...

### findings

#### 1. [严重级别] 问题标题
- 文件：...
- 行号：...
- 触发条件：...
- 问题：...
- 修复建议：...

### 总体判断
...
""".strip(),
        agent=agent,
    )


def test_task(module: str, agent):
    return Task(
        description=f"""
{_BASE_INSTRUCTIONS}

测试目标模块：{module}

请读取目标源码和 tests/ 下相近测试文件，分析测试风格和覆盖盲区。输出 pytest 测试建议和示例代码。
外部依赖如 Neo4j、LLM、文件系统、网络请求必须 mock 或 fixture 隔离。
""".strip(),
        expected_output="""
## 测试建议

### files_read
- ...

### 现有测试风格
...

### 覆盖盲区
- ...

### 建议新增测试
- ...

### pytest 示例
```python
...
```
""".strip(),
        agent=agent,
    )


def debug_task(error_info: str, agent):
    return Task(
        description=f"""
{_BASE_INSTRUCTIONS}

错误信息：{error_info}

请根据 traceback / 报错文本定位相关文件，读取出错文件和上下游调用代码。输出根因、最小修复、回归测试和类似隐患。
""".strip(),
        expected_output="""
## 调试诊断

### files_read
- ...

### 根因
...

### 调用链
...

### 最小修复方案
...

### 回归测试
...

### 类似隐患
...
""".strip(),
        agent=agent,
    )


# ============================================================
# 优化审查 —— 5 Agent 分角度分析
# ============================================================

def optimize_task(angle: str, agent):
    angle_prompts = {
        "架构": """
先读取 README.md、api/app.py、api/routes/ingest.py、pipeline/ingest.py、graph/writer.py、graph/query.py、api/routes/ask.py、api/routes/eval.py。
分析模块深度、接口一致性、数据流、重复实现、路由是否承载过多业务逻辑。
重点关注：4 级提取漏斗、GraphWriter、GraphRAG、引用验证、语义推理、质量检查器。
输出 3-5 个架构优化点，按优先级排序。
""",
        "安全": """
先读取 api/security.py、api/app.py、api/routes/query.py、api/routes/subgraph.py、api/routes/ingest.py、graph/client.py。
检查认证授权、CORS、审计日志、Cypher 注入防护、删除/写入操作、文件上传输入校验。
只报告真实代码中能定位的问题，不要泛泛而谈。
""",
        "质量": """
先读取 api/routes/ingest.py、pipeline/ingest.py、graph/writer.py、graph/entity_resolver.py、extractors/rule_extractor.py、extractors/llm_extractor.py。
检查重复逻辑、异常处理、类型注解、资源管理、命名一致性和模块职责。
输出 3-5 个代码质量改进点。
""",
        "测试": """
先读取 tests/test_graph_writer.py、tests/test_rule_extractor.py、tests/test_entity_resolver.py、tests/test_rag_retriever.py，以及对应源码。
分析测试风格、fixture/mock 使用、覆盖盲区。
输出缺失测试清单，并给 1 个最值得补的 pytest 示例。
""",
        "Bug": """
先读取 api/routes/ingest.py、api/routes/ask.py、api/routes/eval.py、extractors/llm_extractor.py、graph/query.py。
检查 None/空列表、bare except、文件/session 资源泄漏、私有 helper 调用不匹配、LLM 响应结构异常。
每个 bug 给触发条件、影响、修复代码片段。
""",
    }

    return Task(
        description=f"""
{_BASE_INSTRUCTIONS}

优化角度：{angle}

{angle_prompts.get(angle, angle_prompts['架构']).strip()}
""".strip(),
        expected_output=f"""
## {angle}方面优化建议

### files_read
- ...

### Top findings

#### 1. 问题标题
- 文件：...
- 行号：...
- 严重级别：...
- 问题：...
- 建议：...
- 预期收益：...

### 优先级排序
1. ...
""".strip(),
        agent=agent,
    )


def pipeline_develop_task(requirement: str, agent):
    return Task(
        description=f"""
{_BASE_INSTRUCTIONS}

你处在“分析 → 开发 → 审查 → 测试”的 pipeline 第 2 步。
需求：{requirement}

请参考上游架构分析结果，输出 patch 级开发方案。不要声称已经修改文件。
""".strip(),
        expected_output=develop_task(requirement, agent).expected_output,
        agent=agent,
    )


def pipeline_review_task(agent):
    return Task(
        description=f"""
{_BASE_INSTRUCTIONS}

你处在 pipeline 第 3 步。请审查上游开发方案的正确性、安全性、性能和可测试性。
重点指出：哪些改动可以接受、哪些需要调整、哪些风险必须先解决。
""".strip(),
        expected_output=review_task("上游开发方案", agent).expected_output,
        agent=agent,
    )


def pipeline_test_task(agent):
    return Task(
        description=f"""
{_BASE_INSTRUCTIONS}

你处在 pipeline 第 4 步。请基于上游开发方案和审查意见，设计对应 pytest 测试。
输出测试文件建议、fixture/mock 策略、关键测试代码和运行命令。
""".strip(),
        expected_output=test_task("上游开发方案涉及模块", agent).expected_output,
        agent=agent,
    )
