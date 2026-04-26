from __future__ import annotations

import json
import re

from core.logger import get_logger
from llm.ollama_client import ChatMessage, OllamaClient
from llm.prompt_templates import TAG_GENERATOR_SYSTEM

log = get_logger(__name__)

_TAG_RE: re.Pattern[str] = re.compile(r"^[a-z0-9_]{2,32}$")


class TagGenerator:
    """Pequeno classificador via LLM que devolve 1..3 tags em snake_case."""

    def __init__(
        self,
        ollama: OllamaClient,
        classifier_model: str | None = None,
        max_tags: int = 3,
    ) -> None:
        self._ollama: OllamaClient = ollama
        self._classifier_model: str | None = classifier_model
        self._max_tags: int = max_tags

    async def generate(self, user_message: str) -> list[str]:
        if not user_message.strip():
            return ["chat"]

        messages = [
            ChatMessage(role="system", content=TAG_GENERATOR_SYSTEM),
            ChatMessage(role="user", content=user_message[:2000]),
        ]
        try:
            result = await self._ollama.chat(
                messages=messages,
                model=self._classifier_model,
                temperature=0.0,
                format_json=True,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Falha ao gerar tags, usando fallback. err=%s", exc)
            return ["chat"]

        return self._parse(result.content)

    def _parse(self, raw: str) -> list[str]:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("TagGenerator devolveu não-JSON: %r", raw[:200])
            return ["chat"]

        tags = obj.get("tags") if isinstance(obj, dict) else None
        if not isinstance(tags, list):
            return ["chat"]

        cleaned: list[str] = []
        for t in tags:
            if not isinstance(t, str):
                continue
            normalized = t.strip().lower().replace("-", "_").replace(" ", "_")
            if _TAG_RE.match(normalized):
                cleaned.append(normalized)
            if len(cleaned) >= self._max_tags:
                break

        return cleaned or ["chat"]
