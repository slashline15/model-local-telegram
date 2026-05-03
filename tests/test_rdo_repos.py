from __future__ import annotations

import pytest

from core.uid import gen_uid
from database.repos.empresas import EMPRESA_TIPO_OWN, EMPRESA_TIPO_THIRD_PARTY
from database.sqlite_mgr import SQLiteManager


pytestmark = pytest.mark.asyncio


async def _make_admin_with_project(mgr: SQLiteManager, telegram_id: int = 1):
    admin = await mgr.users.register(telegram_id=telegram_id, name="Admin", role="admin")
    proj = await mgr.projects.create(uid=gen_uid(), name="Obra", created_by=admin.id)
    return admin, proj


# ────────────────── funcoes ──────────────────

async def test_seed_creates_default_funcoes(sqlite_mgr: SQLiteManager) -> None:
    rows = await sqlite_mgr.funcoes.list_active()
    nomes = {r.nome for r in rows}
    # Amostra do desenho:
    assert {"Pedreiro", "Servente", "Mestre de obras", "Engenheiro"}.issubset(nomes)
    assert len(rows) >= 15


async def test_seed_is_idempotent_on_double_init(sqlite_mgr: SQLiteManager) -> None:
    await sqlite_mgr.init_schema()  # roda de novo
    rows = await sqlite_mgr.funcoes.list_active()
    nomes = [r.nome for r in rows]
    assert len(nomes) == len(set(nomes))  # sem duplicata


async def test_get_funcao_by_nome_is_case_insensitive(sqlite_mgr: SQLiteManager) -> None:
    f1 = await sqlite_mgr.funcoes.get_by_nome("pedreiro")
    f2 = await sqlite_mgr.funcoes.get_by_nome("PEDREIRO")
    f3 = await sqlite_mgr.funcoes.get_by_nome("Pedreiro")
    assert f1 is not None and f2 is not None and f3 is not None
    assert f1.id == f2.id == f3.id


async def test_set_ativo_hides_from_list(sqlite_mgr: SQLiteManager) -> None:
    f = await sqlite_mgr.funcoes.get_by_nome("Motorista")
    assert f is not None
    await sqlite_mgr.funcoes.set_ativo(f.id, False)
    nomes = {r.nome for r in await sqlite_mgr.funcoes.list_active()}
    assert "Motorista" not in nomes


# ────────────────── empresas ──────────────────

async def test_create_empresa_default_third_party(sqlite_mgr: SQLiteManager) -> None:
    admin, proj = await _make_admin_with_project(sqlite_mgr, telegram_id=10)
    e = await sqlite_mgr.empresas.create(
        uid=gen_uid(), project_id=proj.id, nome="Patamar", created_by=admin.id,
    )
    assert e.tipo == EMPRESA_TIPO_THIRD_PARTY
    assert e.ativo is True


async def test_create_empresa_own_with_cnpj(sqlite_mgr: SQLiteManager) -> None:
    admin, proj = await _make_admin_with_project(sqlite_mgr, telegram_id=11)
    e = await sqlite_mgr.empresas.create(
        uid=gen_uid(), project_id=proj.id, nome="HOSS",
        cnpj="12.345.678/0001-90", tipo=EMPRESA_TIPO_OWN, created_by=admin.id,
    )
    assert e.tipo == EMPRESA_TIPO_OWN
    assert e.cnpj == "12.345.678/0001-90"


async def test_invalid_tipo_rejected(sqlite_mgr: SQLiteManager) -> None:
    admin, proj = await _make_admin_with_project(sqlite_mgr, telegram_id=12)
    with pytest.raises(Exception):
        await sqlite_mgr.empresas.create(
            uid=gen_uid(), project_id=proj.id, nome="X",
            tipo="invalido", created_by=admin.id,
        )


async def test_list_filters_inactive_and_by_tipo(sqlite_mgr: SQLiteManager) -> None:
    admin, proj = await _make_admin_with_project(sqlite_mgr, telegram_id=13)
    own = await sqlite_mgr.empresas.create(
        uid=gen_uid(), project_id=proj.id, nome="Hoss",
        tipo=EMPRESA_TIPO_OWN, created_by=admin.id,
    )
    t1 = await sqlite_mgr.empresas.create(
        uid=gen_uid(), project_id=proj.id, nome="Patamar", created_by=admin.id,
    )
    t2 = await sqlite_mgr.empresas.create(
        uid=gen_uid(), project_id=proj.id, nome="Master", created_by=admin.id,
    )
    await sqlite_mgr.empresas.set_ativo(t2.id, False)

    actives = await sqlite_mgr.empresas.list_for_project(proj.id)
    assert {e.id for e in actives} == {own.id, t1.id}

    only_third = await sqlite_mgr.empresas.list_for_project(
        proj.id, tipo=EMPRESA_TIPO_THIRD_PARTY,
    )
    assert {e.id for e in only_third} == {t1.id}


async def test_find_by_nome_case_insensitive_within_project(
    sqlite_mgr: SQLiteManager,
) -> None:
    admin, proj = await _make_admin_with_project(sqlite_mgr, telegram_id=14)
    await sqlite_mgr.empresas.create(
        uid=gen_uid(), project_id=proj.id, nome="Rocha Alumínio", created_by=admin.id,
    )
    found = await sqlite_mgr.empresas.find_by_nome(proj.id, "rocha alumínio")
    assert found is not None and found.nome == "Rocha Alumínio"


# ────────────────── colaboradores ──────────────────

async def test_create_colaborador_with_function_and_company(
    sqlite_mgr: SQLiteManager,
) -> None:
    admin, proj = await _make_admin_with_project(sqlite_mgr, telegram_id=20)
    emp = await sqlite_mgr.empresas.create(
        uid=gen_uid(), project_id=proj.id, nome="HOSS",
        tipo=EMPRESA_TIPO_OWN, created_by=admin.id,
    )
    funcao = await sqlite_mgr.funcoes.get_by_nome("Pedreiro")
    assert funcao is not None

    c = await sqlite_mgr.colaboradores.create(
        uid=gen_uid(), project_id=proj.id, empresa_id=emp.id,
        funcao_id=funcao.id, nome="João Silva", apelido="Joãozinho",
        created_by=admin.id,
    )
    assert c.nome == "João Silva"
    assert c.apelido == "Joãozinho"
    assert c.funcao_id == funcao.id


async def test_list_colaboradores_filters(sqlite_mgr: SQLiteManager) -> None:
    admin, proj = await _make_admin_with_project(sqlite_mgr, telegram_id=21)
    emp = await sqlite_mgr.empresas.create(
        uid=gen_uid(), project_id=proj.id, nome="HOSS",
        tipo=EMPRESA_TIPO_OWN, created_by=admin.id,
    )
    pedreiro = await sqlite_mgr.funcoes.get_by_nome("Pedreiro")
    servente = await sqlite_mgr.funcoes.get_by_nome("Servente")
    assert pedreiro and servente

    p1 = await sqlite_mgr.colaboradores.create(
        uid=gen_uid(), project_id=proj.id, empresa_id=emp.id,
        funcao_id=pedreiro.id, nome="A", created_by=admin.id,
    )
    p2 = await sqlite_mgr.colaboradores.create(
        uid=gen_uid(), project_id=proj.id, empresa_id=emp.id,
        funcao_id=pedreiro.id, nome="B", created_by=admin.id,
    )
    s1 = await sqlite_mgr.colaboradores.create(
        uid=gen_uid(), project_id=proj.id, empresa_id=emp.id,
        funcao_id=servente.id, nome="C", created_by=admin.id,
    )
    await sqlite_mgr.colaboradores.set_ativo(p2.id, False)

    actives = await sqlite_mgr.colaboradores.list_for_project(proj.id)
    assert {c.id for c in actives} == {p1.id, s1.id}

    only_pedreiros = await sqlite_mgr.colaboradores.list_for_project(
        proj.id, funcao_id=pedreiro.id,
    )
    assert {c.id for c in only_pedreiros} == {p1.id}


async def test_colaborador_isolated_per_project(sqlite_mgr: SQLiteManager) -> None:
    admin, p1 = await _make_admin_with_project(sqlite_mgr, telegram_id=30)
    p2 = await sqlite_mgr.projects.create(uid=gen_uid(), name="Outra", created_by=admin.id)

    e1 = await sqlite_mgr.empresas.create(
        uid=gen_uid(), project_id=p1.id, nome="X", created_by=admin.id,
    )
    e2 = await sqlite_mgr.empresas.create(
        uid=gen_uid(), project_id=p2.id, nome="Y", created_by=admin.id,
    )
    await sqlite_mgr.colaboradores.create(
        uid=gen_uid(), project_id=p1.id, empresa_id=e1.id,
        nome="Alice", created_by=admin.id,
    )
    await sqlite_mgr.colaboradores.create(
        uid=gen_uid(), project_id=p2.id, empresa_id=e2.id,
        nome="Bob", created_by=admin.id,
    )

    n1 = await sqlite_mgr.colaboradores.list_for_project(p1.id)
    n2 = await sqlite_mgr.colaboradores.list_for_project(p2.id)
    assert {c.nome for c in n1} == {"Alice"}
    assert {c.nome for c in n2} == {"Bob"}
