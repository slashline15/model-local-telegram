# database/repos/cronograma.py

from __future__ import annotations

import aiosqlite

from core.exceptions import StorageError
from database.models import CronogramaEtapa
from database.repos.base import BaseRepo
from database.schema import now_iso


class CronogramaEtapasRepo(BaseRepo):
    """CRUD para cronograma_etapas."""

    async def create(
        self,
        *,
        uid: str,
        project_id: int,
        etapa: str,
        descricao: str | None = None,
        data_prevista_inicio: str | None = None,
        data_prevista_termino: str | None = None,
        parent_id: int | None = None,
        ordem: int = 0,
    ) -> CronogramaEtapa:
        try:
            async with aiosqlite.connect(self._db_path) as conn:
                cur = await conn.execute(
                    """
                    INSERT INTO cronograma_etapas
                        (uid, project_id, parent_id, etapa, descricao,
                         data_prevista_inicio, data_prevista_termino, ordem, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (uid, project_id, parent_id, etapa, descricao,
                     data_prevista_inicio, data_prevista_termino, ordem, now_iso()),
                )
                await conn.commit()
                if cur.lastrowid is None:
                    raise StorageError("INSERT não retornou lastrowid.")
                row_id = int(cur.lastrowid)
            return await self._get_or_raise(row_id)
        except aiosqlite.Error as exc:
            raise StorageError(f"Falha ao criar etapa: {exc}") from exc

    async def list_for_project(self, project_id: int) -> list[CronogramaEtapa]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM cronograma_etapas WHERE project_id = ? ORDER BY ordem, id",
                (project_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_etapa(r) for r in rows]

    async def get_by_id(self, etapa_id: int) -> CronogramaEtapa | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM cronograma_etapas WHERE id = ?", (etapa_id,)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_etapa(row) if row else None

    async def _get_or_raise(self, etapa_id: int) -> CronogramaEtapa:
        e = await self.get_by_id(etapa_id)
        if e is None:
            raise StorageError(f"Etapa {etapa_id} não encontrada após INSERT.")
        return e


def _row_to_etapa(row: aiosqlite.Row) -> CronogramaEtapa:
    def _opt(k: str):  # type: ignore[no-untyped-def]
        return row[k] if k in row.keys() else None

    return CronogramaEtapa(
        id=int(row["id"]),
        uid=str(row["uid"]),
        project_id=int(row["project_id"]),
        parent_id=_opt("parent_id"),
        etapa=str(row["etapa"]),
        descricao=_opt("descricao"),
        data_prevista_inicio=_opt("data_prevista_inicio"),
        data_prevista_termino=_opt("data_prevista_termino"),
        ordem=int(row["ordem"] or 0),
        created_at=str(row["created_at"]),
    )
