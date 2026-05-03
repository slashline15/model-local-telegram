# database/repos/reminders.py

from __future__ import annotations

import aiosqlite

from core.exceptions import StorageError
from database.models import Reminder
from database.repos.base import BaseRepo
from database.schema import now_iso


class RemindersRepo(BaseRepo):
    """Lembretes datados — agendamento, cancelamento, listagem."""

    async def insert(
        self,
        *,
        user_id: int,
        chat_id: int,
        text: str,
        scheduled_for: str,
        source_interaction_id: int | None = None,
    ) -> int:
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                """
                INSERT INTO reminders
                    (user_id, chat_id, text, scheduled_for, status,
                     source_interaction_id, created_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (user_id, chat_id, text, scheduled_for,
                 source_interaction_id, now_iso()),
            )
            await conn.commit()
            if cur.lastrowid is None:
                raise StorageError("INSERT em reminders não retornou lastrowid.")
            return int(cur.lastrowid)

    async def mark_sent(self, reminder_id: int) -> None:
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "UPDATE reminders SET status='sent', sent_at=? WHERE id=?",
                (now_iso(), reminder_id),
            )
            await conn.commit()

    async def cancel(self, reminder_id: int, *, user_id: int) -> bool:
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                "UPDATE reminders SET status='cancelled' "
                "WHERE id=? AND user_id=? AND status='pending'",
                (reminder_id, user_id),
            )
            await conn.commit()
            return (cur.rowcount or 0) > 0

    async def list_pending(self) -> list[Reminder]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM reminders WHERE status='pending' "
                "ORDER BY scheduled_for ASC"
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_reminder(r) for r in rows]

    async def list_for_user(
        self, user_id: int, *, only_pending: bool = True, limit: int = 20
    ) -> list[Reminder]:
        clause = "WHERE user_id=?" + (" AND status='pending'" if only_pending else "")
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM reminders {clause} "
                f"ORDER BY scheduled_for ASC LIMIT ?",
                (user_id, int(limit)),
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_reminder(r) for r in rows]


def _row_to_reminder(row: aiosqlite.Row) -> Reminder:
    return Reminder(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        chat_id=int(row["chat_id"]),
        text=str(row["text"]),
        scheduled_for=str(row["scheduled_for"]),
        status=str(row["status"]),
        source_interaction_id=row["source_interaction_id"],
        created_at=str(row["created_at"]),
        sent_at=row["sent_at"],
    )
