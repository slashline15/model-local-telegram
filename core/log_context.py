from __future__ import annotations

from contextvars import ContextVar

current_run_id: ContextVar[str] = ContextVar("current_run_id", default="--------")


def set_run_id(run_id: str) -> None:
    """Define o run_id corrente da Task asyncio.

    ContextVars são copiadas por Task, então cada update do Telegram fica
    isolado automaticamente — não há vazamento entre handlers concorrentes.
    """
    current_run_id.set(run_id[:8])


def get_run_id() -> str:
    return current_run_id.get()
