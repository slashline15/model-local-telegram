from __future__ import annotations

from typing import Any

from core.logger import get_logger
from core.reminders import ReminderManager, humanize_delta, parse_when
from tools.registry import ToolRegistry, ToolSpec

log = get_logger(__name__)


def register(registry: ToolRegistry, *, manager: ReminderManager) -> None:
    """Registra schedule_reminder e cancel_reminder ligados ao manager dado."""

    async def _schedule_reminder(
        when_iso: str,
        text: str,
        _ctx: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ctx = _ctx or {}
        user_id = ctx.get("user_id")
        chat_id = ctx.get("chat_id")
        if user_id is None or chat_id is None:
            return {"ok": False, "error": "contexto sem user_id/chat_id"}

        try:
            when = parse_when(when_iso)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        result = await manager.schedule(
            user_id=int(user_id),
            chat_id=int(chat_id),
            text=text.strip(),
            when=when,
            source_interaction_id=ctx.get("interaction_id"),
        )
        return {
            "ok": True,
            "reminder_id": result.reminder_id,
            "scheduled_for": result.scheduled_for.isoformat(timespec="seconds"),
            "in": humanize_delta(result.seconds_from_now),
        }

    async def _cancel_reminder(
        reminder_id: int,
        _ctx: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ctx = _ctx or {}
        user_id = ctx.get("user_id")
        if user_id is None:
            return {"ok": False, "error": "contexto sem user_id"}
        ok = await manager.cancel(int(reminder_id), user_id=int(user_id))
        return {"ok": ok, "reminder_id": int(reminder_id)}

    registry.register(
        ToolSpec(
            name="schedule_reminder",
            description=(
                "Agenda um lembrete para o próprio usuário no Telegram. "
                "Use quando o usuário expressar intenção (mesmo implícita) "
                "de cobrar algo, fazer follow-up, ou lembrar de uma ação "
                "futura. Não pergunte permissão — agende e mencione discretamente."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "when_iso": {
                        "type": "string",
                        "description": (
                            "Data/hora absoluta no formato ISO 8601 "
                            "(YYYY-MM-DDTHH:MM:SS). Use a 'Data/hora atual' do "
                            "system prompt como referência para resolver "
                            "expressões como 'depois do almoço', 'amanhã 14h'."
                        ),
                    },
                    "text": {
                        "type": "string",
                        "description": (
                            "Texto curto do lembrete (≤140 chars), em primeira "
                            "pessoa do usuário. Ex.: 'Cobrar Aquarela sobre "
                            "entrega de tinta para Tita'."
                        ),
                    },
                },
                "required": ["when_iso", "text"],
            },
            handler=_schedule_reminder,
        )
    )

    registry.register(
        ToolSpec(
            name="cancel_reminder",
            description="Cancela um lembrete pendente do próprio usuário pelo id.",
            parameters={
                "type": "object",
                "properties": {
                    "reminder_id": {
                        "type": "integer",
                        "description": "ID retornado por schedule_reminder.",
                    },
                },
                "required": ["reminder_id"],
            },
            handler=_cancel_reminder,
        )
    )
