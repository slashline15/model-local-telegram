# database/repos/settings.py

from __future__ import annotations

import aiosqlite

from database.models import UserSettings
from database.repos.base import BaseRepo
from database.schema import now_iso


class SettingsRepo(BaseRepo):
    """Configurações por usuário (modelo padrão, temperatura)."""

    __slots__ = ("_default_model", "_default_temperature")

    def __init__(
        self,
        db_path,
        default_model: str = "gemma:2b",
        default_temperature: float = 0.7,
    ) -> None:
        super().__init__(db_path)
        self._default_model: str = default_model
        self._default_temperature: float = default_temperature

    async def get(self, user_id: int) -> UserSettings:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT user_id, current_model, temperature, current_project_id, "
                "       created_at, updated_at "
                "FROM user_settings WHERE user_id = ?",
                (user_id,),
            ) as cur:
                row = await cur.fetchone()

        if row is None:
            now = now_iso()
            settings = UserSettings(
                user_id=user_id,
                current_model=self._default_model,
                temperature=self._default_temperature,
                current_project_id=None,
                created_at=now,
                updated_at=now,
            )
            await self._upsert(settings)
            return settings

        return UserSettings(
            user_id=int(row["user_id"]),
            current_model=str(row["current_model"]),
            temperature=float(row["temperature"]),
            current_project_id=row["current_project_id"],
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )

    async def set_model(self, user_id: int, model: str) -> None:
        cur = await self.get(user_id)
        await self._upsert(
            UserSettings(
                user_id=user_id,
                current_model=model,
                temperature=cur.temperature,
                current_project_id=cur.current_project_id,
                created_at=cur.created_at or now_iso(),
                updated_at=now_iso(),
            )
        )

    async def set_temperature(self, user_id: int, temperature: float) -> None:
        cur = await self.get(user_id)
        await self._upsert(
            UserSettings(
                user_id=user_id,
                current_model=cur.current_model,
                temperature=float(temperature),
                current_project_id=cur.current_project_id,
                created_at=cur.created_at or now_iso(),
                updated_at=now_iso(),
            )
        )

    async def set_current_project(self, user_id: int, project_id: int | None) -> None:
        """Define a obra ativa do usuário (None = sem obra ativa)."""
        cur = await self.get(user_id)
        await self._upsert(
            UserSettings(
                user_id=user_id,
                current_model=cur.current_model,
                temperature=cur.temperature,
                current_project_id=project_id,
                created_at=cur.created_at or now_iso(),
                updated_at=now_iso(),
            )
        )

    async def reset(self, user_id: int) -> UserSettings:
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("DELETE FROM user_settings WHERE user_id = ?", (user_id,))
            await conn.commit()
        return await self.get(user_id)

    async def _upsert(self, s: UserSettings) -> None:
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                """
                INSERT INTO user_settings
                    (user_id, current_model, temperature, current_project_id,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    current_model      = excluded.current_model,
                    temperature        = excluded.temperature,
                    current_project_id = excluded.current_project_id,
                    updated_at         = excluded.updated_at
                """,
                (s.user_id, s.current_model, s.temperature, s.current_project_id,
                 s.created_at, s.updated_at),
            )
            await conn.commit()
