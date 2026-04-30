"""Application configuration loaded from environment variables.

All settings are read from the process environment via ``pydantic-settings``.
A ``.env`` file in the working directory is honored for local development.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Obsidian Headless ---
    obsidian_email: str = ""
    obsidian_password: str = ""
    obsidian_vault_name: str = ""
    obsidian_vault_encryption_password: str = ""
    obsidian_vault_path: Path = Path("/vault")
    obsidian_headless_enabled: bool = True

    # --- API ---
    api_token: str = Field(default="change-me", min_length=1)
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # --- Embeddings ---
    embedding_provider: Literal["openai", "fake"] = "openai"
    openai_api_key: str = ""
    openai_embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    # --- Storage ---
    vector_backend: Literal["qdrant", "memory"] = "qdrant"
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "obsidian_notes"
    sqlite_path: Path = Path("/data/obsidian-rag-api.sqlite")

    # --- Indexing ---
    chunk_target_tokens: int = 750
    chunk_max_tokens: int = 1000
    chunk_min_tokens: int = 100
    watcher_enabled: bool = True

    # --- Logging ---
    log_level: str = "info"

    @field_validator("log_level")
    @classmethod
    def _normalize_log_level(cls, v: str) -> str:
        return v.lower()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""
    return Settings()


def reset_settings_cache() -> None:
    """Clear the cached settings (used in tests)."""
    get_settings.cache_clear()
