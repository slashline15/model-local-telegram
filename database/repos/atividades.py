# database/repos/atividades.py

from __future__ import annotations

import aiosqlite

from core.exceptions import StorageError
from database.models import Atividade
from database.repos.base import BaseRepo
from database.schema import now_iso

ESTADO_CONCLUIDA = "concluida"
ESTADO_EM_ANDAMENTO = "em_andamento"
ESTADO_ATRASADA = "atrasada"
ESTADO_IMPEDIDA = "impedida"
_ESTADOS_VALIDOS = {
    ESTADO_CONCLUIDA, ESTADO_EM_ANDAMENTO, ESTADO_ATRASADA, ESTADO_IMPEDIDA,
}

_ESTADO_ALIASES: dict[str, str] = {
    "concluida":    ESTADO_CONCLUIDA,
    "concluída":    ESTADO_CONCLUIDA,
    "ok":           ESTADO_CONCLUIDA,
    "feita":        ESTADO_CONCLUIDA,
    "andamento":    ESTADO_EM_ANDAMENTO,
    "em_andamento": ESTADO_EM_ANDAMENTO,
    "andando":      ESTADO_EM_ANDAMENTO,
    "atrasada":     ESTADO_ATRASADA,
    "atraso":       ESTADO_ATRASADA,
    "impedida":     ESTADO_IMPEDIDA,
    "parada":       ESTADO_IMPEDIDA,
    "bloqueada":    ESTADO_IMPEDIDA,
}


def normalizar_estado(estado: str) -> str:
    e = estado.strip().lower()
    mapped = _ESTADO_ALIASES.get(e)
    if mapped is None:
        raise StorageError(
            f"Estado inválido: {estado!r}. "
            f"Use: concluida | em_andamento | atrasada | impedida."
        )
    return mapped


class AtividadesRepo(BaseRepo):
    """Atividades operacionais por dia/obra (a etapa do cronograma é opcional)."""

    async def insert(
        self,
        *,
        project_id: int,
        dia: str,
        estado: str,
        descricao: str,
        criado_por: int,
        etapa_id: int | None = None,
        responsavel_id: int | None = None,
        interaction_id: int | None = None,
    ) -> int:
        try:
            async with aiosqlite.connect(self._db_path) as conn:
                cur = await conn.execute(
                    """
                    INSERT INTO atividades (
                        project_id, dia, etapa_id, responsavel_id,
                        estado, descricao,
                        interaction_id, criado_por, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id, dia, etapa_id, responsavel_id,
                        estado, descricao,
                        interaction_id, criado_por, now_iso(),
                    ),
                )
                await conn.commit()
                if cur.lastrowid is None:
                    raise StorageError("INSERT atividades não retornou id.")
                return int(cur.lastrowid)
        except aiosqlite.Error as exc:
            raise StorageError(f"Falha ao inserir atividade: {exc}") from exc

    async def list_for_dia(
        self, project_id: int, dia: str
    ) -> list[Atividade]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM atividades "
                "WHERE project_id = ? AND dia = ? "
                "ORDER BY id ASC",
                (project_id, dia),
            ) as cur:
                rows = await cur.fetchall()
        return [_row(r) for r in rows]

    async def list_recent(
        self, project_id: int, limit: int = 20
    ) -> list[Atividade]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM atividades WHERE project_id = ? "
                "ORDER BY dia DESC, id DESC LIMIT ?",
                (project_id, int(limit)),
            ) as cur:
                rows = await cur.fetchall()
        return [_row(r) for r in rows]


def _row(r: aiosqlite.Row) -> Atividade:
    return Atividade(
        id=int(r["id"]),
        project_id=int(r["project_id"]),
        dia=str(r["dia"]),
        etapa_id=r["etapa_id"],
        responsavel_id=r["responsavel_id"],
        estado=str(r["estado"]),
        descricao=str(r["descricao"]),
        interaction_id=r["interaction_id"],
        criado_por=int(r["criado_por"]),
        created_at=str(r["created_at"]),
    )
