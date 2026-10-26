from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = ""
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
