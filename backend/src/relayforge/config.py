from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", extra="ignore", hide_input_in_errors=True
    )
    model_provider: Literal["mock", "remote_json"] = "mock"
    model_api_key: SecretStr = SecretStr("")
    model_base_url: str | None = None
    model_name: str | None = None
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://relayforge:unconfigured@127.0.0.1:5432/relayforge"
    )
    api_host: Literal["127.0.0.1"] = "127.0.0.1"
    api_port: int = 8000
    workspace_root: Path = ROOT / ".local" / "workspaces"
    mock_external_path: Path = ROOT / ".local" / "demo-data" / "external.sqlite3"
    mcp_token_path: Path = ROOT / ".local" / "mcp-api.token"
    langgraph_strict_msgpack: bool = True

    @field_validator("database_url")
    @classmethod
    def postgres_only(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must use the PostgreSQL psycopg driver")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
