from __future__ import annotations

import json
from dataclasses import dataclass

from core.logger import get_logger
from llm.ollama_client import ChatMessage, OllamaClient

log = get_logger(__name__)

ALLOWED_INTENTS: tuple[str, ...] = (
    "question",          # pergunta factual / conceitual
    "code_help",         # pedir/depurar código
    "chitchat",          # conversa casual
    "summarize",         # resumir conteúdo
    "translate",         # tradução
    "image_analysis",    # descrever/analisar imagem
    "voice_transcribed", # mensagem que veio de transcrição
    "tool_use",          # claramente requer uma ferramenta (busca, cálculo, etc.)
    "other",             # fallback
)

_INTENT_SYSTEM: str = (
    "Você é um classificador de intenção. "
    "Receberá a mensagem do usuário e deve responder EXCLUSIVAMENTE com JSON no "
    "formato {\"intent\": \"<um_dos_valores>\", \"confidence\": <float 0..1>, "
    "\"reason\": \"<curto>\"}. Os valores permitidos para 'intent' são: "
    + ", ".join(ALLOWED_INTENTS)
    + ". Não invente valores. Se houver dúvida real, use 'other'."
)


@dataclass(slots=True, frozen=True)
class IntentResult:
    intent: str
    confidence: float
    reason: str


class IntentClassifier:
    """Classifica a intenção em UM rótulo de um conjunto fechado."""

    def __init__(
        self,
        ollama: OllamaClient,
        classifier_model: str | None = None,
    ) -> None:
        self._ollama: OllamaClient = ollama
        self._model: str | None = classifier_model

    async def classify(
        self, user_message: str, hint: str | None = None
    ) -> IntentResult:
        if not user_message.strip():
            return IntentResult(intent="chitchat", confidence=0.0, reason="empty input")

        prompt = user_message[:2000]
        if hint:
            prompt = f"[contexto: {hint}]\n{prompt}"

        messages = [
            ChatMessage(role="system", content=_INTENT_SYSTEM),
            ChatMessage(role="user", content=prompt),
        ]

        try:
            result = await self._ollama.chat(
                messages=messages,
                model=self._model,
                temperature=0.0,
                format_json=True,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("IntentClassifier falhou (%s) — fallback 'other'", exc)
            return IntentResult(intent="other", confidence=0.0, reason=str(exc))

        return self._parse(result.content)

    def _parse(self, raw: str) -> IntentResult:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("IntentClassifier devolveu não-JSON: %r", raw[:200])
            return IntentResult(intent="other", confidence=0.0, reason="parse error")

        intent = str(obj.get("intent", "")).strip().lower()
        if intent not in ALLOWED_INTENTS:
            log.info("Intent inválida %r → 'other'", intent)
            intent = "other"

        try:
            conf = max(0.0, min(1.0, float(obj.get("confidence", 0.0))))
        except (TypeError, ValueError):
            conf = 0.0

        reason = str(obj.get("reason", ""))[:200]
        return IntentResult(intent=intent, confidence=conf, reason=reason)
