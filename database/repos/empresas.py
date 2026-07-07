# database/repos/empresas.py

from __future__ import annotations

import aiosqlite

from core.exceptions import StorageError
from database.models import Empresa
from database.repos.base import BaseRepo
from database.schema import now_iso

EMPRESA_TIPO_OWN: str = "own"
EMPRESA_TIPO_THIRD_PARTY: str = "third_party"
_VALID_TIPOS: frozenset[str] = frozenset({EMPRESA_TIPO_OWN, EMPRESA_TIPO_THIRD_PARTY})


class EmpresasRepo(BaseRepo):
    """Empresas (própria e terceirizadas) por obra."""

    async def create(
        self,
        *,
        uid: str,
        project_id: int,
        nome: str,
        created_by: int,
        cnpj: str | None = None,
        tipo: str = EMPRESA_TIPO_THIRD_PARTY,
    ) -> Empresa:
        if tipo not in _VALID_TIPOS:
            raise StorageError(f"Tipo de empresa inválido: {tipo!r}")
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                """
                INSERT INTO empresas (uid, project_id, nome, cnpj, tipo,
                                      ativo, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (uid, project_id, nome, cnpj, tipo, created_by, now_iso()),
            )
            await conn.commit()
            row_id = cur.lastrowid
        if row_id is None:
            raise StorageError("INSERT em empresas não retornou lastrowid.")
        emp = await self.get_by_id(int(row_id))
        if emp is None:
            raise StorageError(f"Empresa recém-criada id={row_id} não foi encontrada.")
        return emp

    async def get_by_id(self, empresa_id: int) -> Empresa | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM empresas WHERE id = ?", (empresa_id,)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_empresa(row) if row else None

    async def get_by_uid(self, uid: str) -> Empresa | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM empresas WHERE uid = ?", (uid,)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_empresa(row) if row else None

    async def find_by_nome(
        self, project_id: int, nome: str, *, only_active: bool = True
    ) -> Empresa | None:
        """Match case-insensitive por nome dentro da obra (pra parser)."""
        clause = "AND ativo = 1" if only_active else ""
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM empresas WHERE project_id = ? "
                f"AND LOWER(nome) = LOWER(?) {clause} LIMIT 1",
                (project_id, nome),
            ) as cur:
                row = await cur.fetchone()
        return _row_to_empresa(row) if row else None

    async def list_for_project(
        self, project_id: int, *, only_active: bool = True,
        tipo: str | None = None,
    ) -> list[Empresa]:
        clauses = ["project_id = ?"]
        params: list = [project_id]
        if only_active:
            clauses.append("ativo = 1")
        if tipo is not None:
            clauses.append("tipo = ?")
            params.append(tipo)
        query = (
            f"SELECT * FROM empresas WHERE {' AND '.join(clauses)} "
            "ORDER BY tipo ASC, nome ASC"
        )
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(query, params) as cur:
                rows = await cur.fetchall()
        return [_row_to_empresa(r) for r in rows]

    async def set_fornecedor(self, empresa_id: int, fornecedor_id: int) -> None:
        """Vincula a empresa local ao catálogo global (auto-link por CNPJ)."""
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "UPDATE empresas SET fornecedor_id = ? WHERE id = ?",
                (fornecedor_id, empresa_id),
            )
            await conn.commit()

    async def set_ativo(self, empresa_id: int, ativo: bool) -> None:
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "UPDATE empresas SET ativo = ? WHERE id = ?",
                (int(ativo), empresa_id),
            )
            await conn.commit()


def _row_to_empresa(row: aiosqlite.Row) -> Empresa:
    return Empresa(
        id=int(row["id"]),
        uid=str(row["uid"]),
        project_id=int(row["project_id"]),
        nome=str(row["nome"]),
        cnpj=row["cnpj"],
        tipo=str(row["tipo"]),
        ativo=bool(row["ativo"]),
        created_by=int(row["created_by"]),
        created_at=str(row["created_at"]),
        fornecedor_id=(
            int(row["fornecedor_id"])
            if "fornecedor_id" in row.keys() and row["fornecedor_id"] is not None
            else None
        ),
    )
