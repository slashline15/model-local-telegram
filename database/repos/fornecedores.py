# database/repos/fornecedores.py

from __future__ import annotations

import json
import re

import aiosqlite

from core.exceptions import StorageError
from database.models import Fornecedor
from database.repos.base import BaseRepo
from database.schema import now_iso


def normalize_cnpj(cnpj: str) -> str:
    """Só dígitos — chave natural da tabela."""
    return re.sub(r"\D", "", cnpj)


class FornecedoresRepo(BaseRepo):
    """Catálogo global de fornecedores — CNPJ como chave natural.

    Medições e notas ficam nas tabelas por obra; aqui só o cadastro canônico
    (com enriquecimento opcional da Receita Federal).
    """

    async def get_by_cnpj(self, cnpj: str) -> Fornecedor | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM fornecedores WHERE cnpj = ?",
                (normalize_cnpj(cnpj),),
            ) as cur:
                row = await cur.fetchone()
        return _row_to_fornecedor(row) if row else None

    async def get_by_id(self, fornecedor_id: int) -> Fornecedor | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM fornecedores WHERE id = ?", (fornecedor_id,)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_fornecedor(row) if row else None

    async def create(
        self,
        *,
        cnpj: str,
        razao_social: str,
        nome_fantasia: str | None = None,
        tipo_atividade: str | None = None,
        situacao_rf: str | None = None,
        fonte: str = "manual",
        dados_rf: dict | None = None,
        consultado_em: str | None = None,
    ) -> Fornecedor:
        cnpj_limpo = normalize_cnpj(cnpj)
        if not cnpj_limpo:
            raise StorageError(f"CNPJ inválido: {cnpj!r}")
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                """
                INSERT INTO fornecedores
                    (cnpj, razao_social, nome_fantasia, tipo_atividade,
                     situacao_rf, fonte, dados_rf, consultado_em, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cnpj_limpo, razao_social, nome_fantasia, tipo_atividade,
                    situacao_rf, fonte,
                    json.dumps(dados_rf, ensure_ascii=False) if dados_rf else None,
                    consultado_em, now_iso(),
                ),
            )
            await conn.commit()
            row_id = cur.lastrowid
        if row_id is None:
            raise StorageError("INSERT em fornecedores não retornou lastrowid.")
        forn = await self.get_by_id(int(row_id))
        if forn is None:
            raise StorageError(f"Fornecedor recém-criado id={row_id} não encontrado.")
        return forn

    async def update_from_rf(
        self,
        fornecedor_id: int,
        *,
        razao_social: str,
        nome_fantasia: str | None,
        situacao_rf: str | None,
        dados_rf: dict,
    ) -> None:
        """Atualiza campos desnormalizados com dados frescos da Receita."""
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                """
                UPDATE fornecedores
                SET razao_social = ?, nome_fantasia = ?, situacao_rf = ?,
                    fonte = 'receita_federal', dados_rf = ?, consultado_em = ?
                WHERE id = ?
                """,
                (
                    razao_social, nome_fantasia, situacao_rf,
                    json.dumps(dados_rf, ensure_ascii=False),
                    now_iso(), fornecedor_id,
                ),
            )
            await conn.commit()

    async def list_all(self, *, limit: int = 500) -> list[Fornecedor]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM fornecedores ORDER BY razao_social LIMIT ?",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_fornecedor(r) for r in rows]

    async def search_by_nome(self, termo: str, *, limit: int = 20) -> list[Fornecedor]:
        like = f"%{termo}%"
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT * FROM fornecedores
                WHERE razao_social LIKE ? OR nome_fantasia LIKE ?
                ORDER BY razao_social LIMIT ?
                """,
                (like, like, limit),
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_fornecedor(r) for r in rows]


def _row_to_fornecedor(row: aiosqlite.Row) -> Fornecedor:
    return Fornecedor(
        id=int(row["id"]),
        cnpj=str(row["cnpj"]),
        razao_social=str(row["razao_social"]),
        nome_fantasia=row["nome_fantasia"],
        tipo_atividade=row["tipo_atividade"],
        situacao_rf=row["situacao_rf"],
        fonte=str(row["fonte"]),
        dados_rf=row["dados_rf"],
        consultado_em=row["consultado_em"],
        created_at=str(row["created_at"]),
    )
