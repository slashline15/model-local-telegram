# database/repos/clima.py

from __future__ import annotations

import aiosqlite

from core.exceptions import StorageError
from database.models import ClimaDiario
from database.repos.base import BaseRepo
from database.schema import now_iso

CONDICAO_SOL = "sol"
CONDICAO_NUBLADO = "nublado"
CONDICAO_CHUVA = "chuva"
_CONDICOES_VALIDAS = {CONDICAO_SOL, CONDICAO_NUBLADO, CONDICAO_CHUVA}


def validar_condicao(condicao: str) -> str:
    c = condicao.strip().lower()
    if c not in _CONDICOES_VALIDAS:
        raise StorageError(
            f"Condição inválida: {condicao!r}. Use sol, nublado ou chuva."
        )
    return c


class ClimaRepo(BaseRepo):
    """Registros climáticos por dia/obra."""

    async def insert(
        self,
        *,
        project_id: int,
        dia: str,
        condicao: str,
        hora_inicio: str | None,
        hora_fim: str | None,
        criado_por: int,
        interaction_id: int | None = None,
    ) -> int:
        try:
            async with aiosqlite.connect(self._db_path) as conn:
                cur = await conn.execute(
                    """
                    INSERT INTO clima_diario (
                        project_id, dia, condicao,
                        hora_inicio, hora_fim,
                        interaction_id, criado_por, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id, dia, condicao,
                        hora_inicio, hora_fim,
                        interaction_id, criado_por, now_iso(),
                    ),
                )
                await conn.commit()
                if cur.lastrowid is None:
                    raise StorageError("INSERT clima_diario não retornou id.")
                return int(cur.lastrowid)
        except aiosqlite.Error as exc:
            raise StorageError(f"Falha ao inserir clima: {exc}") from exc

    async def list_for_dia(
        self, project_id: int, dia: str
    ) -> list[ClimaDiario]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM clima_diario "
                "WHERE project_id = ? AND dia = ? "
                "ORDER BY id ASC",
                (project_id, dia),
            ) as cur:
                rows = await cur.fetchall()
        return [_row(r) for r in rows]

    async def list_recent(
        self, project_id: int, limit: int = 10
    ) -> list[ClimaDiario]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM clima_diario WHERE project_id = ? "
                "ORDER BY dia DESC, id DESC LIMIT ?",
                (project_id, int(limit)),
            ) as cur:
                rows = await cur.fetchall()
        return [_row(r) for r in rows]


def _row(r: aiosqlite.Row) -> ClimaDiario:
    return ClimaDiario(
        id=int(r["id"]),
        project_id=int(r["project_id"]),
        dia=str(r["dia"]),
        condicao=str(r["condicao"]),
        hora_inicio=r["hora_inicio"],
        hora_fim=r["hora_fim"],
        interaction_id=r["interaction_id"],
        criado_por=int(r["criado_por"]),
        created_at=str(r["created_at"]),
    )
