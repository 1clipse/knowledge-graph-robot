#!/usr/bin/env python3
"""
增强版 DWG 训练数据标注管线
=============================
改进点:
  1. 描述润色 — 修复 %%c/%%p 等 CAD 乱码，去除套话
  2. 两阶段抽取 — 先抽实体，再抽关系（准确度更高）
  3. Schema 校验 — 检查类型是否在预定义列表中
  4. 数据增强 — 对描述做细微改写，生成更多训练样本
  5. 质量评分 — 标记低质量样本供人工复核
  6. 直接输出训练格式 — 兼容 03_qlora_train.py 和 LLaMA-Factory

Usage:
  python enhanced_label.py --dir "C:/Users/Knightz/Desktop/train_dwg"
  python enhanced_label.py --dir "C:/Users/Knightz/Desktop/train_dwg" --test
  python enhanced_label.py --dir "C:/Users/Knightz/Desktop/train_dwg" --augment 3
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# Fix Windows console encoding for Chinese output
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import requests

# ── 配置 ───────────────────────────────────────────
API_URL = "http://10.117.29.24:5200/v1/chat/completions"
MODEL = "qwen2.5-7b"
TIMEOUT = 180

_HERE = Path(__file__).resolve().parent
_DATA_DIR = _HERE / "data"
_DATA_DIR.mkdir(exist_ok=True)

OUTPUT_TRAINING = _DATA_DIR / "enhanced_labeled.json"
OUTPUT_LLAMAFACTORY = _DATA_DIR / "enhanced_labeled_llamafactory.json"

# ── Schema (与 industrial_robot.yaml 一致) ──────────
VALID_ENTITIES = {
    "Robot", "Manufacturer", "Component", "Reducer", "ServoMotor",
    "Controller", "Sensor", "ApplicationScenario", "Process",
    "EndEffector", "Standard", "Material", "Software",
}
VALID_RELATIONS = {
    "manufactures", "uses_reducer", "uses_servo", "uses_controller",
    "uses_component", "applied_in", "contains", "complies_with",
    "performs_process", "process_material",
}

# ── Gold examples (few-shot) ────────────────────────
FEWSHOT_EXAMPLES = """
【示例1】
输入文本: "安川 MOTOMAN GP180 是6轴工业机器人，负载180kg，臂展2702mm，重复定位精度±0.05mm。采用安川YRC1000控制器，应用于搬运和点焊场景。"
抽取结果:
{
  "entities": [
    {"name": "MOTOMAN GP180", "type": "Robot", "properties": {"axes": 6, "payload": 180, "reach": 2702, "repeatability": 0.05}},
    {"name": "安川", "type": "Manufacturer", "properties": {}},
    {"name": "YRC1000", "type": "Controller", "properties": {}},
    {"name": "搬运", "type": "ApplicationScenario", "properties": {}},
    {"name": "点焊", "type": "ApplicationScenario", "properties": {}}
  ],
  "relations": [
    {"source": {"name": "安川", "type": "Manufacturer"}, "target": {"name": "MOTOMAN GP180", "type": "Robot"}, "relation_type": "manufactures", "properties": {}},
    {"source": {"name": "MOTOMAN GP180", "type": "Robot"}, "target": {"name": "YRC1000", "type": "Controller"}, "relation_type": "uses_controller", "properties": {}},
    {"source": {"name": "MOTOMAN GP180", "type": "Robot"}, "target": {"name": "搬运", "type": "ApplicationScenario"}, "relation_type": "applied_in", "properties": {}},
    {"source": {"name": "MOTOMAN GP180", "type": "Robot"}, "target": {"name": "点焊", "type": "ApplicationScenario"}, "relation_type": "applied_in", "properties": {}}
  ]
}

【示例2】
输入文本: "CHX-3底盘旋转涡轮箱，材质A3板，包含蜗杆轴、涡轮。焊接后需磨床处理防止漏油。4-M10深20，6-M14深25。"
抽取结果:
{
  "entities": [
    {"name": "CHX-3底盘旋转涡轮箱", "type": "Component", "properties": {"material": "A3板"}},
    {"name": "蜗杆轴", "type": "Component", "properties": {}},
    {"name": "涡轮", "type": "Component", "properties": {}},
    {"name": "A3板", "type": "Material", "properties": {}},
    {"name": "磨床处理", "type": "Process", "properties": {"purpose": "防止漏油"}}
  ],
  "relations": [
    {"source": {"name": "CHX-3底盘旋转涡轮箱", "type": "Component"}, "target": {"name": "蜗杆轴", "type": "Component"}, "relation_type": "contains", "properties": {}},
    {"source": {"name": "CHX-3底盘旋转涡轮箱", "type": "Component"}, "target": {"name": "涡轮", "type": "Component"}, "relation_type": "contains", "properties": {}},
    {"source": {"name": "CHX-3底盘旋转涡轮箱", "type": "Component"}, "target": {"name": "A3板", "type": "Material"}, "relation_type": "process_material", "properties": {}},
    {"source": {"name": "CHX-3底盘旋转涡轮箱", "type": "Component"}, "target": {"name": "磨床处理", "type": "Process"}, "relation_type": "performs_process", "properties": {}}
  ]
}

【示例3】
输入文本: "CHX-3大手臂摆线减速机安装法兰，材质45#钢。用于连接大手臂与RV减速机。"
抽取结果:
{
  "entities": [
    {"name": "CHX-3大手臂摆线减速机安装法兰", "type": "Component", "properties": {"material": "45#钢"}},
    {"name": "大手臂", "type": "Component", "properties": {}},
    {"name": "RV减速机", "type": "Reducer", "properties": {}},
    {"name": "45#钢", "type": "Material", "properties": {}}
  ],
  "relations": [
    {"source": {"name": "CHX-3大手臂摆线减速机安装法兰", "type": "Component"}, "target": {"name": "大手臂", "type": "Component"}, "relation_type": "contains", "properties": {}},
    {"source": {"name": "CHX-3大手臂摆线减速机安装法兰", "type": "Component"}, "target": {"name": "RV减速机", "type": "Reducer"}, "relation_type": "uses_component", "properties": {}},
    {"source": {"name": "CHX-3大手臂摆线减速机安装法兰", "type": "Component"}, "target": {"name": "45#钢", "type": "Material"}, "relation_type": "process_material", "properties": {}}
  ]
}
"""

# ── 提示词 ─────────────────────────────────────────

POLISH_PROMPT = """你是一个资深机械工程师。下面是一张 DWG 工程图纸的结构化描述，请把它润色成一段 80-150 字的自然语言描述。

润色规则：
1. 去除无意义的技术标记符号（如 %%c、%%p、%%u 等 CAD 转义字符），把它们转成人类可读的文字
2. 去除 "SW_NOTE_0"、"SW_TABLEANNOTATION_0" 这类内部图块名称
3. 不要写 "这张图纸可能是"、"推测"、"可能" 等不确定措辞，直接描述
4. 保留所有有用的信息：零件名、材质、公差、表面处理、工艺要求、关键尺寸
5. 如果原文信息不足，只保留确切的信息，不要编造
6. 输出纯文本，不要 markdown"""

EXTRACT_ENTITIES_PROMPT = f"""你是一个工业机器人知识图谱实体抽取专家。

## 可抽取的实体类型
Robot（机器人）, Manufacturer（制造商）, Component（零部件）, Reducer（减速器）, ServoMotor（伺服电机）, Controller（控制器）, Sensor（传感器）, ApplicationScenario（应用场景）, Process（工艺）, EndEffector（末端执行器）, Standard（标准）, Material（材料）, Software（软件）

## 规则
1. 只抽取文本中明确提到的实体，不要编造
2. 实体名称要准确完整
3. 对于零件/组件类型的实体，如果能从文件名推断完整名称就用完整名称
4. 数值属性存到 properties 字典（数字不带单位）
5. 严格输出 JSON 数组，不要输出其他内容

## 参考示例
{FEWSHOT_EXAMPLES}

现在请从以下文本中抽取实体，只输出 entities 数组:"""

EXTRACT_RELS_PROMPT = f"""你是一个工业机器人知识图谱关系抽取专家。

## 可用的关系类型
- manufactures (Manufacturer→Robot)
- uses_reducer (Robot→Reducer)
- uses_servo (Robot→ServoMotor)
- uses_controller (Robot→Controller)
- uses_component (Robot/Component→Component)
- applied_in (Robot→ApplicationScenario)
- contains (Component→Component)
- complies_with (Robot/Component→Standard)
- performs_process (Robot→Process)
- process_material (Component→Material)

## 规则
1. 只使用上面列出的关系类型
2. source 和 target 必须都是上文已列出的实体
3. 不要编造不存在的关系
4. 严格输出 JSON 数组，不要输出其他内容

## 参考示例
{FEWSHOT_EXAMPLES}

上文已抽取的实体:
{{entities_json}}

现在请从以下文本中抽取关系，只输出 relations 数组:"""

AUGMENT_PROMPT = """你是一个数据增强助手。请对以下机械零件描述做细微改写，保持所有事实信息不变，但用不同的措辞、语序。输出改写后的文本（纯文本，不要 markdown）。

改写要求：
1. 保持所有零件名、材质、尺寸、公差信息完全不变
2. 可以调整语序、换同义词
3. 长度与原文相近

原文："""

# ── API 调用 ───────────────────────────────────────

def call_api(system_prompt: str, user_text: str, temperature: float = 0.1) -> Optional[str]:
    """调用 P100 API，非流式 → SSE fallback"""
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        "temperature": temperature,
        "max_tokens": 2048,
        "stream": False,
    }
    try:
        resp = requests.post(API_URL, json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception:
        pass

    # SSE fallback
    try:
        payload.pop("stream", None)
        resp = requests.post(API_URL, json=payload, timeout=TIMEOUT, stream=True)
        parts = []
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                break
            try:
                delta = json.loads(data).get("choices", [{}])[0].get("delta", {})
                if "content" in delta:
                    parts.append(delta["content"])
            except json.JSONDecodeError:
                continue
        return "".join(parts) if parts else None
    except Exception as e:
        print(f"  [API 错误] {e}")
        return None


def extract_json_array(text: str, key: str = None) -> Optional[list]:
    """从 LLM 输出中提取 JSON 数组"""
    text = text.strip()
    # 直接是数组
    if text.startswith("["):
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
    # 完整对象，取里面的 key
    if text.startswith("{"):
        try:
            obj = json.loads(text)
            if key and key in obj:
                return obj[key]
            if "entities" in obj:
                return obj["entities"]
            if "relations" in obj:
                return obj["relations"]
        except json.JSONDecodeError:
            pass
    # ```json 块
    for marker in ("```json", "```"):
        if marker in text:
            block = text.split(marker)[1].split("```")[0].strip()
            return extract_json_array(block, key)
    return None


def fix_truncated_json(text: str) -> Optional[list]:
    """尝试修复被截断的 JSON 数组"""
    text = text.strip()
    if text.startswith("["):
        # 尝试补全最后一个对象
        for suffix in ("}]", "}]}]", "]", "}]}]}]"):
            candidate = text.rstrip(",\n ") + suffix
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
    return None


# ── Schema 校验 ────────────────────────────────────

def validate_entities(entities: list) -> tuple[list, list[str]]:
    """校验实体，返回 (有效实体, 警告列表)"""
    warnings = []
    valid = []
    for e in entities:
        if not isinstance(e, dict):
            continue
        etype = e.get("type", "")
        if etype not in VALID_ENTITIES:
            warnings.append(f"未知实体类型 '{etype}' → 实体 '{e.get('name', '?')}'")
            # 尝试纠正: Component 作为默认
            if etype in ("Part", "Assembly", "零件", "组件", "部件", "法兰", "箱体", "盖板"):
                e["type"] = "Component"
                warnings[-1] += " → 已修正为 Component"
            else:
                continue
        if not e.get("name"):
            warnings.append("实体缺少 name 字段，已跳过")
            continue
        if not isinstance(e.get("properties"), dict):
            e["properties"] = {}
        valid.append(e)
    return valid, warnings


def validate_relations(relations: list, entity_names: set) -> tuple[list, list[str]]:
    """校验关系，返回 (有效关系, 警告列表)"""
    warnings = []
    valid = []
    for r in relations:
        if not isinstance(r, dict):
            continue
        rtype = r.get("relation_type", "")
        if rtype not in VALID_RELATIONS:
            warnings.append(f"未知关系类型 '{rtype}'，已跳过")
            continue
        src = r.get("source", {})
        tgt = r.get("target", {})
        if not src.get("name") or not tgt.get("name"):
            warnings.append("关系缺少 source/target name，已跳过")
            continue
        if src["name"] not in entity_names:
            warnings.append(f"关系 source '{src['name']}' 未在实体列表中，已跳过")
            continue
        if tgt["name"] not in entity_names:
            warnings.append(f"关系 target '{tgt['name']}' 未在实体列表中，已跳过")
            continue
        if not isinstance(r.get("properties"), dict):
            r["properties"] = {}
        valid.append(r)
    return valid, warnings


def quality_score(entities: list, relations: list, warnings: list) -> str:
    """给样本打分: good / ok / review"""
    if not entities:
        return "review"
    if len(warnings) > 3:
        return "review"
    if len(entities) >= 2 and len(relations) >= 1:
        return "good"
    if len(entities) >= 1:
        return "ok"
    return "review"


# ── 核心流程 ───────────────────────────────────────

def polish_description(raw_desc: str, filename: str) -> Optional[str]:
    """润色描述文本"""
    print(f"  润色描述... ", end="", flush=True)
    user_input = f"文件名: {filename}\n\n原始描述:\n{raw_desc}"
    result = call_api(POLISH_PROMPT, user_input, temperature=0.2)
    if result and len(result) > 30:
        print(f"{len(result)} 字符 ✓")
        return result.strip()
    print("失败，使用原始描述")
    return raw_desc


def extract_entities_stage(description: str) -> tuple[list, list[str]]:
    """阶段1: 抽取实体"""
    print(f"  抽取实体... ", end="", flush=True)
    result = call_api(EXTRACT_ENTITIES_PROMPT, description, temperature=0.05)
    if not result:
        return [], ["API 调用失败"]

    entities = extract_json_array(result) or fix_truncated_json(result)
    if not entities:
        # 尝试作为完整对象解析
        try:
            obj = json.loads(result.strip())
            if "entities" in obj:
                entities = obj["entities"]
        except json.JSONDecodeError:
            pass
    if not entities:
        return [], [f"JSON 解析失败: {result[:200]}..."]

    valid, warnings = validate_entities(entities)
    print(f"{len(valid)}个 ✓" if valid else f"0个 (警告: {len(warnings)})")
    return valid, warnings


def extract_relations_stage(description: str, entities: list) -> tuple[list, list[str]]:
    """阶段2: 抽取关系"""
    if len(entities) < 2:
        return [], []

    print(f"  抽取关系... ", end="", flush=True)
    entities_json = json.dumps(entities, ensure_ascii=False, indent=2)
    prompt = EXTRACT_RELS_PROMPT.replace("{entities_json}", entities_json)
    result = call_api(prompt, f"{prompt}\n\n文本: {description}", temperature=0.05)
    if not result:
        return [], ["API 调用失败"]

    relations = extract_json_array(result) or fix_truncated_json(result)
    if not relations:
        try:
            obj = json.loads(result.strip())
            if "relations" in obj:
                relations = obj["relations"]
        except json.JSONDecodeError:
            pass
    if not relations:
        # 没抽到关系不是致命错误
        print("0个")
        return [], []

    entity_names = {e["name"] for e in entities}
    valid, warnings = validate_relations(relations, entity_names)
    print(f"{len(valid)}个 ✓" if valid else f"0个 (警告: {len(warnings)})")
    return valid, warnings


def augment_description(description: str, n_variants: int) -> list[str]:
    """生成 N 个改写版本"""
    variants = []
    for i in range(n_variants):
        print(f"  增强 {i+1}/{n_variants}... ", end="", flush=True)
        result = call_api(AUGMENT_PROMPT, description, temperature=0.7)
        if result and len(result) > 30 and result != description:
            variants.append(result.strip())
            print(f"✓")
        else:
            print("跳过")
    return variants


def process_file(txt_path: Path, augment_count: int) -> list[dict]:
    """处理单个 DWG 的描述文件，返回训练样本列表"""
    stem = txt_path.stem
    with open(txt_path, "r", encoding="utf-8") as f:
        raw_desc = f.read().strip()

    print(f"\n{'─'*60}")
    print(f"处理: {stem}")
    print(f"  原始描述: {len(raw_desc)} 字符")

    # Step 1: 润色描述
    polished = polish_description(raw_desc, stem)
    if polished != raw_desc:
        # 更新 .txt 文件
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(polished)

    # Step 2: 增强描述（可选）
    all_descriptions = [polished]
    if augment_count > 0:
        variants = augment_description(polished, augment_count)
        all_descriptions.extend(variants)

    # Step 3: 对每个描述版本做两阶段抽取
    samples = []
    for desc_idx, desc in enumerate(all_descriptions):
        if desc_idx > 0:
            print(f"  --- 增强版本 {desc_idx} ---")

        entities, e_warnings = extract_entities_stage(desc)
        if not entities:
            print(f"  [跳过] 未抽取到实体")
            continue

        relations, r_warnings = extract_relations_stage(desc, entities)

        all_warnings = e_warnings + r_warnings
        score = quality_score(entities, relations, all_warnings)

        output_obj = {"entities": entities, "relations": relations}
        output_str = json.dumps(output_obj, ensure_ascii=False)

        sample = {
            "instruction": "你是一个专业的工业机器人领域知识图谱信息抽取专家。请从以下文本中抽取出实体和关系。\n\n## 实体类型\nRobot, Manufacturer, Component, Reducer, ServoMotor, Controller, Sensor, ApplicationScenario, Process, EndEffector, Standard, Material, Software\n\n## 关系类型\nmanufactures (Manufacturer→Robot), uses_reducer (Robot→Reducer), uses_servo (Robot→ServoMotor), uses_controller (Robot→Controller), uses_component (Robot→Component), applied_in (Robot→ApplicationScenario), contains (Component→Component), complies_with (Robot/Component→Standard), performs_process (Robot→Process), process_material (Component→Material)\n\n## 输出格式\n严格输出 JSON，包含 entities 和 relations 两个数组。不要输出任何其他内容。\n实体: {\"name\":\"实体名\",\"type\":\"类型\",\"properties\":{}}\n关系: {\"source\":{\"name\":\"源实体名\",\"type\":\"源类型\"},\"target\":{\"name\":\"目标实体名\",\"type\":\"目标类型\"},\"relation_type\":\"关系类型\",\"properties\":{}}",
            "input": desc,
            "output": output_str,
            "_meta": {
                "source_file": stem,
                "source_desc": txt_path.name,
                "quality": score,
                "warnings": all_warnings,
                "entity_count": len(entities),
                "relation_count": len(relations),
                "is_augmented": desc_idx > 0,
                "timestamp": datetime.now().isoformat(),
            },
        }
        samples.append(sample)
        print(f"  质量: {score} | {len(entities)}实体 {len(relations)}关系")

    return samples


def convert_to_llamafactory(samples: list[dict]) -> list[dict]:
    """转换为 LLaMA-Factory 格式 (prompt/query/response)"""
    converted = []
    for s in samples:
        converted.append({
            "prompt": s["instruction"],
            "query": s["input"],
            "response": s["output"],
            "system": "",
            "history": [],
        })
    return converted


def print_summary(samples: list[dict]):
    """打印汇总统计"""
    print(f"\n{'='*60}")
    print(f"汇总")
    print(f"{'='*60}")
    print(f"  总样本数: {len(samples)}")
    if not samples:
        return

    scores = {"good": 0, "ok": 0, "review": 0}
    total_e, total_r = 0, 0
    source_files = set()
    augmented = 0

    for s in samples:
        meta = s.get("_meta", {})
        scores[meta.get("quality", "review")] += 1
        total_e += meta.get("entity_count", 0)
        total_r += meta.get("relation_count", 0)
        source_files.add(meta.get("source_file", ""))
        if meta.get("is_augmented"):
            augmented += 1

    print(f"  源文件数: {len(source_files)}")
    print(f"  增强样本: {augmented}")
    print(f"  质量分布: good={scores['good']}, ok={scores['ok']}, review={scores['review']}")
    print(f"  总实体数: {total_e} (平均 {total_e/max(len(samples),1):.1f}/条)")
    print(f"  总关系数: {total_r} (平均 {total_r/max(len(samples),1):.1f}/条)")

    review_items = [s for s in samples if s.get("_meta", {}).get("quality") == "review"]
    if review_items:
        print(f"\n  ⚠ 需人工复核 ({len(review_items)} 条):")
        for s in review_items:
            meta = s["_meta"]
            print(f"    - {meta['source_file']}: {meta['warnings']}")


def augment_from_cleaned(cleaned_file: str, n_variants: int = 3, test: bool = False):
    """从清洗后的数据增强：改写 input 描述，保持 output 不变"""
    inpath = Path(cleaned_file)
    if not inpath.exists():
        print(f"文件不存在: {inpath}")
        return

    with open(inpath, "r", encoding="utf-8") as f:
        samples = json.load(f)

    print(f"加载清洗数据: {len(samples)} 条")
    print(f"每条生成 {n_variants} 个变体")

    # API check
    print(f"测试 API... ", end="", flush=True)
    try:
        r = requests.get("http://10.117.29.24:5200/health", timeout=5)
        print(f"OK")
    except Exception as e:
        print(f"失败: {e}")
        return

    if test:
        samples = samples[:1]
        print(f"⚠ 测试模式: 只处理 1 条")

    existing_inputs = {s["input"] for s in samples}
    new_samples = []
    original_count = len(samples)

    for i, sample in enumerate(samples):
        source = sample.get("_meta", {}).get("source_file", f"sample_{i}")
        desc = sample["input"]
        print(f"\n[{i+1}/{original_count}] {source}")

        for v in range(n_variants):
            print(f"  变体 {v+1}/{n_variants}... ", end="", flush=True)
            variant = call_api(AUGMENT_PROMPT, desc, temperature=0.7)
            if variant and len(variant) > 30 and variant != desc and variant not in existing_inputs:
                new_sample = deepcopy(sample)
                new_sample["input"] = variant
                new_sample["_meta"]["is_augmented"] = True
                new_sample["_meta"]["augmented_from"] = source
                new_sample["_meta"]["timestamp"] = datetime.now().isoformat()
                new_samples.append(new_sample)
                existing_inputs.add(variant)
                print(f"✓ ({len(variant)} 字符)")
            else:
                print("跳过 (重复或太短)")

        time.sleep(2)

    samples.extend(new_samples)

    # Save
    outpath = inpath.with_name(inpath.stem + "_augmented.json")
    with open(outpath, "w", encoding="utf-8") as f:
        json.dump(samples, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"增强完成: {original_count} 原始 + {len(new_samples)} 新增 = {len(samples)} 条")
    print(f"保存: {outpath}")

    # Also save LLaMA-Factory format
    lf_data = convert_to_llamafactory(samples)
    lf_path = _DATA_DIR / f"{inpath.stem}_augmented_llamafactory.json"
    with open(lf_path, "w", encoding="utf-8") as f:
        json.dump(lf_data, f, ensure_ascii=False, indent=2)
    print(f"LLaMA-Factory: {lf_path}")

    return samples


def main(dir: str = None, test: bool = False, augment: int = 0,
         skip_polish: bool = False, force: bool = False,
         from_cleaned: str = None):
    """
    Args:
        dir: DWG/描述文件所在目录
        test: 单文件测试模式
        augment: 每条生成的增强变体数 (from .txt mode)
        skip_polish: 跳过润色步骤
        force: 强制重新处理
        from_cleaned: 从清洗后的 JSON 增强 (--from-cleaned data/enhanced_labeled_cleaned.json --augment 3)
    """
    if from_cleaned:
        augment_from_cleaned(from_cleaned, n_variants=augment or 3, test=test)
        return

    if not dir:
        print("用法: python enhanced_label.py --dir <目录路径>")
        print("示例: python enhanced_label.py --dir \"C:\\Users\\Knightz\\Desktop\\train_dwg\"")
        print("增强: python enhanced_label.py --from-cleaned data/enhanced_labeled_cleaned.json --augment 3")
        return

    root = Path(dir)
    if not root.exists():
        print(f"目录不存在: {root}")
        return

    # 收集 .txt 文件
    txt_files = sorted(root.glob("*.txt"))
    dwg_files = {p.stem for p in root.glob("*.DWG")} | {p.stem for p in root.glob("*.dwg")}

    if not txt_files:
        print(f"目录下没有 .txt 描述文件: {root}")
        print("请先为每个 DWG 创建 .txt 描述文件")
        return

    print(f"目录: {root}")
    print(f"DWG 文件: {len(dwg_files)} 个")
    print(f"描述文件: {len(txt_files)} 个")
    print(f"增强倍数: {augment}x")
    print(f"API: {API_URL}")

    # 测试 API 连通性
    print(f"\n测试 API 连通性... ", end="", flush=True)
    try:
        r = requests.get("http://10.117.29.24:5200/health", timeout=5)
        print(f"OK (status={r.status_code})")
    except Exception as e:
        print(f"失败: {e}")
        print("请确认 P100 API 已启动")
        return

    if test:
        txt_files = txt_files[:1]
        print(f"\n⚠ 测试模式: 只处理 {txt_files[0].name}")

    # 加载已有标注
    existing = []
    if OUTPUT_TRAINING.exists() and not force:
        with open(OUTPUT_TRAINING, "r", encoding="utf-8") as f:
            existing = json.load(f)
        print(f"\n已有标注: {len(existing)} 条")

    existing_sources = {
        (item.get("_meta", {}).get("source_file", ""),
         item.get("input", ""))
        for item in existing
    }

    new_samples = []

    for i, txt_path in enumerate(txt_files):
        # 检查是否有对应 DWG
        stem = txt_path.stem
        if stem not in dwg_files:
            print(f"\n[{i+1}/{len(txt_files)}] {txt_path.name} — 无对应 DWG，跳过")
            continue

        # 检查是否已处理
        if stem in {s.get("_meta", {}).get("source_file", "") for s in existing} and not force:
            print(f"\n[{i+1}/{len(txt_files)}] {stem} — 已有标注，跳过")
            continue

        try:
            samples = process_file(txt_path, augment_count=augment)
            for s in samples:
                key = (s["_meta"]["source_file"], s["input"])
                if key not in existing_sources:
                    new_samples.append(s)
                    existing_sources.add(key)
        except Exception as e:
            print(f"  [错误] {e}")
            import traceback
            traceback.print_exc()

        # 每处理完一个文件就保存
        all_samples = existing + new_samples
        with open(OUTPUT_TRAINING, "w", encoding="utf-8") as f:
            json.dump(all_samples, f, ensure_ascii=False, indent=2)

        time.sleep(2)

    # ── 保存 ──
    all_samples = existing + new_samples
    print_summary(all_samples)

    with open(OUTPUT_TRAINING, "w", encoding="utf-8") as f:
        json.dump(all_samples, f, ensure_ascii=False, indent=2)
    print(f"\n训练数据 → {OUTPUT_TRAINING}")

    # LLaMA-Factory 格式
    lf_data = convert_to_llamafactory(all_samples)
    with open(OUTPUT_LLAMAFACTORY, "w", encoding="utf-8") as f:
        json.dump(lf_data, f, ensure_ascii=False, indent=2)
    print(f"LLaMA-Factory 格式 → {OUTPUT_LLAMAFACTORY}")

    # 同时输出 dataset_info.json
    dataset_info = {
        "kg_robot_enhanced": {
            "file_name": "enhanced_labeled_llamafactory.json",
            "columns": {
                "prompt": "prompt",
                "query": "query",
                "response": "response",
                "system": "system",
                "history": "history",
            },
        },
    }
    info_path = _DATA_DIR / "dataset_info.json"
    # 合并已有
    if info_path.exists():
        with open(info_path, "r", encoding="utf-8") as f:
            old_info = json.load(f)
        old_info.update(dataset_info)
        dataset_info = old_info
    with open(info_path, "w", encoding="utf-8") as f:
        json.dump(dataset_info, f, ensure_ascii=False, indent=2)
    print(f"Dataset info → {info_path}")

    # 提示下一步
    print(f"\n{'='*60}")
    print("下一步:")
    print(f"  1. 检查标注质量: 打开 {OUTPUT_TRAINING}")
    print(f"     - 搜索 'quality': 'review' 的条目，人工修正")
    print(f"     - 修正后把 _meta.quality 改为 'good'")
    print(f"  2. 合并到主训练数据:")
    print(f"     cd E:\\Knowledge Graph_robot\\scripts\\finetune")
    print(f"     python 02_prepare_data.py merge")
    print(f"  3. 上传 P100 训练:")
    print(f'     scp {OUTPUT_TRAINING} z@10.117.29.24:/data/finetune/data/handcrafted_examples.json')
    print(f"  4. 开始训练:")
    print(f"     ssh z@10.117.29.24")
    print(f"     cd /data/finetune && CUDA_VISIBLE_DEVICES=0 python 03_qlora_train.py train")
    print(f"  (或用 LLaMA-Factory):")
    print(f'     scp {OUTPUT_LLAMAFACTORY} z@10.117.29.24:/data/finetune/data/')
    print(f'     scp {info_path} z@10.117.29.24:/data/finetune/data/')
    print(f"     llamafactory-cli train /data/finetune/09_train_config.yaml")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Enhanced DWG labeling pipeline")
    parser.add_argument("--dir", type=str, default=None, help="Directory containing DWG + .txt files")
    parser.add_argument("--test", action="store_true", help="Single file test mode")
    parser.add_argument("--augment", type=int, default=0, help="Number of augment variants per sample")
    parser.add_argument("--skip-polish", action="store_true", help="Skip description polishing")
    parser.add_argument("--force", action="store_true", help="Force re-process all files")
    parser.add_argument("--from-cleaned", type=str, default=None,
                        help="Augment from cleaned JSON (e.g. data/enhanced_labeled_cleaned.json)")
    args = parser.parse_args()

    main(dir=args.dir, test=args.test, augment=args.augment,
         skip_polish=args.skip_polish, force=args.force,
         from_cleaned=args.from_cleaned)
