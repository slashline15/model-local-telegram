# database/repos/efetivo.py

from __future__ import annotations

import aiosqlite

from core.exceptions import StorageError
from database.models import EfetivoDiario
from database.repos.base import BaseRepo
from database.schema import now_iso


class EfetivoRepo(BaseRepo):
    """Quantitativo de mão de obra por dia/obra/função/empresa."""

    async def insert(
        self,
        *,
        project_id: int,
        dia: str,
        funcao_id: int,
        empresa_id: int | None,
        qtd: int,
        criado_por: int,
        interaction_id: int | None = None,
    ) -> int:
        if qtd <= 0:
            raise StorageError(f"Qtd deve ser positiva (recebi {qtd}).")
        try:
            async with aiosqlite.connect(self._db_path) as conn:
                cur = await conn.execute(
                    """
                    INSERT INTO efetivo_diario (
                        project_id, dia, funcao_id, empresa_id, qtd,
                        interaction_id, criado_por, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id, dia, funcao_id, empresa_id, qtd,
                        interaction_id, criado_por, now_iso(),
                    ),
                )
                await conn.commit()
                if cur.lastrowid is None:
                    raise StorageError("INSERT efetivo_diario não retornou id.")
                return int(cur.lastrowid)
        except aiosqlite.Error as exc:
            raise StorageError(f"Falha ao inserir efetivo: {exc}") from exc

    async def list_for_dia(
        self, project_id: int, dia: str
    ) -> list[EfetivoDiario]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM efetivo_diario "
                "WHERE project_id = ? AND dia = ? "
                "ORDER BY id ASC",
                (project_id, dia),
            ) as cur:
                rows = await cur.fetchall()
        return [_row(r) for r in rows]

    async def list_recent(
        self, project_id: int, limit: int = 30
    ) -> list[EfetivoDiario]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM efetivo_diario WHERE project_id = ? "
                "ORDER BY dia DESC, id DESC LIMIT ?",
                (project_id, int(limit)),
            ) as cur:
                rows = await cur.fetchall()
        return [_row(r) for r in rows]


def _row(r: aiosqlite.Row) -> EfetivoDiario:
    return EfetivoDiario(
        id=int(r["id"]),
        project_id=int(r["project_id"]),
        dia=str(r["dia"]),
        funcao_id=int(r["funcao_id"]),
        empresa_id=r["empresa_id"],
        qtd=int(r["qtd"]),
        interaction_id=r["interaction_id"],
        criado_por=int(r["criado_por"]),
        created_at=str(r["created_at"]),
    )
