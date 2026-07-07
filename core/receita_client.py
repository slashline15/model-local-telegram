# core/receita_client.py

"""
Consulta pública de CNPJ (Receita Federal via publica.cnpj.ws) e vínculo
empresa (por obra) → fornecedor (catálogo global).

A API é pública e tem rate limit — por isso o cache: fornecedor já consultado
há menos de `_RF_TTL_Days` dias não gera nova chamada. Falha de rede nunca
propaga: cadastro de empresa funciona igual, só fica sem enriquecimento.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import aiohttp

from core.logger import get_logger
from database.models import Fornecedor
from database.repos.fornecedores import FornecedoresRepo, normalize_cnpj

log = get_logger(__name__)

_RF_URL: str = "https://publica.cnpj.ws/cnpj/{cnpj}"
_RF_TIMEOUT_S: float = 10.0
_RF_TTL_DAYS: int = 30

# Assinatura de um lookup — permite injetar fake nos testes (sem rede).
LookupFn = Callable[[str], Awaitable[dict[str, Any] | None]]


async def lookup_cnpj(cnpj: str) -> dict[str, Any] | None:
    """Consulta CNPJ na base pública. Retorna None se não encontrado/erro."""
    cnpj_limpo = normalize_cnpj(cnpj)
    if len(cnpj_limpo) != 14:
        return None
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=_RF_TIMEOUT_S)
        ) as session:
            async with session.get(_RF_URL.format(cnpj=cnpj_limpo)) as r:
                if r.status != 200:
                    log.info("Receita: CNPJ %s → HTTP %d", cnpj_limpo, r.status)
                    return None
                return await r.json()
    except (aiohttp.ClientError, TimeoutError) as exc:
        log.warning("Receita: falha ao consultar CNPJ %s: %s", cnpj_limpo, exc)
        return None


def _parse_rf_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Extrai campos desnormalizados do JSON da publica.cnpj.ws.

    O payload aninha dados do estabelecimento; parsing defensivo porque o
    formato já mudou no passado.
    """
    estab = data.get("estabelecimento") or {}
    atividade = estab.get("atividade_principal") or {}
    return {
        "razao_social": str(data.get("razao_social") or ""),
        "nome_fantasia": estab.get("nome_fantasia") or data.get("nome_fantasia"),
        "situacao_rf": estab.get("situacao_cadastral") or data.get("situacao_cadastral"),
        "atividade": atividade.get("descricao") or data.get("cnae_fiscal_descricao"),
    }


def _is_fresh(consultado_em: str | None) -> bool:
    if not consultado_em:
        return False
    try:
        ts = datetime.fromisoformat(consultado_em)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return datetime.now(tz=timezone.utc) - ts < timedelta(days=_RF_TTL_DAYS)


async def ensure_fornecedor(
    repo: FornecedoresRepo,
    cnpj: str,
    *,
    lookup: LookupFn | None = lookup_cnpj,
    fallback_nome: str | None = None,
) -> Fornecedor | None:
    """Garante um fornecedor no catálogo global para o CNPJ dado.

    1. Já existe e foi consultado há < 30 dias → retorna direto.
    2. Existe mas está velho → tenta refresh na RF (falha = mantém o que tem).
    3. Não existe → consulta RF e cria; sem RF, cria 'manual' se houver
       `fallback_nome` (o nome digitado no /empresa add), senão retorna None.

    `lookup=None` desliga a rede (testes / modo offline).
    """
    cnpj_limpo = normalize_cnpj(cnpj)
    if len(cnpj_limpo) != 14:
        return None

    existente = await repo.get_by_cnpj(cnpj_limpo)
    if existente is not None and _is_fresh(existente.consultado_em):
        return existente

    data = await lookup(cnpj_limpo) if lookup is not None else None
    if data is None:
        if existente is not None:
            return existente
        if fallback_nome:
            return await repo.create(
                cnpj=cnpj_limpo, razao_social=fallback_nome, fonte="manual"
            )
        return None

    campos = _parse_rf_payload(data)
    razao = campos["razao_social"] or fallback_nome or cnpj_limpo
    if existente is not None:
        await repo.update_from_rf(
            existente.id,
            razao_social=razao,
            nome_fantasia=campos["nome_fantasia"],
            situacao_rf=campos["situacao_rf"],
            dados_rf=data,
        )
        return await repo.get_by_id(existente.id)

    return await repo.create(
        cnpj=cnpj_limpo,
        razao_social=razao,
        nome_fantasia=campos["nome_fantasia"],
        tipo_atividade=campos["atividade"],
        situacao_rf=campos["situacao_rf"],
        fonte="receita_federal",
        dados_rf=data,
        consultado_em=datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
    )
