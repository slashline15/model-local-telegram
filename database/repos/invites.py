# database/repos/invites.py

from __future__ import annotations

import aiosqlite

from core.exceptions import StorageError
from database.models import Invite
from database.repos.base import BaseRepo
from database.schema import now_iso


class InvitesRepo(BaseRepo):
    """Convites de uso único — token vai no deep link `t.me/SEU_BOT?start=<token>`."""

    async def create(
        self,
        *,
        uid: str,
        token: str,
        role: str,
        created_by: int,
        project_id: int | None = None,
        expires_at: str | None = None,
    ) -> Invite:
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                """
                INSERT INTO invites (uid, token, project_id, role, created_by,
                                     expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (uid, token, project_id, role, created_by,
                 expires_at, now_iso()),
            )
            await conn.commit()
            row_id = cur.lastrowid
        if row_id is None:
            raise StorageError("INSERT em invites não retornou lastrowid.")
        inv = await self.get_by_id(int(row_id))
        if inv is None:
            raise StorageError(f"Invite recém-criado id={row_id} não foi encontrado.")
        return inv

    async def get_by_id(self, invite_id: int) -> Invite | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM invites WHERE id = ?", (invite_id,)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_invite(row) if row else None

    async def get_by_token(self, token: str) -> Invite | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM invites WHERE token = ?", (token,)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_invite(row) if row else None

    async def mark_used(self, invite_id: int, *, used_by: int) -> bool:
        """Marca como usado em UPDATE atômico (rejeita reuso). True se consumiu agora."""
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                "UPDATE invites SET used_by = ?, used_at = ? "
                "WHERE id = ? AND used_at IS NULL",
                (used_by, now_iso(), invite_id),
            )
            await conn.commit()
            return (cur.rowcount or 0) > 0

    async def list_pending(
        self, *, created_by: int | None = None, project_id: int | None = None
    ) -> list[Invite]:
        clauses: list[str] = ["used_at IS NULL"]
        params: list = []
        if created_by is not None:
            clauses.append("created_by = ?")
            params.append(created_by)
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        query = (
            f"SELECT * FROM invites WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at DESC"
        )
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(query, params) as cur:
                rows = await cur.fetchall()
        return [_row_to_invite(r) for r in rows]


def _row_to_invite(row: aiosqlite.Row) -> Invite:
    return Invite(
        id=int(row["id"]),
        uid=str(row["uid"]),
        token=str(row["token"]),
        project_id=row["project_id"],
        role=str(row["role"]),
        created_by=int(row["created_by"]),
        used_by=row["used_by"],
        expires_at=row["expires_at"],
        used_at=row["used_at"],
        created_at=str(row["created_at"]),
    )
