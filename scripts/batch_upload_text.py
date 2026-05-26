#!/usr/bin/env python3
"""用 .txt 描述文件 → 微调LLM抽取 → 写入Neo4j，替代 DXF 规则引擎"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

KG_API = "http://127.0.0.1:8000"


def clear_graph():
    """跳过清空（/query 端点禁止 DELETE），追加式入图"""
    print("跳过清空，新数据将以追加方式入库")


def ingest_text(text: str, source_file: str) -> bool:
    """通过 /ingest/text 用微调LLM抽取并入库"""
    print(f"  抽取入库: {source_file} ... ", end="", flush=True)
    try:
        resp = requests.post(
            f"{KG_API}/api/v1/ingest/text",
            json={
                "text": text,
                "use_llm": True,
                "use_rule_fallback": False,  # 只用微调模型，不用规则回退
            },
            timeout=180,
        )
        if resp.status_code == 200:
            body = resp.json()
            print(f"OK ({body.get('entities_count', 0)}实体, {body.get('relations_count', 0)}关系)")
            return True
        else:
            print(f"失败 ({resp.status_code}): {resp.text[:200]}")
            return False
    except requests.ConnectionError:
        print("连接失败 — KG 服务启动了吗?")
        return False
    except Exception as e:
        print(f"错误: {e}")
        return False


def main(dir: str = None, clear: bool = False):
    if not dir:
        print("用法: python batch_upload_text.py <目录> [--clear]")
        return

    root = Path(dir)
    txt_files = sorted(root.glob("*.txt"))
    if not txt_files:
        print(f"目录下没有 .txt 文件: {root}")
        return

    # Health check
    try:
        r = requests.get(f"{KG_API}/health", timeout=5)
        print(f"KG 服务: {r.json()}")
    except Exception:
        print(f"KG 服务未启动 ({KG_API})")
        return

    if clear:
        clear_graph()

    print(f"\n找到 {len(txt_files)} 个描述文件\n")

    ok = fail = 0
    for txt in txt_files:
        with open(txt, "r", encoding="utf-8") as f:
            text = f.read().strip()

        print(f"[{ok+fail+1}/{len(txt_files)}] {txt.stem}")
        print(f"  文本: {len(text)} 字符")

        if ingest_text(text, txt.stem):
            ok += 1
        else:
            fail += 1

        time.sleep(2)  # 别打崩 API

    print(f"\n完成: {ok} 成功, {fail} 失败")
    print(f"打开 http://{KG_API}/chat 提问")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="用描述文本+微调LLM重新入图")
    parser.add_argument("dir", nargs="?", default=r"C:\Users\Knightz\Desktop\train_dwg")
    parser.add_argument("--clear", action="store_true", help="先清空旧数据")
    args = parser.parse_args()
    main(args.dir, args.clear)
