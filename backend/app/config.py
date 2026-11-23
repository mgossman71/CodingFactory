"""Application configuration, loaded from environment variables only.

Secrets (LLM_API_KEY) are never stored in the database or logged.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- LLM (shared by all agents) ---
    llm_base_url: str = "http://lxc-strata2:8080/v1"
    llm_api_key: str = ""
    llm_model: str = "qwen3.8-flash-next-iq3_s"
    llm_max_concurrent_requests: int = Field(default=1, ge=1, le=8)
    llm_request_timeout: int = Field(default=600, ge=10, le=3600)
    llm_max_retries: int = Field(default=3, ge=0, le=8)
    llm_max_tokens: int = Field(default=0, ge=0, le=128000)  # 0 = server default

    # --- Workflow bounds ---
    max_discovery_rounds: int = Field(default=2, ge=1, le=6)
    max_tasks: int = Field(default=12, ge=1, le=40)
    max_agent_output_repair_attempts: int = Field(default=2, ge=0, le=3)

    # --- Storage ---
    data_dir: str = "./data"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir}/codingfactory.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
