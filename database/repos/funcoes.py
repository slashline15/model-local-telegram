# database/repos/funcoes.py

from __future__ import annotations

import aiosqlite

from core.exceptions import StorageError
from database.models import Funcao
from database.repos.base import BaseRepo
from database.schema import now_iso


class FuncoesRepo(BaseRepo):
    """Catálogo global de cargos. Seed inicial vive em schema.py."""

    async def list_active(self) -> list[Funcao]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM funcoes WHERE ativo = 1 ORDER BY nome ASC"
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_funcao(r) for r in rows]

    async def get_by_id(self, funcao_id: int) -> Funcao | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM funcoes WHERE id = ?", (funcao_id,)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_funcao(row) if row else None

    async def get_by_nome(self, nome: str) -> Funcao | None:
        """Busca case-insensitive por nome exato."""
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM funcoes WHERE LOWER(nome) = LOWER(?)", (nome,)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_funcao(row) if row else None

    async def create(self, *, nome: str) -> Funcao:
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                "INSERT INTO funcoes (nome, ativo, created_at) VALUES (?, 1, ?)",
                (nome, now_iso()),
            )
            await conn.commit()
            row_id = cur.lastrowid
        if row_id is None:
            raise StorageError("INSERT em funcoes não retornou lastrowid.")
        f = await self.get_by_id(int(row_id))
        if f is None:
            raise StorageError(f"Função recém-criada id={row_id} não foi encontrada.")
        return f

    async def set_ativo(self, funcao_id: int, ativo: bool) -> None:
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "UPDATE funcoes SET ativo = ? WHERE id = ?",
                (int(ativo), funcao_id),
            )
            await conn.commit()


def _row_to_funcao(row: aiosqlite.Row) -> Funcao:
    return Funcao(
        id=int(row["id"]),
        nome=str(row["nome"]),
        ativo=bool(row["ativo"]),
        created_at=str(row["created_at"]),
    )
