"""
CrewAI 多 Agent 协作 —— 工业机器人知识图谱项目二次开发。

用法:
    python crew_agents/run.py models
    python crew_agents/run.py analyze  --question "xxx"
    python crew_agents/run.py develop  --requirement "xxx"
    python crew_agents/run.py review   --target "api/routes/ask.py"
    python crew_agents/run.py test     --module "graph/writer.py"
    python crew_agents/run.py debug    --error "xxx"
    python crew_agents/run.py optimize
    python crew_agents/run.py pipeline --requirement "xxx"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from crewai import Crew, Process

from crew_agents.agents import (
    architect_agent,
    developer_agent,
    review_agent,
    test_agent,
    debug_agent,
)
from crew_agents.config import AGENT_VERBOSE, agent_model_summary
from crew_agents.tasks import (
    analyze_task,
    debug_task,
    develop_task,
    optimize_task,
    pipeline_develop_task,
    pipeline_review_task,
    pipeline_test_task,
    review_task,
    test_task,
)


def _run_single(agent, task):
    crew = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        verbose=AGENT_VERBOSE,
    )
    return crew.kickoff()


def run_models():
    """打印当前模型配置，不暴露 API Key。"""
    return agent_model_summary()


def run_analyze(question: str):
    """架构分析。"""
    return _run_single(architect_agent, analyze_task(question, architect_agent))


def run_develop(requirement: str):
    """功能开发方案。"""
    return _run_single(developer_agent, develop_task(requirement, developer_agent))


def run_review(target: str):
    """代码审查。"""
    return _run_single(review_agent, review_task(target, review_agent))


def run_test(module: str):
    """测试生成。"""
    return _run_single(test_agent, test_task(module, test_agent))


def run_debug(error_info: str):
    """调试诊断。"""
    return _run_single(debug_agent, debug_task(error_info, debug_agent))


def run_optimize():
    """5 Agent 协作审查项目优化点。"""
    tasks = [
        optimize_task("架构", architect_agent),
        optimize_task("安全", review_agent),
        optimize_task("质量", developer_agent),
        optimize_task("测试", test_agent),
        optimize_task("Bug", debug_agent),
    ]
    crew = Crew(
        agents=[architect_agent, review_agent, developer_agent, test_agent, debug_agent],
        tasks=tasks,
        process=Process.sequential,
        verbose=AGENT_VERBOSE,
    )
    crew.kickoff()

    output_parts = ["# 5 Agent 项目优化报告"]
    labels = ["架构分析", "安全审查", "代码质量", "测试覆盖", "潜在 Bug"]
    for label, task in zip(labels, tasks):
        output_parts.append(f"\n{'=' * 60}\n## {label}\n{'=' * 60}\n{task.output}")
    return "\n".join(output_parts)


def run_pipeline(requirement: str):
    """全流程协作：分析 → 开发 → 审查 → 测试。"""
    tasks = [
        analyze_task(requirement, architect_agent),
        pipeline_develop_task(requirement, developer_agent),
        pipeline_review_task(review_agent),
        pipeline_test_task(test_agent),
    ]
    crew = Crew(
        agents=[architect_agent, developer_agent, review_agent, test_agent],
        tasks=tasks,
        process=Process.sequential,
        verbose=AGENT_VERBOSE,
    )
    crew.kickoff()

    labels = ["1. 架构分析", "2. 开发方案", "3. 审查意见", "4. 测试方案"]
    output_parts = [f"# Pipeline 执行报告\n\n需求：{requirement}"]
    for label, task in zip(labels, tasks):
        output_parts.append(f"\n{'=' * 60}\n## {label}\n{'=' * 60}\n{task.output}")
    return "\n".join(output_parts)


def main():
    parser = argparse.ArgumentParser(description="工业机器人知识图谱 —— 多 Agent 开发系统")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("models", help="显示当前 Agent 模型配置（不显示 API Key）")

    p1 = sub.add_parser("analyze", help="架构分析")
    p1.add_argument("--question", required=True)

    p2 = sub.add_parser("develop", help="功能开发方案")
    p2.add_argument("--requirement", required=True)

    p3 = sub.add_parser("review", help="代码审查")
    p3.add_argument("--target", required=True)

    p4 = sub.add_parser("test", help="测试生成")
    p4.add_argument("--module", required=True)

    p5 = sub.add_parser("debug", help="调试诊断")
    p5.add_argument("--error", required=True)

    p6 = sub.add_parser("pipeline", help="全流程：分析→开发→审查→测试")
    p6.add_argument("--requirement", required=True)

    sub.add_parser("optimize", help="5 Agent 协作审查项目优化点")

    args = parser.parse_args()

    dispatch = {
        "models": run_models,
        "analyze": lambda: run_analyze(args.question),
        "develop": lambda: run_develop(args.requirement),
        "review": lambda: run_review(args.target),
        "test": lambda: run_test(args.module),
        "debug": lambda: run_debug(args.error),
        "pipeline": lambda: run_pipeline(args.requirement),
        "optimize": run_optimize,
    }

    if args.command in dispatch:
        result = dispatch[args.command]()
        print("\n" + "=" * 60)
        print("执行结果:")
        print("=" * 60)
        print(result)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
