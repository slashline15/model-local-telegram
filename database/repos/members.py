# database/repos/members.py

from __future__ import annotations

import aiosqlite

from database.models import ProjectMember
from database.repos.base import BaseRepo
from database.schema import now_iso


class MembersRepo(BaseRepo):
    """Membros de uma obra — papel + flags de permissão por projeto."""

    async def add(
        self,
        *,
        project_id: int,
        user_id: int,
        role: str,
        can_approve_rdo: bool = False,
        can_view_financial: bool = False,
        can_invite: bool = False,
        invite_id: int | None = None,
    ) -> ProjectMember:
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                """
                INSERT INTO project_members
                    (project_id, user_id, role, can_approve_rdo,
                     can_view_financial, can_invite, joined_at, invite_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, user_id) DO UPDATE SET
                    role               = excluded.role,
                    can_approve_rdo    = excluded.can_approve_rdo,
                    can_view_financial = excluded.can_view_financial,
                    can_invite         = excluded.can_invite
                """,
                (
                    project_id, user_id, role,
                    int(can_approve_rdo), int(can_view_financial), int(can_invite),
                    now_iso(), invite_id,
                ),
            )
            await conn.commit()
        m = await self.get(project_id=project_id, user_id=user_id)
        assert m is not None
        return m

    async def get(self, *, project_id: int, user_id: int) -> ProjectMember | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM project_members WHERE project_id = ? AND user_id = ?",
                (project_id, user_id),
            ) as cur:
                row = await cur.fetchone()
        return _row_to_member(row) if row else None

    async def list_for_project(self, project_id: int) -> list[ProjectMember]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM project_members WHERE project_id = ? "
                "ORDER BY joined_at ASC",
                (project_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_member(r) for r in rows]

    async def list_for_user(self, user_id: int) -> list[ProjectMember]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM project_members WHERE user_id = ? "
                "ORDER BY joined_at ASC",
                (user_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_member(r) for r in rows]

    async def remove(self, *, project_id: int, user_id: int) -> bool:
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                "DELETE FROM project_members WHERE project_id = ? AND user_id = ?",
                (project_id, user_id),
            )
            await conn.commit()
            return (cur.rowcount or 0) > 0


def _row_to_member(row: aiosqlite.Row) -> ProjectMember:
    return ProjectMember(
        project_id=int(row["project_id"]),
        user_id=int(row["user_id"]),
        role=str(row["role"]),
        can_approve_rdo=bool(row["can_approve_rdo"]),
        can_view_financial=bool(row["can_view_financial"]),
        can_invite=bool(row["can_invite"]),
        joined_at=str(row["joined_at"]),
        invite_id=row["invite_id"],
    )
