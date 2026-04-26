from __future__ import annotations

import asyncio
import base64
import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp
import numpy as np

from core.exceptions import EmbeddingError, OllamaError, OllamaTimeoutError
from core.logger import get_logger

log = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class ChatMessage:
    role: str
    content: str
    images_b64: list[str] | None = None
    # Para role="assistant" ecoando uma chamada de tool antes do `tool` result.
    tool_calls: list[dict[str, Any]] | None = None
    # Para role="tool", identifica qual tool produziu este resultado.
    name: str | None = None

    def to_payload(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.images_b64:
            msg["images"] = self.images_b64
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        if self.name:
            msg["name"] = self.name
        return msg


@dataclass(slots=True, frozen=True)
class ChatResult:
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    prompt_tokens: int | None = None
    response_tokens: int | None = None
    total_duration_ms: int | None = None
    model: str | None = None


@dataclass(slots=True, frozen=True)
class HealthReport:
    ollama_reachable: bool
    models_available: list[str]
    chat_model_present: bool
    embedding_model_present: bool
    embedding_dim_live: int | None
    error: str | None = None


class OllamaClient:
    """Cliente HTTP assíncrono para a API local do Ollama."""

    def __init__(
        self,
        host: str,
        default_model: str,
        embedding_model: str,
        request_timeout_s: int = 300,
    ) -> None:
        self._host: str = host.rstrip("/")
        self._default_model: str = default_model
        self._embedding_model: str = embedding_model
        self._timeout: aiohttp.ClientTimeout = aiohttp.ClientTimeout(total=request_timeout_s)
        # Session, lock e referência ao loop são todos LAZY. Se forem criados em
        # um loop que depois é fechado (ex.: bootstrap em asyncio.run separado),
        # `_get_session` detecta e recria no loop atual.
        self._session: aiohttp.ClientSession | None = None
        self._session_lock: asyncio.Lock | None = None
        self._session_loop: asyncio.AbstractEventLoop | None = None

    @property
    def embedding_model(self) -> str:
        return self._embedding_model

    async def _get_session(self) -> aiohttp.ClientSession:
        current = asyncio.get_running_loop()
        # Loop trocou? Joga fora session+lock antigos (estavam presos ao loop morto).
        if self._session_loop is not current:
            old_session = self._session
            self._session = None
            self._session_lock = None
            self._session_loop = current
            if old_session is not None:
                with contextlib.suppress(Exception):
                    await old_session.close()
        if self._session_lock is None:
            self._session_lock = asyncio.Lock()
        async with self._session_lock:
            if self._session is None or self._session.closed:
                self._session = aiohttp.ClientSession(timeout=self._timeout)
            return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            with contextlib.suppress(Exception):
                await self._session.close()
        self._session = None
        self._session_lock = None
        self._session_loop = None

    async def list_models(self) -> list[str]:
        session = await self._get_session()
        url = f"{self._host}/api/tags"
        try:
            async with session.get(url) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise OllamaError(f"GET /api/tags falhou {resp.status}: {body[:300]}")
                data = await resp.json()
        except asyncio.TimeoutError as exc:
            raise OllamaTimeoutError("Timeout ao listar modelos do Ollama.") from exc
        except aiohttp.ClientError as exc:
            raise OllamaError(f"Erro de rede ao listar modelos: {exc}") from exc

        models = data.get("models", []) if isinstance(data, dict) else []
        names: list[str] = []
        for m in models:
            name = m.get("name") if isinstance(m, dict) else None
            if isinstance(name, str):
                names.append(name)
        return names

    async def chat(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        format_json: bool = False,
    ) -> ChatResult:
        session = await self._get_session()
        url = f"{self._host}/api/chat"

        chosen_model = model or self._default_model
        payload: dict[str, Any] = {
            "model": chosen_model,
            "messages": [m.to_payload() for m in messages],
            "stream": False,
            "options": {"temperature": float(temperature)},
        }
        if tools:
            payload["tools"] = tools
        if format_json:
            payload["format"] = "json"

        try:
            async with session.post(url, json=payload) as resp:
                body_text = await resp.text()
                if resp.status >= 400:
                    raise OllamaError(f"POST /api/chat {resp.status}: {body_text[:500]}")
                data = await resp.json(content_type=None)
        except asyncio.TimeoutError as exc:
            raise OllamaTimeoutError("Timeout no /api/chat.") from exc
        except aiohttp.ClientError as exc:
            raise OllamaError(f"Erro de rede no /api/chat: {exc}") from exc

        msg = data.get("message", {}) if isinstance(data, dict) else {}
        content = str(msg.get("content", "") or "")
        tool_calls = [tc for tc in (msg.get("tool_calls") or []) if isinstance(tc, dict)]

        prompt_tokens = data.get("prompt_eval_count") if isinstance(data, dict) else None
        response_tokens = data.get("eval_count") if isinstance(data, dict) else None
        total_ns = data.get("total_duration") if isinstance(data, dict) else None
        total_ms = int(int(total_ns) / 1_000_000) if isinstance(total_ns, (int, float)) else None

        return ChatResult(
            content=content,
            tool_calls=tool_calls,
            raw=data if isinstance(data, dict) else {},
            prompt_tokens=int(prompt_tokens) if isinstance(prompt_tokens, int) else None,
            response_tokens=int(response_tokens) if isinstance(response_tokens, int) else None,
            total_duration_ms=total_ms,
            model=chosen_model,
        )

    async def embed(self, text: str, model: str | None = None) -> np.ndarray:
        session = await self._get_session()
        url = f"{self._host}/api/embeddings"
        payload = {"model": model or self._embedding_model, "prompt": text}

        try:
            async with session.post(url, json=payload) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise EmbeddingError(
                        f"POST /api/embeddings {resp.status}: {body[:300]}"
                    )
                data = await resp.json(content_type=None)
        except asyncio.TimeoutError as exc:
            raise EmbeddingError("Timeout ao gerar embedding.") from exc
        except aiohttp.ClientError as exc:
            raise EmbeddingError(f"Erro de rede no embedding: {exc}") from exc

        vec = data.get("embedding") if isinstance(data, dict) else None
        if not vec:
            raise EmbeddingError("Resposta de embedding vazia.")
        return np.asarray(vec, dtype=np.float32)

    async def detect_embedding_dim(self, sample: str = "ping") -> int:
        vec = await self.embed(sample)
        return int(vec.shape[-1])

    async def health_check(self, expected_dim: int | None = None) -> HealthReport:
        try:
            models = await self.list_models()
        except Exception as exc:  # noqa: BLE001
            return HealthReport(
                ollama_reachable=False,
                models_available=[],
                chat_model_present=False,
                embedding_model_present=False,
                embedding_dim_live=None,
                error=f"list_models: {exc}",
            )

        chat_present = any(m.startswith(self._default_model.split(":")[0]) for m in models)
        emb_present = any(m.startswith(self._embedding_model.split(":")[0]) for m in models)

        live_dim: int | None = None
        err: str | None = None
        if emb_present:
            try:
                live_dim = await self.detect_embedding_dim()
            except Exception as exc:  # noqa: BLE001
                err = f"embed probe: {exc}"

        if expected_dim is not None and live_dim is not None and live_dim != expected_dim:
            err = (
                (err + " | " if err else "")
                + f"dim mismatch: live={live_dim} expected={expected_dim}"
            )

        return HealthReport(
            ollama_reachable=True,
            models_available=models,
            chat_model_present=chat_present,
            embedding_model_present=emb_present,
            embedding_dim_live=live_dim,
            error=err,
        )

    @staticmethod
    def encode_image_b64(image_path: Path) -> str:
        return base64.b64encode(image_path.read_bytes()).decode("ascii")
