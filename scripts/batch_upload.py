#!/usr/bin/env python3
"""批量上传 DWG 文件到知识图谱"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

KG_API = "http://127.0.0.1:8000/api/v1/ingest/file"


def upload_dwg(filepath: Path) -> bool:
    print(f"上传: {filepath.name} ... ", end="", flush=True)
    try:
        with open(filepath, "rb") as f:
            resp = requests.post(
                KG_API,
                files={"file": (filepath.name, f, "application/octet-stream")},
                data={"use_llm": "true"},
                timeout=300,
            )
        if resp.status_code == 200:
            body = resp.json()
            print(f"OK ({body.get('entities_count', 0)}实体, {body.get('relations_count', 0)}关系)")
            return True
        else:
            print(f"失败 ({resp.status_code}): {resp.text[:200]}")
            return False
    except requests.ConnectionError:
        print("连接失败 — KG 服务是否已启动? (http://127.0.0.1:8000)")
        return False
    except Exception as e:
        print(f"错误: {e}")
        return False


def main(dir: str = None):
    if not dir:
        print("用法: python batch_upload.py <DWG目录>")
        return

    root = Path(dir)
    dwg_files = sorted(root.glob("*.DWG")) + sorted(root.glob("*.dwg"))

    if not dwg_files:
        print(f"目录下没有 DWG 文件: {root}")
        return

    print(f"找到 {len(dwg_files)} 个 DWG 文件")
    print(f"API: {KG_API}")
    print()

    ok = fail = 0
    for dwg in dwg_files:
        if upload_dwg(dwg):
            ok += 1
        else:
            fail += 1

    print(f"\n完成: {ok} 成功, {fail} 失败")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="批量上传 DWG 到知识图谱")
    parser.add_argument("dir", nargs="?", default=r"C:\Users\Knightz\Desktop\train_dwg")
    args = parser.parse_args()
    main(args.dir)
