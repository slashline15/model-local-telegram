# core/exceptions.py

from __future__ import annotations


class BotError(Exception):
    """Raiz da hierarquia de exceções do projeto."""


class ConfigError(BotError):
    """Falha em configuração ou variáveis de ambiente."""


class OllamaError(BotError):
    """Erro genérico ao falar com a API do Ollama."""


class OllamaTimeoutError(OllamaError):
    """Timeout em chamadas ao Ollama."""


class EmbeddingError(BotError):
    """Falha ao gerar embeddings."""


class TranscriptionError(BotError):
    """Falha ao transcrever áudio (Whisper)."""


class StorageError(BotError):
    """Erro em I/O persistente (SQLite, FAISS, mídia)."""


class ToolNotFoundError(BotError):
    """Ferramenta solicitada por nome não está registrada."""


class ToolExecutionError(BotError):
    """Falha durante execução de uma ferramenta."""
