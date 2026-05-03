# database/repos/colaboradores.py

from __future__ import annotations

import aiosqlite

from core.exceptions import StorageError
from database.models import Colaborador
from database.repos.base import BaseRepo
from database.schema import now_iso


class ColaboradoresRepo(BaseRepo):
    """Pessoas individuais cadastradas por obra (geralmente da empresa própria)."""

    async def create(
        self,
        *,
        uid: str,
        project_id: int,
        empresa_id: int,
        nome: str,
        created_by: int,
        funcao_id: int | None = None,
        apelido: str | None = None,
    ) -> Colaborador:
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                """
                INSERT INTO colaboradores (uid, project_id, empresa_id, funcao_id,
                                           nome, apelido, ativo, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (uid, project_id, empresa_id, funcao_id, nome, apelido,
                 created_by, now_iso()),
            )
            await conn.commit()
            row_id = cur.lastrowid
        if row_id is None:
            raise StorageError("INSERT em colaboradores não retornou lastrowid.")
        c = await self.get_by_id(int(row_id))
        if c is None:
            raise StorageError(f"Colaborador recém-criado id={row_id} não foi encontrado.")
        return c

    async def get_by_id(self, colaborador_id: int) -> Colaborador | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM colaboradores WHERE id = ?", (colaborador_id,)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_colab(row) if row else None

    async def get_by_uid(self, uid: str) -> Colaborador | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM colaboradores WHERE uid = ?", (uid,)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_colab(row) if row else None

    async def list_for_project(
        self,
        project_id: int,
        *,
        only_active: bool = True,
        funcao_id: int | None = None,
        empresa_id: int | None = None,
    ) -> list[Colaborador]:
        clauses = ["project_id = ?"]
        params: list = [project_id]
        if only_active:
            clauses.append("ativo = 1")
        if funcao_id is not None:
            clauses.append("funcao_id = ?")
            params.append(funcao_id)
        if empresa_id is not None:
            clauses.append("empresa_id = ?")
            params.append(empresa_id)
        query = (
            f"SELECT * FROM colaboradores WHERE {' AND '.join(clauses)} "
            "ORDER BY nome ASC"
        )
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(query, params) as cur:
                rows = await cur.fetchall()
        return [_row_to_colab(r) for r in rows]

    async def set_ativo(self, colaborador_id: int, ativo: bool) -> None:
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "UPDATE colaboradores SET ativo = ? WHERE id = ?",
                (int(ativo), colaborador_id),
            )
            await conn.commit()


def _row_to_colab(row: aiosqlite.Row) -> Colaborador:
    return Colaborador(
        id=int(row["id"]),
        uid=str(row["uid"]),
        project_id=int(row["project_id"]),
        empresa_id=int(row["empresa_id"]),
        funcao_id=row["funcao_id"],
        nome=str(row["nome"]),
        apelido=row["apelido"],
        ativo=bool(row["ativo"]),
        created_by=int(row["created_by"]),
        created_at=str(row["created_at"]),
    )
