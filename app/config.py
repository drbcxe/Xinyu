from functools import lru_cache
from pathlib import Path

from pydantic import Field, PositiveInt, SecretStr, model_validator
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
    model_timeout_seconds: int = Field(default=60, ge=1, le=300)
    model_max_tokens: int = Field(default=4096, ge=256, le=32768)
    agent_timeout_seconds: int = Field(default=300, ge=10, le=900)
    qdrant_url: str = "http://127.0.0.1:6333"
    rag_embedding_model: str = "BAAI/bge-small-zh-v1.5"
    rag_model_cache: str = "D:/DevTools/embedding-models"
    rag_rerank_enabled: bool = True
    rag_dense_enabled: bool = True
    rag_collection_override: str = ""
    rag_cache_bytes: PositiveInt = 64 * 1024 * 1024
    rag_cache_entries: int = Field(default=8, ge=1, le=64)
    rag_cache_ttl_seconds: int = Field(default=120, ge=10, le=600)
    owner_vector_points: PositiveInt = 50000
    global_vector_points: PositiveInt = 250000
    guest_daily_requests: PositiveInt = 8
    account_daily_requests: PositiveInt = 100
    guest_daily_tokens: PositiveInt = 120000
    account_daily_tokens: PositiveInt = 1500000
    global_daily_tokens: PositiveInt = 10000000
    global_daily_requests: PositiveInt = 2000
    ip_daily_requests: PositiveInt = 300
    global_model_concurrency: int = Field(default=4, ge=1, le=32)
    global_queue_limit: PositiveInt = 100
    owner_pending_limit: PositiveInt = 2
    owner_storage_bytes: PositiveInt = 20 * 1024 * 1024
    owner_item_limit: PositiveInt = 500
    global_storage_bytes: PositiveInt = 512 * 1024 * 1024
    global_item_limit: PositiveInt = 20000
    graph_thread_storage_bytes: PositiveInt = 4 * 1024 * 1024
    agent_result_bytes: PositiveInt = 1024 * 1024
    job_storage_reserve_bytes: PositiveInt = 6 * 1024 * 1024
    global_profile_limit: PositiveInt = 10000
    storage_emergency_bytes: PositiveInt = 65536
    model_call_input_limit: PositiveInt = 60000
    model_budget_per_request: PositiveInt = 160000
    model_calls_per_request: PositiveInt = 24
    model_image_token_reserve: PositiveInt = 16000
    worker_advice_concurrency: int = Field(default=2, ge=1, le=16)
    worker_lease_seconds: int = Field(default=90, ge=15, le=600)
    worker_max_attempts: int = Field(default=3, ge=1, le=10)
    worker_retry_base_seconds: int = Field(default=5, ge=1, le=300)
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_security: str = "starttls"
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = ""
    smtp_timeout_seconds: int = Field(default=10, ge=1, le=30)
    ops_token: SecretStr = SecretStr("")
    database_connect_timeout: int = Field(default=5, ge=1, le=30)
    database_statement_timeout_ms: int = Field(default=30000, ge=1000, le=120000)
    backup_key_file: str = ""
    log_file_bytes: PositiveInt = 4 * 1024 * 1024
    log_backup_count: int = Field(default=2, ge=1, le=10)
    log_total_bytes: PositiveInt = 128 * 1024 * 1024
    log_retention_days: int = Field(default=7, ge=1, le=90)
    backup_total_bytes: PositiveInt = 8 * 1024**3
    backup_max_count: int = Field(default=128, ge=3, le=1000)
    backup_retention_days: int = Field(default=7, ge=1, le=365)
    backup_keep_count: int = Field(default=3, ge=1, le=100)
    restore_temp_bytes: PositiveInt = 4 * 1024**3
    runtime_free_bytes: PositiveInt = 2 * 1024**3

    @model_validator(mode="after")
    def storage_reserve_covers_execution(self):
        if self.backup_keep_count > self.backup_max_count:
            raise ValueError("BACKUP_KEEP_COUNT must not exceed BACKUP_MAX_COUNT")
        if (
            self.job_storage_reserve_bytes
            < self.graph_thread_storage_bytes + self.agent_result_bytes + 131072
        ):
            raise ValueError(
                "JOB_STORAGE_RESERVE_BYTES must cover graph, result and input/metadata headroom"
            )
        return self

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
