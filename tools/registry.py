from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from core.exceptions import ToolExecutionError, ToolNotFoundError
from core.logger import get_logger

log = get_logger(__name__)

ToolHandler = Callable[..., Awaitable[Any]]


@dataclass(slots=True, frozen=True)
class ToolSpec:
    """Especificação serializável compatível com o formato `tools` do Ollama."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler

    def to_ollama(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Registro central de ferramentas; despacha por nome de forma assíncrona."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if not inspect.iscoroutinefunction(spec.handler):
            raise ToolExecutionError(
                f"Handler da tool '{spec.name}' precisa ser async."
            )
        if spec.name in self._tools:
            log.warning("Tool '%s' já registrada — sobrescrevendo.", spec.name)
        self._tools[spec.name] = spec

    def specs_for_ollama(self) -> list[dict[str, Any]]:
        return [t.to_ollama() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools.keys())

    async def dispatch(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        ctx: dict[str, Any] | None = None,
    ) -> Any:
        """Despacha a tool. Se o handler declara `_ctx`, injeta o contexto.

        `ctx` carrega user_id/chat_id/interaction_id quando disponíveis — usado
        por tools que precisam saber em nome de quem agir (ex.: lembretes).
        """
        if name not in self._tools:
            raise ToolNotFoundError(f"Tool '{name}' não registrada.")
        spec = self._tools[name]
        kwargs = dict(arguments)
        sig = inspect.signature(spec.handler)
        if "_ctx" in sig.parameters:
            kwargs["_ctx"] = ctx or {}
        try:
            return await spec.handler(**kwargs)
        except TypeError as exc:
            raise ToolExecutionError(
                f"Argumentos inválidos para '{name}': {exc}"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise ToolExecutionError(f"Falha em '{name}': {exc}") from exc
