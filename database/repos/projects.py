# database/repos/projects.py

from __future__ import annotations

import aiosqlite

from core.exceptions import StorageError
from database.models import Project
from database.repos.base import BaseRepo
from database.schema import now_iso


class ProjectsRepo(BaseRepo):
    """Obras — escopo de isolamento de dados (RDOs, atividades, efetivo)."""

    async def create(
        self,
        *,
        uid: str,
        name: str,
        created_by: int,
        admin_id: int | None = None,
        address: str | None = None,
        type: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> Project:
        """Cria a obra. Se `admin_id` não informado, o criador vira admin."""
        admin = admin_id if admin_id is not None else created_by
        ts = now_iso()
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                """
                INSERT INTO projects (uid, name, address, type, status,
                                      start_date, end_date, created_by, admin_id,
                                      created_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
                """,
                (uid, name, address, type, start_date, end_date,
                 created_by, admin, ts),
            )
            row_id = cur.lastrowid
            if row_id is None:
                raise StorageError("INSERT em projects não retornou lastrowid.")
            # Auto-adiciona o admin como membro com permissões totais.
            # Mesma transação garante que obra sem membro admin é impossível.
            await conn.execute(
                """
                INSERT INTO project_members
                    (project_id, user_id, role, can_approve_rdo,
                     can_view_financial, can_invite, joined_at, invite_id)
                VALUES (?, ?, 'admin', 1, 1, 1, ?, NULL)
                """,
                (int(row_id), admin, ts),
            )
            await conn.commit()
        proj = await self.get_by_id(int(row_id))
        if proj is None:
            raise StorageError(f"Projeto recém-criado id={row_id} não foi encontrado.")
        return proj

    async def set_admin(self, project_id: int, new_admin_id: int) -> None:
        """Transfere admin da obra. Caller deve garantir que new_admin é membro."""
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "UPDATE projects SET admin_id = ? WHERE id = ?",
                (new_admin_id, project_id),
            )
            await conn.commit()

    async def get_by_id(self, project_id: int) -> Project | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_project(row) if row else None

    async def get_by_uid(self, uid: str) -> Project | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM projects WHERE uid = ?", (uid,)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_project(row) if row else None

    async def list_for_user(
        self, user_id: int, *, status: str | None = "active"
    ) -> list[Project]:
        """Projetos onde o usuário é membro (ou criador)."""
        params: list = [user_id, user_id]
        clause = ""
        if status is not None:
            clause = "AND p.status = ?"
            params.append(status)
        query = f"""
            SELECT DISTINCT p.* FROM projects p
            LEFT JOIN project_members m ON m.project_id = p.id
            WHERE (m.user_id = ? OR p.created_by = ?) {clause}
            ORDER BY p.created_at DESC
        """
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(query, params) as cur:
                rows = await cur.fetchall()
        return [_row_to_project(r) for r in rows]

    async def update_status(self, project_id: int, status: str) -> None:
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "UPDATE projects SET status = ? WHERE id = ?",
                (status, project_id),
            )
            await conn.commit()


def _row_to_project(row: aiosqlite.Row) -> Project:
    # `admin_id` pode ser NULL em DBs migrados antes de o backfill rodar — o
    # backfill em init_schema cuida de bots reais; aqui o fallback evita None.
    admin_id = row["admin_id"] if "admin_id" in row.keys() and row["admin_id"] is not None else int(row["created_by"])
    return Project(
        id=int(row["id"]),
        uid=str(row["uid"]),
        name=str(row["name"]),
        address=row["address"],
        type=row["type"],
        status=str(row["status"]),
        start_date=row["start_date"],
        end_date=row["end_date"],
        created_by=int(row["created_by"]),
        admin_id=int(admin_id),
        created_at=str(row["created_at"]),
    )
