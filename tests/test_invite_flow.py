"""Fluxo end-to-end do consumo de convite, exercitando os repos diretamente.

O handler `_consume_invite` só cola I/O do Telegram em cima dessa lógica;
testar a coreografia dos repos cobre o que pode quebrar de verdade.
"""
from __future__ import annotations

import uuid

import pytest

from core.permissions import (
    PROJECT_ROLE_ADMIN,
    PROJECT_ROLE_OPERATOR,
    default_member_permissions,
    project_role_implies_global_role,
)
from core.uid import gen_uid
from database.sqlite_mgr import SQLiteManager


pytestmark = pytest.mark.asyncio


async def _make_project_with_admin(
    mgr: SQLiteManager, *, telegram_id: int = 1001
):
    admin = await mgr.users.register(
        telegram_id=telegram_id, name="Admin", role="admin",
    )
    proj = await mgr.projects.create(uid=gen_uid(), name="Obra 1", created_by=admin.id)
    return admin, proj


async def test_consume_invite_makes_user_an_active_member(
    sqlite_mgr: SQLiteManager,
) -> None:
    admin, proj = await _make_project_with_admin(sqlite_mgr)
    invite = await sqlite_mgr.invites.create(
        uid=gen_uid(), token=uuid.uuid4().hex,
        role=PROJECT_ROLE_OPERATOR, created_by=admin.id, project_id=proj.id,
    )

    invitee = await sqlite_mgr.users.register(telegram_id=2002, name="Pedreiro")
    consumed = await sqlite_mgr.invites.mark_used(invite.id, used_by=invitee.id)
    assert consumed

    perms = default_member_permissions(invite.role)
    member = await sqlite_mgr.members.add(
        project_id=proj.id, user_id=invitee.id,
        role=invite.role, invite_id=invite.id, **perms,
    )
    assert member.role == PROJECT_ROLE_OPERATOR
    assert member.can_invite is False

    # Convite operator NÃO eleva role global.
    assert project_role_implies_global_role(invite.role) is None
    after = await sqlite_mgr.users.get_by_id(invitee.id)
    assert after is not None and after.role == "worker"


async def test_admin_invite_transfers_project_admin_and_elevates_global_role(
    sqlite_mgr: SQLiteManager,
) -> None:
    admin, proj = await _make_project_with_admin(sqlite_mgr, telegram_id=3001)
    new_admin = await sqlite_mgr.users.register(telegram_id=3002, name="Engenheiro")
    # Garante que nasceu como 'worker' (default).
    assert new_admin.role == "worker"

    invite = await sqlite_mgr.invites.create(
        uid=gen_uid(), token=uuid.uuid4().hex,
        role=PROJECT_ROLE_ADMIN, created_by=admin.id, project_id=proj.id,
    )

    consumed = await sqlite_mgr.invites.mark_used(invite.id, used_by=new_admin.id)
    assert consumed
    perms = default_member_permissions(invite.role)
    await sqlite_mgr.members.add(
        project_id=proj.id, user_id=new_admin.id,
        role=invite.role, invite_id=invite.id, **perms,
    )
    # Lógica do handler: convite admin transfere admin_id da obra
    await sqlite_mgr.projects.set_admin(proj.id, new_admin.id)

    p2 = await sqlite_mgr.projects.get_by_id(proj.id)
    assert p2 is not None and p2.admin_id == new_admin.id

    # Lógica do handler: eleva role global se vinha de 'member'.
    elevation = project_role_implies_global_role(invite.role)
    if elevation:
        await sqlite_mgr.users.update_role(new_admin.id, elevation)

    after = await sqlite_mgr.users.get_by_id(new_admin.id)
    assert after is not None and after.role == "admin"


async def test_invite_cannot_be_consumed_twice(sqlite_mgr: SQLiteManager) -> None:
    admin, proj = await _make_project_with_admin(sqlite_mgr, telegram_id=4001)
    invite = await sqlite_mgr.invites.create(
        uid=gen_uid(), token=uuid.uuid4().hex,
        role=PROJECT_ROLE_OPERATOR, created_by=admin.id, project_id=proj.id,
    )

    a = await sqlite_mgr.users.register(telegram_id=4002, name="A")
    b = await sqlite_mgr.users.register(telegram_id=4003, name="B")

    assert await sqlite_mgr.invites.mark_used(invite.id, used_by=a.id) is True
    assert await sqlite_mgr.invites.mark_used(invite.id, used_by=b.id) is False

    refreshed = await sqlite_mgr.invites.get_by_id(invite.id)
    assert refreshed is not None and refreshed.used_by == a.id


async def test_interaction_records_project_id(sqlite_mgr: SQLiteManager) -> None:
    admin, proj = await _make_project_with_admin(sqlite_mgr, telegram_id=5001)

    iid = await sqlite_mgr.insert_interaction(
        user_id=admin.telegram_id, chat_id=10,
        user_message="oi", bot_response="olá",
        tags=[], intent=None,
        model_used=None, temperature=None,
        prompt_tokens=None, response_tokens=None, total_duration_ms=None,
        prompt_used=None, positive_ids=[], negative_ids=[],
        retrieved_count=None, embedding_model=None, embedding_dim=None,
        tool_calls=[], media_path=None, media_type=None,
        error=None, run_id=None,
        project_id=proj.id,
    )
    rows = await sqlite_mgr.fetch_by_ids([iid], requester_user_id=None)
    assert rows and rows[0].id == iid
    # `project_id` ainda não está no dataclass Interaction; checagem direta no DB.
    import sqlite3
    conn = sqlite3.connect(sqlite_mgr._db_path)
    pid = conn.execute(
        "SELECT project_id FROM interactions WHERE id = ?", (iid,)
    ).fetchone()[0]
    conn.close()
    assert pid == proj.id
