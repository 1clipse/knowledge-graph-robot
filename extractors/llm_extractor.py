from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
from loguru import logger
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from config.settings import LLMConfig, get_config
from extractors.llm_utils import llm_chat
from schema.loader import build_schema_prompt_context


EXTRACTION_SYSTEM_PROMPT = """你是一个专业的工业机器人领域知识图谱信息抽取专家。你的任务是从给定的文本中抽取出实体和关系，严格按照指定的JSON格式输出。

{schema_context}

## 输出格式要求
你必须输出严格的JSON格式，包含两个数组：entities 和 relations。

### 实体格式
```json
{{
  "name": "实体名称",
  "type": "实体类型(必须为上述实体类型之一)",
  "properties": {{
    "属性名": "属性值"
  }}
}}
```

### 关系格式
```json
{{
  "source": {{
    "name": "源实体名称",
    "type": "源实体类型"
  }},
  "target": {{
    "name": "目标实体名称",
    "type": "目标实体类型"
  }},
  "relation_type": "关系类型(必须为上述关系类型之一)",
  "properties": {{
    "属性名": "属性值"
  }}
}}
```

## 抽取规则
1. 实体类型必须严格使用上述定义的类型，不可自创
2. 关系类型必须严格使用上述定义的类型，源和目标类型必须匹配定义
3. 实体名称要准确完整，不要缩写
4. 属性值要如实提取，不要编造文本中没有的信息
5. 尽量抽取多跳关系：如果A与B有关系，B与C有关系，都要抽取
6. 数值属性请提取为数字，不要带单位说明
7. 如果文本中提到时间信息（推出年份、停产年份、发布时间等），在关系中增加 "valid_from" 和 "valid_to" 字段
8. 如果文本中没有可抽取的信息，返回空数组

## Few-shot 示例

输入文本：
FANUC公司推出的M-20iA是一款6轴工业机器人，额定负载20kg，臂展1853mm，重复定位精度±0.02mm。该机器人采用FANUC自研的RV减速器和αi系列伺服电机，广泛应用于搬运和装配场景。M-20iA搭配R-30iB Plus控制器，支持EtherCAT通信协议。

输出：
```json
{{
  "entities": [
    {{"name": "FANUC", "type": "Manufacturer", "properties": {{"country": "日本"}}}},
    {{"name": "M-20iA", "type": "Robot", "properties": {{"model": "M-20iA", "axes": 6, "payload": 20, "reach": 1853, "repeatability": 0.02, "application_type": "搬运/装配"}}}},
    {{"name": "RV减速器", "type": "Reducer", "properties": {{"reducer_type": "RV减速器"}}}},
    {{"name": "αi系列伺服电机", "type": "ServoMotor", "properties": {{}}}},
    {{"name": "R-30iB Plus", "type": "Controller", "properties": {{"communication_protocol": "EtherCAT"}}}},
    {{"name": "搬运", "type": "ApplicationScenario", "properties": {{}}}},
    {{"name": "装配", "type": "ApplicationScenario", "properties": {{}}}}
  ],
  "relations": [
    {{"source": {{"name": "FANUC", "type": "Manufacturer"}}, "target": {{"name": "M-20iA", "type": "Robot"}}, "relation_type": "manufactures", "properties": {{}}}},
    {{"source": {{"name": "M-20iA", "type": "Robot"}}, "target": {{"name": "RV减速器", "type": "Reducer"}}, "relation_type": "uses_reducer", "properties": {{}}}},
    {{"source": {{"name": "M-20iA", "type": "Robot"}}, "target": {{"name": "αi系列伺服电机", "type": "ServoMotor"}}, "relation_type": "uses_servo", "properties": {{}}}},
    {{"source": {{"name": "M-20iA", "type": "Robot"}}, "target": {{"name": "R-30iB Plus", "type": "Controller"}}, "relation_type": "uses_controller", "properties": {{"is_default": true}}}},
    {{"source": {{"name": "M-20iA", "type": "Robot"}}, "target": {{"name": "搬运", "type": "ApplicationScenario"}}, "relation_type": "applied_in", "properties": {{}}}},
    {{"source": {{"name": "M-20iA", "type": "Robot"}}, "target": {{"name": "装配", "type": "ApplicationScenario"}}, "relation_type": "applied_in", "properties": {{}}}}
  ]
}}
```

输入文本（含时间信息）：
FANUC于2010年推出了M-20iA工业机器人，2024年宣布该型号停产。

输出：
```json
{{
  "entities": [
    {{"name": "FANUC", "type": "Manufacturer", "properties": {{}}}},
    {{"name": "M-20iA", "type": "Robot", "properties": {{"model": "M-20iA"}}}}
  ],
  "relations": [
    {{"source": {{"name": "FANUC", "type": "Manufacturer"}}, "target": {{"name": "M-20iA", "type": "Robot"}}, "relation_type": "manufactures", "properties": {{}}, "valid_from": "2010", "valid_to": "2024"}}
  ]
}}
```
"""


class ExtractedEntity(BaseModel):
    name: str
    type: str
    properties: Dict[str, Any] = Field(default_factory=dict)
    source: str = ""
    source_text: str = ""
    confidence: float = 0.7
    valid_from: str = ""
    valid_to: str = ""


class EntityRef(BaseModel):
    name: str
    type: str


class ExtractedRelation(BaseModel):
    source: EntityRef
    target: EntityRef
    relation_type: str
    properties: Dict[str, Any] = Field(default_factory=dict)
    source_ref: str = ""
    confidence: float = 0.7
    valid_from: str = ""
    valid_to: str = ""


class ExtractionResult(BaseModel):
    entities: List[ExtractedEntity] = Field(default_factory=list)
    relations: List[ExtractedRelation] = Field(default_factory=list)


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMExtractor:
    def __init__(self, config: Optional[LLMConfig] = None) -> None:
        self._config = config or get_config().llm
        self._client = AsyncOpenAI(
            base_url=self._config.base_url,
            api_key=self._config.api_key,
            timeout=httpx.Timeout(120.0, connect=10.0),
            max_retries=self._config.max_retries,
        )
        self._schema_context = build_schema_prompt_context()
        self._system_prompt = EXTRACTION_SYSTEM_PROMPT.format(
            schema_context=self._schema_context
        )
        self._total_usage = TokenUsage()
        self._max_input_chars = 4000

    @property
    def total_usage(self) -> TokenUsage:
        return self._total_usage

    async def _call_llm(self, text: str) -> Tuple[str, TokenUsage]:
        truncated = text if len(text) <= self._max_input_chars else text[:self._max_input_chars] + "\n...(文本过长已截断)"
        if len(text) > self._max_input_chars:
            logger.info(f"Input text truncated from {len(text)} to {self._max_input_chars} chars for LLM")
        start_time = time.time()
        try:
            content, prompt_tokens, completion_tokens = await llm_chat(
                client=self._client,
                model=self._config.model,
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": f"请从以下文本中抽取实体和关系：\n\n{truncated}"},
                ],
                temperature=self._config.temperature,
                max_tokens=self._config.max_tokens,
            )
            elapsed = time.time() - start_time
            logger.debug(f"LLM call completed in {elapsed:.2f}s")
            return content, TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            )
        except Exception as e:
            logger.error(f"LLM API call failed: {e}")
            raise

    def _parse_response(self, content: str) -> ExtractionResult:
        json_str = self._extract_json(content)
        if not json_str:
            logger.warning("No valid JSON found in LLM response")
            return ExtractionResult()
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            return ExtractionResult()

        entities: List[ExtractedEntity] = []
        for item in data.get("entities", []):
            try:
                e = ExtractedEntity(**item)
                e.confidence = 0.7  # LLM extraction default
                e.valid_from = item.get("valid_from", "")
                e.valid_to = item.get("valid_to", "")
                entities.append(e)
            except Exception as ex:
                logger.warning(f"Skipping invalid entity: {ex}")

        relations: List[ExtractedRelation] = []
        for item in data.get("relations", []):
            try:
                r = ExtractedRelation(**item)
                r.confidence = 0.7  # LLM extraction default
                # Pass through temporal fields from LLM output
                r.valid_from = item.get("valid_from", "")
                r.valid_to = item.get("valid_to", "")
                relations.append(r)
            except Exception as e:
                logger.warning(f"Skipping invalid relation: {e}")

        return ExtractionResult(entities=entities, relations=relations)

    @staticmethod
    def _extract_json(text: str) -> Optional[str]:
        # 1. Try markdown code blocks
        for pat in [r"```json\s*(.*?)\s*```", r"```\s*(.*?)\s*```"]:
            match = re.search(pat, text, re.DOTALL)
            if match:
                candidate = match.group(1).strip()
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    pass

        # 2. Find largest JSON object
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = text[start:end + 1]
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass
        return None

    async def extract(self, text: str) -> ExtractionResult:
        if not text.strip():
            return ExtractionResult()
        content, usage = await self._call_llm(text)
        self._total_usage.prompt_tokens += usage.prompt_tokens
        self._total_usage.completion_tokens += usage.completion_tokens
        self._total_usage.total_tokens += usage.total_tokens
        return self._parse_response(content)

    async def extract_batch(self, texts: List[str]) -> List[ExtractionResult]:
        semaphore = asyncio.Semaphore(get_config().extraction.max_concurrent_requests)

        async def _extract_one(t: str) -> ExtractionResult:
            async with semaphore:
                return await self.extract(t)

        tasks = [_extract_one(t) for t in texts]
        return await asyncio.gather(*tasks)

    def disambiguate_entities(self, entities: List[ExtractedEntity]) -> List[ExtractedEntity]:
        seen: Dict[Tuple[str, str], ExtractedEntity] = {}
        for e in entities:
            key = (e.type, e.name)
            if key not in seen:
                seen[key] = e
            else:
                existing = seen[key]
                existing.properties.update({k: v for k, v in e.properties.items() if v})
                existing.confidence = max(existing.confidence, e.confidence)
        return list(seen.values())
