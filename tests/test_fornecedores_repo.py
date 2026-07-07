from __future__ import annotations

import pytest

from core.receita_client import ensure_fornecedor
from database.sqlite_mgr import SQLiteManager

pytestmark = pytest.mark.asyncio

_CNPJ = "12.345.678/0001-95"
_CNPJ_LIMPO = "12345678000195"


async def test_create_and_get_by_cnpj_normalizes(sqlite_mgr: SQLiteManager) -> None:
    forn = await sqlite_mgr.fornecedores.create(
        cnpj=_CNPJ, razao_social="Construtora Alfa LTDA"
    )
    assert forn.cnpj == _CNPJ_LIMPO
    assert forn.fonte == "manual"

    # Busca com CNPJ formatado acha o registro salvo limpo.
    found = await sqlite_mgr.fornecedores.get_by_cnpj(_CNPJ)
    assert found is not None and found.id == forn.id


async def test_update_from_rf(sqlite_mgr: SQLiteManager) -> None:
    forn = await sqlite_mgr.fornecedores.create(
        cnpj=_CNPJ_LIMPO, razao_social="Nome Provisório"
    )
    await sqlite_mgr.fornecedores.update_from_rf(
        forn.id,
        razao_social="Construtora Alfa LTDA",
        nome_fantasia="Alfa",
        situacao_rf="Ativa",
        dados_rf={"razao_social": "Construtora Alfa LTDA"},
    )
    updated = await sqlite_mgr.fornecedores.get_by_id(forn.id)
    assert updated is not None
    assert updated.fonte == "receita_federal"
    assert updated.situacao_rf == "Ativa"
    assert updated.consultado_em is not None


async def test_search_by_nome(sqlite_mgr: SQLiteManager) -> None:
    await sqlite_mgr.fornecedores.create(
        cnpj=_CNPJ_LIMPO, razao_social="Elétrica Beta", nome_fantasia="BetaLuz"
    )
    hits = await sqlite_mgr.fornecedores.search_by_nome("Beta")
    assert len(hits) == 1
    hits_fantasia = await sqlite_mgr.fornecedores.search_by_nome("BetaLuz")
    assert len(hits_fantasia) == 1


async def test_empresa_set_fornecedor_roundtrip(sqlite_mgr: SQLiteManager) -> None:
    user = await sqlite_mgr.register_user(telegram_id=111, name="Dono", role="admin")
    proj = await sqlite_mgr.projects.create(
        uid="p1", name="Obra Teste", created_by=user.id, admin_id=user.id
    )
    emp = await sqlite_mgr.empresas.create(
        uid="e1", project_id=proj.id, nome="Alfa", created_by=user.id,
        cnpj=_CNPJ_LIMPO,
    )
    assert emp.fornecedor_id is None

    forn = await sqlite_mgr.fornecedores.create(
        cnpj=_CNPJ_LIMPO, razao_social="Construtora Alfa LTDA"
    )
    await sqlite_mgr.empresas.set_fornecedor(emp.id, forn.id)
    reloaded = await sqlite_mgr.empresas.get_by_id(emp.id)
    assert reloaded is not None and reloaded.fornecedor_id == forn.id


async def test_ensure_fornecedor_creates_from_rf(sqlite_mgr: SQLiteManager) -> None:
    async def fake_lookup(cnpj: str) -> dict:
        return {
            "razao_social": "Construtora Alfa LTDA",
            "estabelecimento": {
                "nome_fantasia": "Alfa",
                "situacao_cadastral": "Ativa",
                "atividade_principal": {"descricao": "Construção de edifícios"},
            },
        }

    forn = await ensure_fornecedor(
        sqlite_mgr.fornecedores, _CNPJ, lookup=fake_lookup
    )
    assert forn is not None
    assert forn.razao_social == "Construtora Alfa LTDA"
    assert forn.fonte == "receita_federal"
    assert forn.situacao_rf == "Ativa"
    assert forn.tipo_atividade == "Construção de edifícios"


async def test_ensure_fornecedor_fallback_manual(sqlite_mgr: SQLiteManager) -> None:
    async def lookup_falha(cnpj: str) -> None:
        return None

    forn = await ensure_fornecedor(
        sqlite_mgr.fornecedores, _CNPJ,
        lookup=lookup_falha, fallback_nome="Alfa Digitada",
    )
    assert forn is not None
    assert forn.fonte == "manual"
    assert forn.razao_social == "Alfa Digitada"


async def test_ensure_fornecedor_cache_skips_lookup(sqlite_mgr: SQLiteManager) -> None:
    calls: list[str] = []

    async def counting_lookup(cnpj: str) -> dict:
        calls.append(cnpj)
        return {"razao_social": "Alfa"}

    first = await ensure_fornecedor(
        sqlite_mgr.fornecedores, _CNPJ_LIMPO, lookup=counting_lookup
    )
    assert first is not None and len(calls) == 1

    # Segunda chamada: consultado_em fresco ⇒ não consulta a RF de novo.
    second = await ensure_fornecedor(
        sqlite_mgr.fornecedores, _CNPJ_LIMPO, lookup=counting_lookup
    )
    assert second is not None and second.id == first.id
    assert len(calls) == 1


async def test_ensure_fornecedor_invalid_cnpj(sqlite_mgr: SQLiteManager) -> None:
    forn = await ensure_fornecedor(
        sqlite_mgr.fornecedores, "123", lookup=None, fallback_nome="X"
    )
    assert forn is None
