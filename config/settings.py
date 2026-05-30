from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Maps env vars to (section, field) for YAML override
_ENV_OVERRIDE_MAP: dict[str, tuple[str, str]] = {
    "NEO4J_URI": ("neo4j", "uri"),
    "NEO4J_USERNAME": ("neo4j", "username"),
    "NEO4J_PASSWORD": ("neo4j", "password"),
    "NEO4J_DATABASE": ("neo4j", "database"),
    "LLM_BASE_URL": ("llm", "base_url"),
    "LLM_API_KEY": ("llm", "api_key"),
    "LLM_MODEL": ("llm", "model"),
    "LLM_TEMPERATURE": ("llm", "temperature"),
    "LLM_MAX_TOKENS": ("llm", "max_tokens"),
    "LOG_LEVEL": ("logging", "level"),
    "EMBEDDING_MODEL_PATH": ("embedding", "model_path"),
    "HF_HOME": ("embedding", "hf_home"),
    "HF_HUB_OFFLINE": ("embedding", "hf_hub_offline"),
    "ODA_CONVERTER_PATH": ("paths", "oda_converter_path"),
    "KG_FONT_PATH": ("paths", "font_path"),
    "KG_AUTH_MODE": ("auth", "mode"),
    "KG_API_KEY": ("auth", "api_key"),
    "KG_ADMIN_KEY": ("auth", "admin_key"),
    "KG_CORS_ORIGINS": ("app", "cors_origins"),
    "KG_AUDIT_LOG": ("app", "audit_log_path"),
}


class Neo4jConfig(BaseModel):
    uri: str = "bolt://localhost:7687"
    username: str = "neo4j"
    password: str = "changeme"
    database: str = "neo4j"
    max_connection_pool_size: int = 50
    connection_timeout: int = 30


class LLMConfig(BaseModel):
    base_url: str = "https://api.openai.com/v1"
    api_key: str = "your_api_key_here"
    model: str = "gpt-4o"
    temperature: float = 0.1
    max_tokens: int = 4096
    batch_size: int = 10
    max_retries: int = 3
    retry_delay: float = 2.0


class ExtractionConfig(BaseModel):
    entity_similarity_threshold: float = 0.85
    max_concurrent_requests: int = 5
    chunk_size: int = 2000
    chunk_overlap: int = 200


class LoggingConfig(BaseModel):
    level: str = "INFO"
    log_format: str = "{time:YYYY-MM-DD HH:mm:ss} | {level} | {module}:{function}:{line} | {message}"
    rotation: str = "100 MB"
    retention: str = "30 days"


class EmbeddingConfig(BaseModel):
    model_path: str = "models/bge-m3"
    hf_home: str = ""
    hf_hub_offline: bool = False


class PathsConfig(BaseModel):
    oda_converter_path: str = ""
    font_path: str = ""


class AuthConfig(BaseModel):
    mode: str = "none"
    api_key: str = ""
    admin_key: str = ""


class RuntimeConfig(BaseModel):
    cors_origins: str = "http://localhost:3000,http://localhost:8000"
    audit_log_path: str = "logs/audit.log"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


class AppConfig(BaseSettings):
    neo4j: Neo4jConfig = Field(default_factory=Neo4jConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    app: RuntimeConfig = Field(default_factory=RuntimeConfig)

    model_config = {
        "env_file": str(_PROJECT_ROOT / "config" / ".env"),
        "extra": "ignore",
    }

    @classmethod
    def from_yaml(cls, yaml_path: Optional[str] = None) -> "AppConfig":
        if yaml_path is None:
            yaml_path = str(_PROJECT_ROOT / "config" / "default.yaml")

        yaml_file = Path(yaml_path)
        data: dict[str, Any] = {}
        if yaml_file.exists():
            with open(yaml_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

        # Load .env file values (pydantic doesn't apply them when we pass explicit kwargs)
        env_file = _PROJECT_ROOT / "config" / ".env"
        env_values: dict[str, str] = {}
        if env_file.exists():
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    env_values[key.strip()] = value.strip()

        # Override YAML values: system env vars first, then .env file
        for env_var, (section, field) in _ENV_OVERRIDE_MAP.items():
            env_value = os.environ.get(env_var) or env_values.get(env_var)
            if env_value is not None:
                data.setdefault(section, {})[field] = env_value

        return cls(
            neo4j=Neo4jConfig(**data.get("neo4j", {})),
            llm=LLMConfig(**data.get("llm", {})),
            extraction=ExtractionConfig(**data.get("extraction", {})),
            logging=LoggingConfig(**data.get("logging", {})),
            embedding=EmbeddingConfig(**data.get("embedding", {})),
            paths=PathsConfig(**data.get("paths", {})),
            auth=AuthConfig(**data.get("auth", {})),
            app=RuntimeConfig(**data.get("app", {})),
        )


@functools.lru_cache(maxsize=1)
def get_config() -> AppConfig:
    return AppConfig.from_yaml()
