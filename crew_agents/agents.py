"""
5 个开发 Agent —— 面向工业机器人知识图谱项目二次开发。

每个 Agent 都配备 FileReadTool，必须基于真实项目文件输出中文结论。
模型分配在 config.py 中集中管理：重任务使用 DeepSeek V4 Pro，轻任务使用 DeepSeek V4 Flash。
"""
from __future__ import annotations

from pathlib import Path

from crewai import Agent

from .config import AGENT_VERBOSE, LLM_HEAVY, LLM_LIGHT
from .tools import Utf8FileReadTool

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_file_reader = Utf8FileReadTool(project_root=PROJECT_ROOT)

_SHARED_RULES = (
    "通用规则：必须先用 FileReadTool 读取相关文件；"
    "不允许编造文件、函数、行号或测试结果；"
    "如果信息不足，先列出需要继续读取的文件；"
    "输出使用中文，文件路径使用项目相对路径。"
)

AGENT_SPECS = {
    "architect": {
        "role": "架构分析师",
        "goal": "深入理解项目代码结构，分析模块依赖、数据流、接口设计和可演进性",
        "backstory": (
            "你是 Python 后端架构师，精通 FastAPI、Neo4j、NLP、GraphRAG 和知识图谱。"
            "你关注模块边界、接口深度、数据流、配置入口和测试接缝。"
            f"{_SHARED_RULES}"
        ),
        "llm": "heavy",
    },
    "developer": {
        "role": "Python 后端开发",
        "goal": "根据需求给出与现有架构一致的 patch 级修改方案和代码片段",
        "backstory": (
            "你是项目主力开发。先读目标文件理解现有模式，再按项目风格设计改动。"
            "改 api/ 路由要同步考虑 security.py 注入防护；改 graph/ 层要注意 session 关闭；"
            "改 ingest / GraphRAG 要注意数据来源、置信度和 schema 校验。"
            f"{_SHARED_RULES}"
        ),
        "llm": "heavy",
    },
    "review": {
        "role": "代码审查专家",
        "goal": "审查代码正确性、安全漏洞、性能隐患、异常处理和风格问题",
        "backstory": (
            "你是资深代码审查者，专精 FastAPI、Neo4j、LLM 调用和数据摄入安全。"
            "重点检查 Cypher 注入防护、输入校验、认证授权、session 泄漏、异常吞噬、批量写入性能。"
            "每个发现都要给严重级别、文件位置、触发条件和修复建议。"
            f"{_SHARED_RULES}"
        ),
        "llm": "heavy",
    },
    "test": {
        "role": "测试开发工程师",
        "goal": "为项目模块设计 pytest 测试、fixture、mock 策略和覆盖盲区清单",
        "backstory": (
            "你是测试开发工程师。先读目标源码和 tests/ 下现有测试风格，再设计测试。"
            "优先覆盖正常路径、边界条件、异常路径、外部依赖 mock、Neo4j/LLM/CAD 文件隔离。"
            "测试命名使用 test_<函数>_<场景>，必要时使用 pytest fixture 和 parametrize。"
            f"{_SHARED_RULES}"
        ),
        "llm": "light",
    },
    "debug": {
        "role": "调试诊断专家",
        "goal": "根据错误日志和真实代码定位 bug 根因，给出最小修复方案和类似隐患",
        "backstory": (
            "你是调试诊断专家。分析 traceback 时要追溯调用链、输入状态和资源生命周期。"
            "重点关注 None/空列表、文件句柄、网络超时、Neo4j session、LLM 响应结构、编码问题。"
            "修复方案必须具体到文件和代码片段，并说明如何复现和回归测试。"
            f"{_SHARED_RULES}"
        ),
        "llm": "heavy",
    },
}


def _select_llm(kind: str):
    if kind == "light":
        return LLM_LIGHT
    return LLM_HEAVY


def build_agent(name: str) -> Agent:
    """根据 AGENT_SPECS 构造 CrewAI Agent。"""
    if name not in AGENT_SPECS:
        raise KeyError(f"未知 Agent: {name}，可选: {list(AGENT_SPECS)}")

    spec = AGENT_SPECS[name]
    return Agent(
        role=spec["role"],
        goal=spec["goal"],
        backstory=spec["backstory"],
        llm=_select_llm(spec["llm"]),
        verbose=AGENT_VERBOSE,
        allow_delegation=False,
        tools=[_file_reader],
    )


# 保持原导出变量名，兼容 run.py 和已有调用方。
architect_agent = build_agent("architect")
developer_agent = build_agent("developer")
review_agent = build_agent("review")
test_agent = build_agent("test")
debug_agent = build_agent("debug")

all_agents = {
    "architect": architect_agent,
    "developer": developer_agent,
    "review": review_agent,
    "test": test_agent,
    "debug": debug_agent,
}
