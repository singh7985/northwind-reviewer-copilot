"""Settings and configuration loaded from environment / .env file."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    openai_api_key: str = ""
    openai_extraction_model: str = "gpt-4o-mini"
    openai_adjudication_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_qa_model: str = "gpt-4o-mini"

    # DB
    database_url: str = (
        "postgresql+psycopg://northwind:northwind@localhost:5432/northwind"
    )

    # Server
    backend_port: int = 8000
    cors_origins: str = "http://localhost:3000"

    # Storage / paths
    upload_dir: str = str(REPO_ROOT / "backend" / "uploads")
    policy_dir: str = str(REPO_ROOT / "policies")
    submissions_dir: str = str(REPO_ROOT / "submissions")
    employees_seed: str = str(REPO_ROOT / "data" / "employees.json")

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_api_key.strip())

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    # Managed Postgres providers (Render, Heroku, Supabase) hand out URLs like
    # `postgres://...` or `postgresql://...` without a driver tag. SQLAlchemy
    # then tries to import psycopg2, which we don't ship. Normalise to the
    # psycopg-3 driver we actually depend on.
    url = s.database_url
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    if url != s.database_url:
        s.database_url = url
    return s
