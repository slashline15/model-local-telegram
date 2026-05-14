"""Smoke tests dos repos da Fase 4 (clima, efetivo, atividades, anotacoes)."""

from __future__ import annotations

import pytest

from core.exceptions import StorageError
from database.repos.anotacoes import VISIBILIDADE_PRIVADA, VISIBILIDADE_PUBLICA
from database.repos.atividades import (
    ESTADO_CONCLUIDA, ESTADO_EM_ANDAMENTO, normalizar_estado,
)
from database.repos.clima import (
    CONDICAO_CHUVA, CONDICAO_SOL, validar_condicao,
)
from database.sqlite_mgr import SQLiteManager
from database.schema import now_iso

# Marca async em cada teste async (os 2 sync não devem entrar nessa marca).
_async = pytest.mark.asyncio


async def _make_project_and_user(mgr: SQLiteManager) -> tuple[int, int]:
    """Cria user + project mínimos pra FKs. Retorna (user_id, project_id)."""
    import aiosqlite

    async with aiosqlite.connect(mgr._db_path) as conn:
        cur = await conn.execute(
            "INSERT INTO users (telegram_id, name, role, status, created_at, updated_at) "
            "VALUES (?, ?, 'admin', 'active', ?, ?)",
            (1001, "tester", now_iso(), now_iso()),
        )
        user_id = cur.lastrowid
        cur = await conn.execute(
            "INSERT INTO projects (uid, name, status, created_by, admin_id, created_at) "
            "VALUES (?, ?, 'active', ?, ?, ?)",
            ("TESTPROJ", "Obra Teste", user_id, user_id, now_iso()),
        )
        project_id = cur.lastrowid
        await conn.commit()
    assert user_id is not None and project_id is not None
    return int(user_id), int(project_id)


@_async
async def test_clima_insert_and_list(sqlite_mgr: SQLiteManager) -> None:
    user_id, project_id = await _make_project_and_user(sqlite_mgr)
    await sqlite_mgr.clima.insert(
        project_id=project_id, dia="2026-05-14",
        condicao=CONDICAO_SOL, hora_inicio=None, hora_fim=None,
        criado_por=user_id,
    )
    await sqlite_mgr.clima.insert(
        project_id=project_id, dia="2026-05-14",
        condicao=CONDICAO_CHUVA, hora_inicio="09:00", hora_fim="11:30",
        criado_por=user_id,
    )
    rows = await sqlite_mgr.clima.list_for_dia(project_id, "2026-05-14")
    assert [r.condicao for r in rows] == [CONDICAO_SOL, CONDICAO_CHUVA]
    assert rows[1].hora_inicio == "09:00"


def test_clima_validar_condicao_rejeita_invalido() -> None:
    with pytest.raises(StorageError):
        validar_condicao("granizo")


@_async
async def test_efetivo_insert_e_total(sqlite_mgr: SQLiteManager) -> None:
    user_id, project_id = await _make_project_and_user(sqlite_mgr)
    pedreiro = await sqlite_mgr.funcoes.get_by_nome("Pedreiro")
    assert pedreiro is not None
    await sqlite_mgr.efetivo.insert(
        project_id=project_id, dia="2026-05-14",
        funcao_id=pedreiro.id, empresa_id=None, qtd=5,
        criado_por=user_id,
    )
    rows = await sqlite_mgr.efetivo.list_for_dia(project_id, "2026-05-14")
    assert len(rows) == 1
    assert rows[0].qtd == 5


@_async
async def test_efetivo_rejeita_qtd_negativa(sqlite_mgr: SQLiteManager) -> None:
    user_id, project_id = await _make_project_and_user(sqlite_mgr)
    pedreiro = await sqlite_mgr.funcoes.get_by_nome("Pedreiro")
    assert pedreiro is not None
    with pytest.raises(StorageError):
        await sqlite_mgr.efetivo.insert(
            project_id=project_id, dia="2026-05-14",
            funcao_id=pedreiro.id, empresa_id=None, qtd=0,
            criado_por=user_id,
        )


def test_atividade_normaliza_estado_aliases() -> None:
    assert normalizar_estado("concluída") == ESTADO_CONCLUIDA
    assert normalizar_estado("ok") == ESTADO_CONCLUIDA
    assert normalizar_estado("andamento") == ESTADO_EM_ANDAMENTO
    with pytest.raises(StorageError):
        normalizar_estado("inventado")


@_async
async def test_atividade_insert_e_list(sqlite_mgr: SQLiteManager) -> None:
    user_id, project_id = await _make_project_and_user(sqlite_mgr)
    await sqlite_mgr.atividades.insert(
        project_id=project_id, dia="2026-05-14",
        estado=ESTADO_EM_ANDAMENTO, descricao="Concretagem laje L4",
        criado_por=user_id,
    )
    rows = await sqlite_mgr.atividades.list_for_dia(project_id, "2026-05-14")
    assert len(rows) == 1
    assert rows[0].descricao == "Concretagem laje L4"
    assert rows[0].etapa_id is None


@_async
async def test_anotacao_publica_versus_privada(sqlite_mgr: SQLiteManager) -> None:
    """Anotação privada de outro dono NÃO aparece quando requester filtra."""
    dono, project_id = await _make_project_and_user(sqlite_mgr)
    # Cria um segundo user (não-dono) só pra checar filtro
    import aiosqlite
    async with aiosqlite.connect(sqlite_mgr._db_path) as conn:
        cur = await conn.execute(
            "INSERT INTO users (telegram_id, name, role, status, created_at, updated_at) "
            "VALUES (?, ?, 'worker', 'active', ?, ?)",
            (2002, "outro", now_iso(), now_iso()),
        )
        outro = int(cur.lastrowid or 0)
        await conn.commit()

    await sqlite_mgr.anotacoes.insert(
        project_id=project_id, dia="2026-05-14",
        texto="pública", criado_por=dono,
        visibilidade=VISIBILIDADE_PUBLICA,
    )
    await sqlite_mgr.anotacoes.insert(
        project_id=project_id, dia="2026-05-14",
        texto="só dono", criado_por=dono,
        visibilidade=VISIBILIDADE_PRIVADA,
    )

    # Outro user vê só a pública
    vistas = await sqlite_mgr.anotacoes.list_for_dia(
        project_id, "2026-05-14", requester_user_id=outro,
    )
    assert [a.texto for a in vistas] == ["pública"]

    # Dono vê as duas
    todas = await sqlite_mgr.anotacoes.list_for_dia(
        project_id, "2026-05-14", requester_user_id=dono,
    )
    assert [a.texto for a in todas] == ["pública", "só dono"]
