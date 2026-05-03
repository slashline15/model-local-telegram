# core/reminders.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from telegram.ext import Application, ContextTypes

from core.codes import format_hashtag
from core.logger import get_logger

if TYPE_CHECKING:
    from database.sqlite_mgr import SQLiteManager

log = get_logger(__name__)

_JOB_PREFIX: str = "reminder:"


@dataclass(slots=True, frozen=True)
class ScheduleResult:
    reminder_id: int
    scheduled_for: datetime
    seconds_from_now: int


def parse_when(when_iso: str) -> datetime:
    """Aceita ISO com ou sem timezone. Sem tz → interpretado como local."""
    s = when_iso.strip()
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(
            f"when_iso inválido: {when_iso!r} — use formato YYYY-MM-DDTHH:MM:SS"
        ) from exc
    if dt.tzinfo is None:
        dt = dt.astimezone()  # assume local
    return dt


class ReminderManager:
    """Agenda lembretes via PTB JobQueue, persistindo em SQLite.

    Jobs do PTB não são persistidos entre reinícios — por isso `reload_pending`
    re-agenda todos os pendentes lendo o DB no _on_post_init.
    """

    def __init__(self, sqlite: "SQLiteManager") -> None:
        self._sqlite = sqlite
        self._app: Application | None = None

    def bind_app(self, app: Application) -> None:
        self._app = app

    @property
    def _jq(self):
        if self._app is None or self._app.job_queue is None:
            raise RuntimeError(
                "JobQueue indisponível. Instale extras: "
                "pip install 'python-telegram-bot[job-queue]'."
            )
        return self._app.job_queue

    async def schedule(
        self,
        *,
        user_id: int,
        chat_id: int,
        text: str,
        when: datetime,
        source_interaction_id: int | None = None,
    ) -> ScheduleResult:
        if when.tzinfo is None:
            when = when.astimezone()

        rid = await self._sqlite.insert_reminder(
            user_id=user_id,
            chat_id=chat_id,
            text=text,
            scheduled_for=when.isoformat(timespec="seconds"),
            source_interaction_id=source_interaction_id,
        )

        delta = (when - datetime.now(tz=timezone.utc)).total_seconds()
        run_at = max(1.0, delta)  # passado vira ~imediato

        self._jq.run_once(
            self._job_callback,
            when=run_at,
            chat_id=chat_id,
            user_id=user_id,
            data={"reminder_id": rid, "text": text,
                  "source_interaction_id": source_interaction_id},
            name=f"{_JOB_PREFIX}{rid}",
        )
        log.info(
            "Lembrete agendado #%d para %s (%.0fs) — user=%d chat=%d",
            rid, when.isoformat(timespec="seconds"), run_at, user_id, chat_id,
        )
        return ScheduleResult(
            reminder_id=rid,
            scheduled_for=when,
            seconds_from_now=int(delta),
        )

    async def cancel(self, reminder_id: int, *, user_id: int) -> bool:
        ok = await self._sqlite.cancel_reminder(reminder_id, user_id=user_id)
        if not ok:
            return False
        for job in self._jq.get_jobs_by_name(f"{_JOB_PREFIX}{reminder_id}"):
            job.schedule_removal()
        log.info("Lembrete #%d cancelado por user=%d", reminder_id, user_id)
        return True

    async def reload_pending(self) -> int:
        """Re-agenda no JobQueue todos os pendentes do DB. Idempotente."""
        rows = await self._sqlite.list_pending_reminders()
        count = 0
        for r in rows:
            try:
                when = parse_when(r.scheduled_for)
            except ValueError as exc:
                log.warning("Ignorando reminder #%d (data inválida): %s", r.id, exc)
                continue
            delta = (when - datetime.now(tz=timezone.utc)).total_seconds()
            run_at = max(1.0, delta)
            self._jq.run_once(
                self._job_callback,
                when=run_at,
                chat_id=r.chat_id,
                user_id=r.user_id,
                data={"reminder_id": r.id, "text": r.text,
                      "source_interaction_id": r.source_interaction_id},
                name=f"{_JOB_PREFIX}{r.id}",
            )
            count += 1
        if count:
            log.info("Reagendados %d lembrete(s) pendentes do DB.", count)
        return count

    async def _job_callback(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        job = context.job
        if job is None or job.data is None:
            return
        data = job.data if isinstance(job.data, dict) else {}
        rid = int(data.get("reminder_id", 0))
        text = str(data.get("text", ""))
        source = data.get("source_interaction_id")
        chat_id = job.chat_id

        body = f"⏰ Lembrete: {text}"
        if source:
            body += f"\n\n_referência:_ {format_hashtag(int(source))}"
        try:
            await context.bot.send_message(chat_id=chat_id, text=body)
        except Exception as exc:  # noqa: BLE001
            log.exception("Falha ao enviar lembrete #%d: %s", rid, exc)
            return
        try:
            await self._sqlite.mark_reminder_sent(rid)
        except Exception as exc:  # noqa: BLE001
            log.warning("Falha ao marcar reminder #%d sent: %s", rid, exc)


def humanize_delta(seconds: int) -> str:
    """Formata 'em 2h15min', 'em 3 dias', usado pra confirmar agendamento."""
    if seconds < 0:
        return "imediatamente"
    if seconds < 60:
        return f"em {seconds}s"
    if seconds < 3600:
        return f"em {seconds // 60}min"
    if seconds < 86400:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"em {h}h{m:02d}min" if m else f"em {h}h"
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    return f"em {d}d{h}h" if h else f"em {d}d"


__all__ = ["ReminderManager", "ScheduleResult", "parse_when", "humanize_delta"]
