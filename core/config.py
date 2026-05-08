# core/config.py

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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

    # Fallback chain quando o modelo principal falha (ex.: cloud sem quota, 500).
    # CSV no .env: CHAT_FALLBACK_MODELS=translategemma:4b,gemma:2b
    chat_fallback_models: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="Modelos Ollama tentados em sequência se o principal falhar.",
    )
    # Último recurso: usa a chave OpenAI (mesma do Whisper) com este modelo.
    # Vazio = OpenAI fica desligado como fallback de chat.
    openai_chat_fallback_model: str = Field(
        default="",
        description="Ex.: 'gpt-4o-mini'. Vazio desliga o fallback OpenAI.",
    )

    @field_validator("chat_fallback_models", mode="before")
    @classmethod
    def _split_csv(cls, v):  # type: ignore[no-untyped-def]
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    openai_api_key: str = Field(
        default="",
        description="Chave usada apenas para o endpoint de transcrição (Whisper).",
    )
    openai_whisper_model: str = Field(default="whisper-1")
    openai_api_base: str = Field(default="https://api.openai.com/v1")
    openai_whisper_timeout_s: int = Field(
        default=600,
        description="Timeout HTTP do Whisper. Áudios longos podem demorar.",
    )

    # Telegram bot API standard limita downloads via getFile a 20 MB.
    # Whisper aceita áudios até 25 MB por requisição.
    telegram_download_max_mb: int = Field(default=20)
    whisper_max_mb: int = Field(default=25)

    # Timeouts do cliente HTTP do PTB (segundos). Defaults do PTB são 5s, o
    # que estoura em downloads de arquivos grandes (áudios, PDFs).
    telegram_read_timeout_s: float = Field(default=60.0)
    telegram_write_timeout_s: float = Field(default=60.0)
    telegram_connect_timeout_s: float = Field(default=30.0)
    telegram_pool_timeout_s: float = Field(default=30.0)
    telegram_media_write_timeout_s: float = Field(default=600.0)
    telegram_get_updates_read_timeout_s: float = Field(default=30.0)

    sqlite_path: Path = Field(default=Path("./data/bot.db"))
    sqlite_backup_dir: Path = Field(default=Path("./data/backups"))
    sqlite_backup_max_keep: int = Field(
        default=10,
        description="Quantos backups rotativos do bot.db manter em sqlite_backup_dir.",
    )
    sqlite_backup_enabled: bool = Field(
        default=True,
        description="Se True, faz backup automático no startup antes de init_schema.",
    )
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

    # Chunking de documentos grandes.
    chunk_size: int = Field(default=2500)
    chunk_overlap: int = Field(default=300)

    log_level: str = Field(default="INFO")
    log_file: Path = Field(default=Path("./data/bot.log"))

    # Telegram_id do superadmin do bot. No primeiro /start, este telegram_id
    # é registrado/promovido a `users.role='superadmin'`. Vazio ⇒ sem bootstrap
    # automático (admins novos só entram via convite do superadmin existente).
    bootstrap_superadmin_telegram_id: int = Field(
        default=0,
        description="Telegram ID que vira superadmin no primeiro /start.",
    )

    # --- Debug Bot ---
    telegram_debug_bot_token: str = Field(default="")
    debug_mode: bool = Field(default=False)
    debug_notify_min_cost_usd: float = Field(default=0.001)
    debug_notify_sample_rate: float = Field(default=0.05)
    debug_notify_on_error: bool = Field(default=True)
    debug_notify_on_latency_ms: int = Field(default=10000)

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
