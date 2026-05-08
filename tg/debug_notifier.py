# tg/debug_notifier.py

"""
DebugNotifier — envia notificações de pipeline para um bot Telegram separado.

Usa aiohttp diretamente (sem PTB) apenas para POST sendMessage.
Não faz polling; só envia.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

from core.logger import get_logger

if TYPE_CHECKING:
    import aiohttp

log = get_logger(__name__)


class DebugNotifier:
    """Envia notificações de debug para um chat Telegram via bot dedicado."""

    def __init__(
        self,
        token: str,
        chat_id: int | str,
        *,
        min_cost_usd: float = 0.001,
        sample_rate: float = 0.05,
        on_error: bool = True,
        on_latency_ms: int = 10000,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._min_cost_usd = min_cost_usd
        self._sample_rate = sample_rate
        self._on_error = on_error
        self._on_latency_ms = on_latency_ms
        self._session: "aiohttp.ClientSession | None" = None

    async def _get_session(self) -> "aiohttp.ClientSession":
        if self._session is None or self._session.closed:
            import aiohttp
            self._session = aiohttp.ClientSession()
        return self._session

    def _should_notify(
        self,
        cost_usd: float,
        duration_ms: int,
        has_error: bool,
    ) -> bool:
        """Retorna True se algum filtro disparar."""
        if cost_usd >= self._min_cost_usd:
            return True
        if random.random() < self._sample_rate:  # noqa: S311
            return True
        if self._on_error and has_error:
            return True
        if duration_ms >= self._on_latency_ms:
            return True
        return False

    async def notify_pipeline_run(
        self,
        *,
        run_id: str,
        user_name: str,
        project_name: str | None,
        model: str,
        backend: str,
        intent: str | None,
        tags: list[str],
        prompt_tokens: int,
        response_tokens: int,
        cost_usd: float,
        duration_ms: int,
    ) -> None:
        """Formata e envia notificação de pipeline se filtros passarem."""
        if not self._should_notify(cost_usd, duration_ms, has_error=False):
            return
        short_id = run_id[:8] if run_id else "?"
        tags_str = ",".join(tags[:5]) if tags else "—"
        total_tok = prompt_tokens + response_tokens
        cost_str = f"${cost_usd:.4f}" if cost_usd > 0 else "$0.00"
        duration_s = duration_ms / 1000.0
        proj_part = f" · 🏗 {project_name}" if project_name else ""
        text = (
            f"📊 Pipeline · {short_id}\n"
            f"👤 {user_name}{proj_part}\n"
            f"🤖 {model} ({backend})\n"
            f"💬 intent={intent or '?'} · tags={tags_str}\n"
            f"📥 {prompt_tokens} in + {response_tokens} out = {total_tok} tok · {cost_str}\n"
            f"⏱ {duration_s:.1f}s total"
        )
        await self._send(text)

    async def notify_error(
        self,
        *,
        run_id: str,
        user_name: str,
        project_name: str | None,
        error: str,
        duration_ms: int,
    ) -> None:
        """Sempre envia se on_error=True."""
        if not self._on_error:
            return
        short_id = run_id[:8] if run_id else "?"
        proj_part = f" · 🏗 {project_name}" if project_name else ""
        duration_s = duration_ms / 1000.0
        text = (
            f"🔴 Pipeline ERRO · {short_id}\n"
            f"👤 {user_name}{proj_part}\n"
            f"❌ {error}\n"
            f"⏱ {duration_s:.1f}s"
        )
        await self._send(text)

    async def _send(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        try:
            session = await self._get_session()
            async with session.post(url, json=payload, timeout=10) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.warning("DebugNotifier: falha HTTP %d — %s", resp.status, body[:200])
        except Exception as exc:  # noqa: BLE001
            log.warning("DebugNotifier: erro ao enviar: %s", exc)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
