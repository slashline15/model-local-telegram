from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import aiohttp

from core.exceptions import OllamaError, OllamaTimeoutError
from core.logger import get_logger
from llm.ollama_client import ChatMessage, ChatResult

log = get_logger(__name__)


class OpenAIChatClient:
    """Cliente minimal para o endpoint /chat/completions da OpenAI.

    Usado APENAS como fallback de chat. Sem tool_calls e sem visão — em caso
    de fallback, descartamos features avançadas em troca de continuar respondendo.
    Reusa as exceções OllamaError/OllamaTimeoutError pra que o chamador trate
    qualquer falha de chat de forma uniforme.
    """

    def __init__(
        self,
        api_key: str,
        api_base: str = "https://api.openai.com/v1",
        request_timeout_s: int = 60,
    ) -> None:
        self._api_key: str = api_key
        self._api_base: str = api_base.rstrip("/")
        self._timeout: aiohttp.ClientTimeout = aiohttp.ClientTimeout(total=request_timeout_s)
        self._session: aiohttp.ClientSession | None = None
        self._session_loop: asyncio.AbstractEventLoop | None = None

    async def _session_for_loop(self) -> aiohttp.ClientSession:
        current = asyncio.get_running_loop()
        if self._session_loop is not current:
            old = self._session
            self._session = None
            self._session_loop = current
            if old is not None:
                with contextlib.suppress(Exception):
                    await old.close()
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            with contextlib.suppress(Exception):
                await self._session.close()
        self._session = None
        self._session_loop = None

    @staticmethod
    def _to_openai_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            # `tool` e `tool_calls` são deliberadamente ignorados — fallback é
            # text-only. Mensagens vazias de papéis incompatíveis viram `user`.
            role = m.role if m.role in ("system", "user", "assistant") else "user"
            content = m.content or ""
            out.append({"role": role, "content": content})
        return out

    async def chat(
        self,
        messages: list[ChatMessage],
        model: str,
        temperature: float = 0.7,
    ) -> ChatResult:
        session = await self._session_for_loop()
        url = f"{self._api_base}/chat/completions"
        payload: dict[str, Any] = {
            "model": model,
            "messages": self._to_openai_messages(messages),
            "temperature": float(temperature),
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}

        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                body_text = await resp.text()
                if resp.status >= 400:
                    raise OllamaError(
                        f"OpenAI POST /chat/completions {resp.status}: {body_text[:500]}"
                    )
                data = await resp.json(content_type=None)
        except asyncio.TimeoutError as exc:
            raise OllamaTimeoutError("Timeout no /chat/completions da OpenAI.") from exc
        except aiohttp.ClientError as exc:
            raise OllamaError(f"Erro de rede no /chat/completions: {exc}") from exc

        choices = data.get("choices") or []
        first = choices[0] if choices else {}
        msg = first.get("message", {}) if isinstance(first, dict) else {}
        content = str(msg.get("content", "") or "")

        usage = data.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")

        return ChatResult(
            content=content,
            tool_calls=[],
            raw=data if isinstance(data, dict) else {},
            prompt_tokens=int(prompt_tokens) if isinstance(prompt_tokens, int) else None,
            response_tokens=int(completion_tokens) if isinstance(completion_tokens, int) else None,
            total_duration_ms=None,
            model=model,
        )
