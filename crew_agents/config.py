"""
CrewAI Agent LLM 配置中心。

所有开发 Agent 默认使用 DeepSeek：
- 重任务：DeepSeek V4 Pro
- 轻任务：DeepSeek V4 Flash

后续换模型只需要改 config/.env 中的 KG_AGENT_* 配置。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 加载项目本地配置。config/.env 已被 .gitignore 忽略，不要提交真实 API Key。
ENV_FILE = Path(__file__).resolve().parent.parent / "config" / ".env"
load_dotenv(ENV_FILE)

# 允许 CrewAI 工具读取当前项目目录；Windows 下强制 UTF-8，避免中文文件解码失败。
os.environ["CREWAI_TOOLS_ALLOW_UNSAFE_PATHS"] = "true"
os.environ["PYTHONUTF8"] = "1"

from crewai import LLM


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


# ============================================================
# Agent 模型配置
# ============================================================
AGENT_PROVIDER = os.environ.get("KG_AGENT_PROVIDER", "deepseek").strip().lower()
AGENT_HEAVY_MODEL = os.environ.get("KG_AGENT_HEAVY_MODEL", "deepseek-v4-pro").strip()
AGENT_LIGHT_MODEL = os.environ.get("KG_AGENT_LIGHT_MODEL", "deepseek-v4-flash").strip()
AGENT_VERBOSE = _env_bool("KG_AGENT_VERBOSE", True)
AGENT_DISABLE_THINKING = _env_bool("KG_AGENT_DISABLE_THINKING", True)
AGENT_TEMPERATURE = float(os.environ.get("KG_AGENT_TEMPERATURE", "0.1"))
AGENT_MAX_TOKENS = int(os.environ.get("KG_AGENT_MAX_TOKENS", "4096"))

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").strip()


# ============================================================
# DeepSeek reasoning_content 兼容补丁
# ============================================================
def patch_deepseek_reasoning_content() -> None:
    """移除 DeepSeek 响应中的 reasoning_content，避免 CrewAI 多轮调用不兼容。

    CrewAI 通过 OpenAI-compatible provider 调 DeepSeek 时，某些模型响应会带
    reasoning_content。部分 OpenAI SDK / CrewAI 版本在下一轮消息回传时不接受该字段。
    这里只在 DeepSeek provider 下启用，并确保补丁只安装一次。
    """
    try:
        import openai
    except ImportError:
        return

    completions_cls = openai.resources.chat.completions.Completions
    if getattr(completions_cls.create, "_kg_deepseek_patched", False):
        return

    original_create = completions_cls.create

    def patched_create(self, *args, **kwargs):
        response = original_create(self, *args, **kwargs)
        for choice in getattr(response, "choices", []) or []:
            msg = getattr(choice, "message", None)
            if msg is None:
                continue
            if hasattr(msg, "reasoning_content"):
                msg.reasoning_content = None
            model_extra = getattr(msg, "model_extra", None)
            if model_extra:
                model_extra.pop("reasoning_content", None)
        return response

    patched_create._kg_deepseek_patched = True
    completions_cls.create = patched_create


if AGENT_PROVIDER == "deepseek":
    patch_deepseek_reasoning_content()


# ============================================================
# LLM 构造与校验
# ============================================================
def validate_agent_config() -> None:
    """启动前校验 Agent LLM 配置，给出清晰错误。"""
    if AGENT_PROVIDER != "deepseek":
        raise ValueError(
            f"当前只支持 KG_AGENT_PROVIDER=deepseek，实际为: {AGENT_PROVIDER!r}"
        )
    if not DEEPSEEK_API_KEY:
        raise ValueError(
            "缺少 DEEPSEEK_API_KEY。请在 config/.env 中配置，"
            "或设置系统环境变量 DEEPSEEK_API_KEY。"
        )
    if not DEEPSEEK_BASE_URL:
        raise ValueError("缺少 DEEPSEEK_BASE_URL。")
    if not AGENT_HEAVY_MODEL:
        raise ValueError("缺少 KG_AGENT_HEAVY_MODEL。")
    if not AGENT_LIGHT_MODEL:
        raise ValueError("缺少 KG_AGENT_LIGHT_MODEL。")


def build_llm(model: str, *, disable_thinking: bool = True) -> LLM:
    """构造 DeepSeek LLM。

    Args:
        model: DeepSeek 模型名，例如 deepseek-v4-pro / deepseek-v4-flash。
        disable_thinking: 是否通过 extra_body 禁用 thinking mode。
    """
    validate_agent_config()

    extra = {}
    if AGENT_DISABLE_THINKING:
        extra["additional_params"] = {
            "extra_body": {"thinking": {"type": "disabled"}}
        }

    return LLM(
        model=f"deepseek/{model}",
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
        temperature=AGENT_TEMPERATURE,
        max_tokens=AGENT_MAX_TOKENS,
        **extra,
    )


def agent_model_summary() -> str:
    """返回可打印的模型配置摘要；不包含 API Key。"""
    return (
        "CrewAI Agent 模型配置\n"
        f"provider: {AGENT_PROVIDER}\n"
        f"base_url: {DEEPSEEK_BASE_URL}\n"
        f"heavy_model: {AGENT_HEAVY_MODEL}\n"
        f"light_model: {AGENT_LIGHT_MODEL}\n"
        f"verbose: {AGENT_VERBOSE}\n"
        f"disable_thinking: {AGENT_DISABLE_THINKING}\n"
        f"temperature: {AGENT_TEMPERATURE}\n"
        f"max_tokens: {AGENT_MAX_TOKENS}\n"
        f"api_key_loaded: {'yes' if bool(DEEPSEEK_API_KEY) else 'no'}"
    )


# ============================================================
# 预设：给不同角色分配模型
# ============================================================
LLM_HEAVY = build_llm(AGENT_HEAVY_MODEL, disable_thinking=True)
LLM_LIGHT = build_llm(AGENT_LIGHT_MODEL, disable_thinking=True)
