from __future__ import annotations

from extractors.rule_extractor import RuleExtractor


# ── Entity extraction ───────────────────────────────────────────

class TestEntityExtraction:
    def test_robot_extraction_with_specs(self):
        """从文本中抽取 Robot 及其属性（轴数）"""
        text = "FANUC M-20iA 是一款 6 轴工业机器人。"
        extractor = RuleExtractor()
        result = extractor.extract(text)

        robot = _find_entity_containing(result.entities, "M-20iA", "Robot")
        assert robot is not None, f"Expected Robot entity, got: {[(e.name, e.type) for e in result.entities]}"
        assert robot.properties.get("axes") == 6

    def test_robot_payload_extraction(self):
        """抽取 Robot 负载属性（关键字紧邻名称时生效）"""
        text = "FANUC M-20iA 负载 20kg。"
        extractor = RuleExtractor()
        result = extractor.extract(text)

        robot = _find_entity_containing(result.entities, "M-20iA", "Robot")
        assert robot is not None
        assert robot.properties.get("payload") == 20

    def test_robot_reach_extraction(self):
        """抽取 Robot 臂展属性"""
        text = "FANUC M-20iA 臂展 1853mm。"
        extractor = RuleExtractor()
        result = extractor.extract(text)

        robot = _find_entity_containing(result.entities, "M-20iA", "Robot")
        assert robot is not None
        assert robot.properties.get("reach") == 1853

    def test_manufacturer_extraction(self):
        """抽取 Manufacturer"""
        text = "FANUC 推出了一款新型协作机器人，ABB 也发布了新款焊接机器人。"
        extractor = RuleExtractor()
        result = extractor.extract(text)

        names = {e.name for e in result.entities if e.type == "Manufacturer"}
        assert "FANUC" in names
        assert "ABB" in names

    def test_reducer_extraction(self):
        """抽取 Reducer"""
        text = "该机器人采用 RV-40E 减速器，减速比 40:1。"
        extractor = RuleExtractor()
        result = extractor.extract(text)

        reducer = _find_entity(result.entities, "RV-40E")
        assert reducer is not None
        assert reducer.type == "Reducer"

    def test_servo_motor_extraction(self):
        """抽取 ServoMotor（模式捕获名含伺服电机后缀）"""
        text = "SGM7G-44A伺服电机提供强劲动力。"
        extractor = RuleExtractor()
        result = extractor.extract(text)

        motor = _find_entity_containing(result.entities, "SGM7G-44A", "ServoMotor")
        assert motor is not None, f"Expected ServoMotor, got: {[(e.name, e.type) for e in result.entities]}"
        assert motor.type == "ServoMotor"

    def test_controller_extraction(self):
        """抽取 Controller"""
        text = "R-30iB Plus 控制器支持 EtherCAT 通信协议。"
        extractor = RuleExtractor()
        result = extractor.extract(text)

        controller = _find_entity(result.entities, "R-30iB Plus")
        assert controller is not None
        assert controller.type == "Controller"

    def test_application_scenario_extraction(self):
        """抽取 ApplicationScenario"""
        text = "该机器人广泛应用于汽车焊接和电子装配。"
        extractor = RuleExtractor()
        result = extractor.extract(text)

        scenario_names = {e.name for e in result.entities if e.type == "ApplicationScenario"}
        assert len(scenario_names) > 0

    def test_process_extraction(self):
        """抽取 Process"""
        text = "可用于点焊、弧焊、搬运和码垛等工艺。"
        extractor = RuleExtractor()
        result = extractor.extract(text)

        process_names = {e.name for e in result.entities if e.type == "Process"}
        assert "点焊" in process_names
        assert "弧焊" in process_names

    def test_end_effector_extraction(self):
        """抽取 EndEffector（greedy CJK 前缀会捕获上下文词）"""
        text = "伺服焊枪是常见的末端执行器。"
        extractor = RuleExtractor()
        result = extractor.extract(text)

        effector = _find_entity_containing(result.entities, "焊枪", "EndEffector")
        assert effector is not None, f"Got: {[(e.name, e.type) for e in result.entities]}"

    def test_sensor_extraction(self):
        """抽取 Sensor"""
        text = "力矩传感器用于力控装配。"
        extractor = RuleExtractor()
        result = extractor.extract(text)

        sensor = _find_entity_containing(result.entities, "力矩传感器", "Sensor")
        assert sensor is not None, f"Got: {[(e.name, e.type) for e in result.entities]}"

    def test_standard_extraction(self):
        """抽取 Standard"""
        text = "该机器人符合 ISO 10218-1 安全标准。"
        extractor = RuleExtractor()
        result = extractor.extract(text)

        standard = _find_entity(result.entities, "ISO 10218-1")
        assert standard is not None
        assert standard.type == "Standard"


# ── Relation extraction ─────────────────────────────────────────

class TestRelationExtraction:
    def test_manufactures_relation(self):
        """抽取 manufactures 关系"""
        text = "FANUC 推出了 M-20iA 工业机器人。"
        extractor = RuleExtractor()
        result = extractor.extract(text)

        rel = _find_relation(result.relations, "manufactures")
        assert rel is not None
        assert rel.source.type == "Manufacturer"
        assert rel.target.type == "Robot"
        assert rel.source.name == "FANUC"

    def test_uses_reducer_relation(self):
        """抽取 uses_reducer 关系（要求目标名含'减速器'后缀）"""
        text = "M-20iA采用RV-40E减速器"
        extractor = RuleExtractor()
        result = extractor.extract(text)

        rel = _find_relation(result.relations, "uses_reducer")
        assert rel is not None, f"No uses_reducer in: {[(r.relation_type, r.source.name, r.target.name) for r in result.relations]}"
        assert rel.source.type == "Robot"
        assert rel.target.type == "Reducer"

    def test_uses_servo_relation(self):
        """抽取 uses_servo 关系（要求目标名含'伺服电机'后缀）"""
        text = "M-20iA使用αiF8伺服电机"
        extractor = RuleExtractor()
        result = extractor.extract(text)

        rel = _find_relation(result.relations, "uses_servo")
        assert rel is not None, f"No uses_servo in: {[(r.relation_type, r.source.name, r.target.name) for r in result.relations]}"

    def test_uses_controller_relation(self):
        """抽取 uses_controller 关系（动词：搭配|配合|使用|配备，目标名紧邻控制器）"""
        text = "M-20iA使用R-30iB控制器"
        extractor = RuleExtractor()
        result = extractor.extract(text)

        rel = _find_relation(result.relations, "uses_controller")
        assert rel is not None, f"No uses_controller in: {[(r.relation_type, r.source.name, r.target.name) for r in result.relations]}"

    def test_applied_in_relation(self):
        """抽取 applied_in 关系"""
        text = "M-20iA 广泛应用于汽车焊接。"
        extractor = RuleExtractor()
        result = extractor.extract(text)

        rel = _find_relation(result.relations, "applied_in")
        assert rel is not None
        assert rel.source.type == "Robot"
        assert rel.target.type == "ApplicationScenario"

    def test_complies_with_relation(self):
        """抽取 complies_with 关系"""
        text = "M-20iA 符合 ISO 10218-1 标准。"
        extractor = RuleExtractor()
        result = extractor.extract(text)

        rel = _find_relation(result.relations, "complies_with")
        assert rel is not None
        assert rel.target.type == "Standard"


# ── Edge cases ──────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_text(self):
        """空文本返回空结果"""
        extractor = RuleExtractor()
        result = extractor.extract("")
        assert len(result.entities) == 0
        assert len(result.relations) == 0

    def test_no_match_text(self):
        """无匹配文本返回空结果"""
        extractor = RuleExtractor()
        result = extractor.extract("今天天气很好，适合出去散步。")
        assert len(result.entities) == 0
        assert len(result.relations) == 0

    def test_duplicate_entities_merged(self):
        """重复实体合并属性（regex 合并同名 key）"""
        text = "FANUC M-20iA 是一款 6 轴机器人，FANUC M-20iA 负载 20kg。"
        extractor = RuleExtractor()
        result = extractor.extract(text)

        robots = _find_all_entities_containing(result.entities, "M-20iA", "Robot")
        assert len(robots) == 1
        assert robots[0].properties.get("axes") == 6
        assert robots[0].properties.get("payload") == 20

    def test_rule_confidence_is_high(self):
        """规则抽取的置信度高于 LLM（0.95 > 0.7）"""
        extractor = RuleExtractor()
        result = extractor.extract("FANUC M-20iA 是一款 6 轴工业机器人，额定负载 20kg。")
        for entity in result.entities:
            assert entity.confidence == 0.95
        for rel in result.relations:
            assert rel.confidence == 0.95


# ── Helpers ─────────────────────────────────────────────────────

def _find_entity(entities, name):
    for e in entities:
        if e.name == name:
            return e
    return None


def _find_entity_containing(entities, substring, entity_type=None):
    for e in entities:
        if substring in e.name:
            if entity_type is None or e.type == entity_type:
                return e
    return None


def _find_all_entities_containing(entities, substring, entity_type=None):
    result = []
    for e in entities:
        if substring in e.name:
            if entity_type is None or e.type == entity_type:
                result.append(e)
    return result


def _find_relation(relations, rel_type):
    for r in relations:
        if r.relation_type == rel_type:
            return r
    return None
