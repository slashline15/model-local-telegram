# database/repos/users.py

from __future__ import annotations

import aiosqlite

from core.exceptions import StorageError
from database.models import User
from database.repos.base import BaseRepo
from database.schema import now_iso


class UsersRepo(BaseRepo):
    """Cadastro de usuários do bot (telegram_id ↔ id interno)."""

    async def register(
        self,
        *,
        telegram_id: int,
        name: str,
        email: str | None = None,
        role: str = "worker",
        invited_by: int | None = None,
    ) -> User:
        now = now_iso()
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                """
                INSERT INTO users (telegram_id, name, email, role, status, invited_by,
                                   created_at, updated_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    name       = excluded.name,
                    updated_at = excluded.updated_at
                """,
                (telegram_id, name, email, role, invited_by, now, now),
            )
            await conn.commit()
        user = await self.get_by_telegram_id(telegram_id)
        if user is None:
            raise StorageError(f"Falha ao registrar usuário telegram_id={telegram_id}")
        return user

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_user(row) if row else None

    async def get_by_id(self, user_id: int) -> User | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_user(row) if row else None

    async def update_role(self, user_id: int, role: str) -> None:
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "UPDATE users SET role = ?, updated_at = ? WHERE id = ?",
                (role, now_iso(), user_id),
            )
            await conn.commit()

    async def update_status(self, user_id: int, status: str) -> None:
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "UPDATE users SET status = ?, updated_at = ? WHERE id = ?",
                (status, now_iso(), user_id),
            )
            await conn.commit()

    async def list(
        self, *, role: str | None = None, status: str = "active", limit: int = 100
    ) -> list[User]:
        if role:
            query = "SELECT * FROM users WHERE status = ? AND role = ? LIMIT ?"
            params: tuple = (status, role, limit)
        else:
            query = "SELECT * FROM users WHERE status = ? LIMIT ?"
            params = (status, limit)
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(query, params) as cur:
                rows = await cur.fetchall()
        return [_row_to_user(r) for r in rows]


def _row_to_user(row: aiosqlite.Row) -> User:
    return User(
        id=int(row["id"]),
        telegram_id=int(row["telegram_id"]),
        name=str(row["name"]),
        email=row["email"],
        role=str(row["role"]),
        status=str(row["status"]),
        invited_by=row["invited_by"],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )
