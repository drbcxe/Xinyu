from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = ""
    redis_url: str = "redis://127.0.0.1:6379/1"
    auth_allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8000,http://127.0.0.1:8000"
    model_base_url: str = ""
    model_name: str = ""
    relationship_vision_model: str = ""
    relationship_vision_base_url: str = ""
    relationship_vision_api_key: SecretStr = SecretStr("")
    model_api_key: SecretStr = SecretStr("")
    model_timeout_seconds: int = 60
    model_max_tokens: int = 4096
    agent_timeout_seconds: int = 300

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
