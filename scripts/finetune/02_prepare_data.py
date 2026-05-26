#!/usr/bin/env python3
"""
准备微调数据：从 Neo4j 导出已有实体/关系 + 手工修正 → QLoRA 训练格式
也可以在 data/knowledge_extraction.json 直接写手工标注数据
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

_HERE = Path(__file__).resolve().parent
_DATA_DIR = _HERE / "data"
_DATA_DIR.mkdir(exist_ok=True)

# ============================================================
# 训练数据格式说明
# 每条数据 = 一个 instruction + input(原始文本) + output(期望的抽取结果)
# ============================================================

EXTRACTION_SYSTEM = """你是一个专业的工业机器人领域知识图谱信息抽取专家。请从以下文本中抽取出实体和关系。

## 实体类型
Robot, Manufacturer, Component, Reducer, ServoMotor, Controller, Sensor, ApplicationScenario, Process, EndEffector, Standard, Material, Software

## 关系类型
manufactures (Manufacturer→Robot), uses_reducer (Robot→Reducer), uses_servo (Robot→ServoMotor), uses_controller (Robot→Controller), uses_component (Robot→Component), applied_in (Robot→ApplicationScenario), contains (Component→Component)

## 输出格式
严格输出 JSON，包含 entities 和 relations 两个数组。
实体: {"name":"实体名","type":"类型","properties":{}}
关系: {"source":{"name":"源","type":"源类型"},"target":{"name":"目标","type":"目标类型"},"relation_type":"关系类型","properties":{}}

## 规则
1. 类型必须用预定义列表中的
2. 实体名称要准确完整，不要缩写
3. 数值属性提取为数字，不要带单位
4. 不要编造文本中没有的信息"""


def export_from_neo4j(output_path: str = None) -> None:
    """
    从 Neo4j 导出已有数据集作为种子数据
    需要用户手工修正后使用
    """
    from neo4j import GraphDatabase

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USERNAME", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")

    if not password:
        print("请设置 NEO4J_PASSWORD 环境变量")
        return

    driver = GraphDatabase.driver(uri, auth=(user, password))
    samples: List[Dict[str, Any]] = []

    with driver.session() as session:
        # 查找所有包含实体和关系的文本片段
        result = session.run(
            "MATCH (n) WHERE n.file IS NOT NULL "
            "WITH DISTINCT n.file AS file "
            "MATCH (e) WHERE e.file = file "
            "RETURN file, collect(DISTINCT {name: e.name, type: labels(e)[0]}) AS entities "
            "LIMIT 20"
        )

        for record in result:
            file = record["file"]
            entities = record["entities"]
            # 查这个文件相关的所有关系
            rel_result = session.run(
                "MATCH (s)-[r]->(t) WHERE s.file = $file AND t.file = $file "
                "RETURN labels(s)[0] AS s_label, s.name AS s_name, "
                "labels(t)[0] AS t_label, t.name AS t_name, "
                "type(r) AS rel_type",
                file=file
            )
            relations = [
                {
                    "source": {"name": r["s_name"], "type": r["s_label"]},
                    "target": {"name": r["t_name"], "type": r["t_label"]},
                    "relation_type": r["rel_type"],
                    "properties": {},
                }
                for r in rel_result
            ]

            # 这是自动生成的需要人工验证的种子
            sample = {
                "instruction": EXTRACTION_SYSTEM,
                "input": f"文件 {file} 中包含以下CAD实体。请验证并补充抽取。",
                "output": json.dumps(
                    {"entities": entities, "relations": relations},
                    ensure_ascii=False,
                    indent=2,
                ),
            }
            samples.append(sample)

    driver.close()

    out_path = output_path or str(_DATA_DIR / "seed_from_neo4j.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(samples, f, ensure_ascii=False, indent=2)
    print(f"导出 {len(samples)} 条种子数据 → {out_path}")
    print("⚠️  这些数据需要人工验证修正后才能用于微调！")


def create_handcrafted_examples() -> List[Dict[str, Any]]:
    """
    手工标注的高质量样本，直接可用于微调
    这是最重要的部分 — 质量 > 数量
    建议至少准备 50-100 条高质量标注数据
    """
    examples = [
        # ===== 示例 1 =====
        {
            "instruction": EXTRACTION_SYSTEM,
            "input": "FANUC公司推出的M-20iA是一款6轴工业机器人，额定负载20kg，臂展1853mm，"
            "重复定位精度±0.02mm。该机器人采用FANUC自研的RV减速器和αi系列伺服电机，"
            "广泛应用于搬运和装配场景。M-20iA搭配R-30iB Plus控制器，支持EtherCAT通信协议。",
            "output": json.dumps(
                {
                    "entities": [
                        {"name": "FANUC", "type": "Manufacturer", "properties": {"country": "日本"}},
                        {"name": "M-20iA", "type": "Robot", "properties": {"axes": 6, "payload_kg": 20, "reach_mm": 1853, "repeatability_mm": 0.02}},
                        {"name": "RV减速器", "type": "Reducer", "properties": {"reducer_type": "RV"}},
                        {"name": "αi系列伺服电机", "type": "ServoMotor", "properties": {}},
                        {"name": "R-30iB Plus", "type": "Controller", "properties": {"protocol": "EtherCAT"}},
                        {"name": "搬运", "type": "ApplicationScenario", "properties": {}},
                        {"name": "装配", "type": "ApplicationScenario", "properties": {}},
                    ],
                    "relations": [
                        {"source": {"name": "FANUC", "type": "Manufacturer"}, "target": {"name": "M-20iA", "type": "Robot"}, "relation_type": "manufactures", "properties": {}},
                        {"source": {"name": "M-20iA", "type": "Robot"}, "target": {"name": "RV减速器", "type": "Reducer"}, "relation_type": "uses_reducer", "properties": {}},
                        {"source": {"name": "M-20iA", "type": "Robot"}, "target": {"name": "αi系列伺服电机", "type": "ServoMotor"}, "relation_type": "uses_servo", "properties": {}},
                        {"source": {"name": "M-20iA", "type": "Robot"}, "target": {"name": "R-30iB Plus", "type": "Controller"}, "relation_type": "uses_controller", "properties": {}},
                        {"source": {"name": "M-20iA", "type": "Robot"}, "target": {"name": "搬运", "type": "ApplicationScenario"}, "relation_type": "applied_in", "properties": {}},
                        {"source": {"name": "M-20iA", "type": "Robot"}, "target": {"name": "装配", "type": "ApplicationScenario"}, "relation_type": "applied_in", "properties": {}},
                    ],
                },
                ensure_ascii=False,
            ),
        },
        # ===== 示例 2 — CAD 图纸文本 =====
        {
            "instruction": EXTRACTION_SYSTEM,
            "input": "DXF 文件: 机器人底座加工图纸.dxf\n"
            "包含 12 个图块: BASE_PLATE, MOTOR_BRACKET, REDUCER_FLANGE, "
            "BEARING_HOUSING, SHAFT_COUPLER, OIL_SEAL, BOLT_M8, NUT_M8, "
            "WASHER_M8, ALIGNMENT_PIN, LIFTING_EYE, LUBRICATION_NIPPLE\n"
            "图层: 粗实线, 尺寸线, 中心线, 剖面线, 标注, 隐藏线\n"
            "标注文字: 材质45#钢, 调质处理HB220-250, 未注倒角C2, "
            "表面粗糙度Ra3.2, 配合公差H7/g6",
            "output": json.dumps(
                {
                    "entities": [
                        {"name": "机器人底座加工图纸.dxf", "type": "Component", "properties": {"doc_type": "CAD_Drawing"}},
                        {"name": "BASE_PLATE", "type": "Component", "properties": {"source": "DXF_BLOCK"}},
                        {"name": "MOTOR_BRACKET", "type": "Component", "properties": {"source": "DXF_BLOCK"}},
                        {"name": "REDUCER_FLANGE", "type": "Component", "properties": {"source": "DXF_BLOCK"}},
                        {"name": "BEARING_HOUSING", "type": "Component", "properties": {"source": "DXF_BLOCK"}},
                        {"name": "SHAFT_COUPLER", "type": "Component", "properties": {"source": "DXF_BLOCK"}},
                        {"name": "45#钢", "type": "Material", "properties": {"hardness": "HB220-250", "treatment": "调质"}},
                    ],
                    "relations": [
                        {"source": {"name": "机器人底座加工图纸.dxf", "type": "Component"}, "target": {"name": "BASE_PLATE", "type": "Component"}, "relation_type": "contains", "properties": {}},
                        {"source": {"name": "机器人底座加工图纸.dxf", "type": "Component"}, "target": {"name": "MOTOR_BRACKET", "type": "Component"}, "relation_type": "contains", "properties": {}},
                        {"source": {"name": "BASE_PLATE", "type": "Component"}, "target": {"name": "45#钢", "type": "Material"}, "relation_type": "process_material", "properties": {}},
                    ],
                },
                ensure_ascii=False,
            ),
        },
    ]
    return examples


def validate_data(data: List[Dict[str, Any]]) -> None:
    """验证数据格式"""
    errors = []
    valid_types = {
        "Robot", "Manufacturer", "Component", "Reducer", "ServoMotor",
        "Controller", "Sensor", "ApplicationScenario", "Process",
        "EndEffector", "Standard", "Material", "Software",
    }
    valid_rels = {
        "manufactures", "uses_reducer", "uses_servo", "uses_controller",
        "uses_component", "applied_in", "contains", "complies_with",
        "performs_process", "process_requires", "process_material",
    }

    for i, item in enumerate(data):
        if "instruction" not in item or "input" not in item or "output" not in item:
            errors.append(f"条目 {i}: 缺少 instruction/input/output 字段")
            continue
        try:
            output = json.loads(item["output"])
            for e in output.get("entities", []):
                if e["type"] not in valid_types:
                    errors.append(f"条目 {i}: 实体 '{e['name']}' 类型 '{e['type']}' 无效")
            for r in output.get("relations", []):
                if r["relation_type"] not in valid_rels:
                    errors.append(f"条目 {i}: 关系类型 '{r['relation_type']}' 无效")
        except json.JSONDecodeError as e:
            errors.append(f"条目 {i}: output 不是合法 JSON: {e}")

    if errors:
        print("数据验证失败:")
        for e in errors:
            print(f"  ❌ {e}")
    else:
        print(f"✅ 数据验证通过，共 {len(data)} 条")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "export":
        export_from_neo4j()
    else:
        # 验证手工数据
        examples = create_handcrafted_examples()
        validate_data(examples)

        out = _DATA_DIR / "handcrafted_examples.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(examples, f, ensure_ascii=False, indent=2)
        print(f"示例数据已写入 {out}")
        print(f"共 {len(examples)} 条")
        print()
        print("=== 下一步 ===")
        print("1. 在此文件基础上添加更多标注数据（建议 50-100 条）")
        print("2. 把你的 DWG/DXF 文本 + 正确抽取结果写入")
        print("3. 运行 03_qlora_train.py 开始训练")
