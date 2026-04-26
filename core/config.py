from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurações globais carregadas a partir de variáveis de ambiente / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    telegram_bot_token: str = Field(..., description="Token do bot do Telegram.")

    ollama_host: str = Field(
        default="http://localhost:11434",
        description="URL base da API local do Ollama.",
    )
    ollama_default_model: str = Field(default="gemma:2b")
    ollama_embedding_model: str = Field(default="nomic-embed-text")
    ollama_request_timeout_s: int = Field(default=300)

    openai_api_key: str = Field(
        default="",
        description="Chave usada apenas para o endpoint de transcrição (Whisper).",
    )
    openai_whisper_model: str = Field(default="whisper-1")
    openai_api_base: str = Field(default="https://api.openai.com/v1")

    sqlite_path: Path = Field(default=Path("./data/bot.db"))
    faiss_index_path: Path = Field(default=Path("./data/faiss.index"))
    faiss_id_map_path: Path = Field(default=Path("./data/faiss_id_map.json"))
    media_dir: Path = Field(default=Path("./data/media"))

    embedding_dim: int = Field(default=768)

    rag_top_k: int = Field(default=20)
    rag_max_positive: int = Field(default=3)
    rag_max_negative: int = Field(default=2)
    rag_max_neutral: int = Field(default=3)
    rag_positive_score_threshold: int = Field(default=4)
    rag_negative_score_threshold: int = Field(default=2)
    # Histórico cronológico injetado no prompt (independente do RAG semântico).
    rag_recent_history: int = Field(default=6)

    log_level: str = Field(default="INFO")
    log_file: Path = Field(default=Path("./data/bot.log"))

    def ensure_dirs(self) -> None:
        for path in (
            self.sqlite_path.parent,
            self.faiss_index_path.parent,
            self.media_dir,
            self.log_file.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()  # type: ignore[call-arg]
    settings.ensure_dirs()
    return settings
