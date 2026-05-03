from __future__ import annotations

import pytest

from core.uid import gen_uid
from database.sqlite_mgr import SQLiteManager


pytestmark = pytest.mark.asyncio


async def test_project_create_and_lookup(sqlite_mgr: SQLiteManager) -> None:
    creator = await sqlite_mgr.users.register(telegram_id=111, name="Daniel", role="admin")

    uid = gen_uid()
    proj = await sqlite_mgr.projects.create(
        uid=uid,
        name="Reforma Igreja",
        created_by=creator.id,
        address="Rua X, 123",
        type="reforma",
    )
    assert proj.id > 0
    assert proj.uid == uid
    assert proj.status == "active"

    by_uid = await sqlite_mgr.projects.get_by_uid(uid)
    assert by_uid is not None and by_uid.id == proj.id


async def test_invite_single_use_flow(sqlite_mgr: SQLiteManager) -> None:
    creator = await sqlite_mgr.users.register(telegram_id=222, name="Eng", role="admin")
    proj = await sqlite_mgr.projects.create(
        uid=gen_uid(), name="Obra X", created_by=creator.id,
    )

    inv = await sqlite_mgr.invites.create(
        uid=gen_uid(), token="tok-abc-123",
        role="worker", created_by=creator.id, project_id=proj.id,
    )
    assert inv.used_at is None

    fetched = await sqlite_mgr.invites.get_by_token("tok-abc-123")
    assert fetched is not None and fetched.id == inv.id

    invitee = await sqlite_mgr.users.register(telegram_id=333, name="Worker")

    consumed = await sqlite_mgr.invites.mark_used(inv.id, used_by=invitee.id)
    assert consumed is True

    # segundo uso é rejeitado (atomic).
    consumed_again = await sqlite_mgr.invites.mark_used(inv.id, used_by=invitee.id)
    assert consumed_again is False


async def test_member_add_and_list(sqlite_mgr: SQLiteManager) -> None:
    creator = await sqlite_mgr.users.register(telegram_id=444, name="A", role="admin")
    worker = await sqlite_mgr.users.register(telegram_id=555, name="B")
    proj = await sqlite_mgr.projects.create(
        uid=gen_uid(), name="P", created_by=creator.id,
    )

    m = await sqlite_mgr.members.add(
        project_id=proj.id, user_id=worker.id,
        role="encarregado", can_approve_rdo=True, can_invite=True,
    )
    assert m.can_approve_rdo is True
    assert m.can_invite is True
    assert m.can_view_financial is False

    fetched = await sqlite_mgr.members.get(project_id=proj.id, user_id=worker.id)
    assert fetched is not None and fetched.role == "encarregado"

    by_user = await sqlite_mgr.members.list_for_user(worker.id)
    assert len(by_user) == 1 and by_user[0].project_id == proj.id


async def test_list_projects_for_user_includes_creator_and_member(
    sqlite_mgr: SQLiteManager,
) -> None:
    owner = await sqlite_mgr.users.register(telegram_id=601, name="Owner", role="admin")
    other = await sqlite_mgr.users.register(telegram_id=602, name="Other", role="admin")
    worker = await sqlite_mgr.users.register(telegram_id=603, name="Worker")

    p1 = await sqlite_mgr.projects.create(uid=gen_uid(), name="Owned", created_by=owner.id)
    p2 = await sqlite_mgr.projects.create(uid=gen_uid(), name="External", created_by=other.id)
    await sqlite_mgr.members.add(project_id=p2.id, user_id=worker.id, role="worker")
    # projeto que o worker NÃO é membro nem dono
    await sqlite_mgr.projects.create(uid=gen_uid(), name="Hidden", created_by=other.id)

    owner_projects = await sqlite_mgr.projects.list_for_user(owner.id)
    assert {p.id for p in owner_projects} == {p1.id}

    worker_projects = await sqlite_mgr.projects.list_for_user(worker.id)
    assert {p.id for p in worker_projects} == {p2.id}


async def test_member_upsert_overwrites_permissions(sqlite_mgr: SQLiteManager) -> None:
    creator = await sqlite_mgr.users.register(telegram_id=701, name="A", role="admin")
    user = await sqlite_mgr.users.register(telegram_id=702, name="B")
    proj = await sqlite_mgr.projects.create(uid=gen_uid(), name="P", created_by=creator.id)

    await sqlite_mgr.members.add(
        project_id=proj.id, user_id=user.id, role="worker",
    )
    await sqlite_mgr.members.add(
        project_id=proj.id, user_id=user.id,
        role="supervisor", can_approve_rdo=True,
    )

    # Após create() o admin já é membro automático; só validamos o user inserido.
    user_member = await sqlite_mgr.members.get(project_id=proj.id, user_id=user.id)
    assert user_member is not None
    assert user_member.role == "supervisor"
    assert user_member.can_approve_rdo is True


async def test_create_project_auto_adds_admin_as_member(
    sqlite_mgr: SQLiteManager,
) -> None:
    creator = await sqlite_mgr.users.register(telegram_id=801, name="Admin")
    proj = await sqlite_mgr.projects.create(
        uid=gen_uid(), name="Obra", created_by=creator.id,
    )

    assert proj.admin_id == creator.id

    members = await sqlite_mgr.members.list_for_project(proj.id)
    assert len(members) == 1
    m = members[0]
    assert m.user_id == creator.id
    assert m.role == "admin"
    assert m.can_approve_rdo and m.can_view_financial and m.can_invite


async def test_set_current_project_persists(sqlite_mgr: SQLiteManager) -> None:
    creator = await sqlite_mgr.users.register(telegram_id=901, name="A")
    proj = await sqlite_mgr.projects.create(uid=gen_uid(), name="P", created_by=creator.id)

    await sqlite_mgr.settings.set_current_project(creator.id, proj.id)
    s = await sqlite_mgr.settings.get(creator.id)
    assert s.current_project_id == proj.id

    await sqlite_mgr.settings.set_current_project(creator.id, None)
    s = await sqlite_mgr.settings.get(creator.id)
    assert s.current_project_id is None
