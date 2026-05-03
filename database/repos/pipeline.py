# database/repos/pipeline.py

from __future__ import annotations

import json

import aiosqlite

from database.models import PipelineStepRow
from database.repos.base import BaseRepo
from database.schema import now_iso


class PipelineRepo(BaseRepo):
    """Auditoria dos passos de um run de pipeline."""

    async def save_steps(
        self,
        *,
        run_id: str,
        user_id: int,
        chat_id: int | None,
        interaction_id: int | None,
        steps: list[PipelineStepRow],
    ) -> None:
        if not steps:
            return
        ts = now_iso()
        rows = [
            (
                run_id, interaction_id, user_id, chat_id,
                s.step_index, s.step_name, s.status, int(s.duration_ms),
                json.dumps(s.details, ensure_ascii=False, default=str),
                s.error, ts,
            )
            for s in steps
        ]
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.executemany(
                """
                INSERT INTO pipeline_steps
                    (run_id, interaction_id, user_id, chat_id, step_index,
                     step_name, status, duration_ms, details, error, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            await conn.commit()
