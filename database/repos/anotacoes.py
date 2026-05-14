# database/repos/anotacoes.py

from __future__ import annotations

import aiosqlite

from core.exceptions import StorageError
from database.models import Anotacao
from database.repos.base import BaseRepo
from database.schema import now_iso

NATUREZA_EVENTO = "evento"
NATUREZA_OCORRENCIA = "ocorrencia"
_NATUREZAS_VALIDAS = {NATUREZA_EVENTO, NATUREZA_OCORRENCIA}

VISIBILIDADE_PUBLICA = "publica"
VISIBILIDADE_PRIVADA = "privada"


class AnotacoesRepo(BaseRepo):
    """Anotações livres do diário — campo mais importante do RDO."""

    async def insert(
        self,
        *,
        project_id: int,
        dia: str,
        texto: str,
        criado_por: int,
        natureza: str = NATUREZA_EVENTO,
        inicio: str | None = None,
        fim: str | None = None,
        atividade_id: int | None = None,
        recurso: str | None = None,
        impacto: str | None = None,
        visibilidade: str = VISIBILIDADE_PUBLICA,
        interaction_id: int | None = None,
    ) -> int:
        if natureza not in _NATUREZAS_VALIDAS:
            raise StorageError(
                f"Natureza inválida: {natureza!r}. Use evento ou ocorrencia."
            )
        if not texto.strip():
            raise StorageError("Texto da anotação não pode ser vazio.")
        try:
            async with aiosqlite.connect(self._db_path) as conn:
                cur = await conn.execute(
                    """
                    INSERT INTO anotacoes (
                        project_id, dia, inicio, fim, natureza,
                        atividade_id, recurso, impacto, texto, visibilidade,
                        interaction_id, criado_por, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        project_id, dia, inicio, fim, natureza,
                        atividade_id, recurso, impacto, texto, visibilidade,
                        interaction_id, criado_por, now_iso(),
                    ),
                )
                await conn.commit()
                if cur.lastrowid is None:
                    raise StorageError("INSERT anotacoes não retornou id.")
                return int(cur.lastrowid)
        except aiosqlite.Error as exc:
            raise StorageError(f"Falha ao inserir anotação: {exc}") from exc

    async def list_for_dia(
        self,
        project_id: int,
        dia: str,
        *,
        requester_user_id: int | None,
    ) -> list[Anotacao]:
        """Lista anotações do dia respeitando visibilidade (mesma regra do
        repo de interactions: pública OR dono). `requester_user_id=None` é
        bypass explícito."""
        params: list[object] = [project_id, dia]
        where = "project_id = ? AND dia = ?"
        if requester_user_id is not None:
            where += " AND (visibilidade = 'publica' OR criado_por = ?)"
            params.append(requester_user_id)
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM anotacoes WHERE {where} ORDER BY id ASC",
                params,
            ) as cur:
                rows = await cur.fetchall()
        return [_row(r) for r in rows]

    async def list_recent(
        self,
        project_id: int,
        limit: int = 10,
        *,
        requester_user_id: int | None,
    ) -> list[Anotacao]:
        params: list[object] = [project_id]
        where = "project_id = ?"
        if requester_user_id is not None:
            where += " AND (visibilidade = 'publica' OR criado_por = ?)"
            params.append(requester_user_id)
        params.append(int(limit))
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM anotacoes WHERE {where} "
                f"ORDER BY dia DESC, id DESC LIMIT ?",
                params,
            ) as cur:
                rows = await cur.fetchall()
        return [_row(r) for r in rows]


def _row(r: aiosqlite.Row) -> Anotacao:
    return Anotacao(
        id=int(r["id"]),
        project_id=int(r["project_id"]),
        dia=str(r["dia"]),
        inicio=r["inicio"],
        fim=r["fim"],
        natureza=str(r["natureza"]),
        atividade_id=r["atividade_id"],
        recurso=r["recurso"],
        impacto=r["impacto"],
        texto=str(r["texto"]),
        visibilidade=str(r["visibilidade"]),
        interaction_id=r["interaction_id"],
        criado_por=int(r["criado_por"]),
        created_at=str(r["created_at"]),
    )
