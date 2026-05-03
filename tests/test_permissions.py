from __future__ import annotations

import pytest

from core.permissions import (
    GLOBAL_ROLE_ADMIN,
    GLOBAL_ROLE_MEMBER,
    GLOBAL_ROLE_SUPERADMIN,
    PROJECT_ROLE_ADMIN,
    PROJECT_ROLE_CLIENT,
    PROJECT_ROLE_CO_RESPONSIBLE,
    PROJECT_ROLE_OPERATOR,
    can_create_project,
    can_invite_role,
    default_member_permissions,
    project_rank,
    project_role_implies_global_role,
)
from database.models import ProjectMember, User


def _user(role: str) -> User:
    return User(
        id=1, telegram_id=10, name="x", email=None,
        role=role, status="active", invited_by=None,
        created_at="", updated_at="",
    )


def _member(role: str, *, can_invite: bool = False) -> ProjectMember:
    return ProjectMember(
        project_id=1, user_id=1, role=role,
        can_approve_rdo=False, can_view_financial=False, can_invite=can_invite,
        joined_at="", invite_id=None,
    )


def test_can_create_project_only_admins() -> None:
    assert can_create_project(_user(GLOBAL_ROLE_SUPERADMIN))
    assert can_create_project(_user(GLOBAL_ROLE_ADMIN))
    assert not can_create_project(_user(GLOBAL_ROLE_MEMBER))


def test_superadmin_global_can_invite_anyone_anywhere() -> None:
    su = _user(GLOBAL_ROLE_SUPERADMIN)
    for r in (PROJECT_ROLE_ADMIN, PROJECT_ROLE_CO_RESPONSIBLE,
              PROJECT_ROLE_OPERATOR, PROJECT_ROLE_CLIENT):
        assert can_invite_role(su, None, r)


def test_user_without_project_member_cannot_invite() -> None:
    u = _user(GLOBAL_ROLE_ADMIN)
    assert not can_invite_role(u, None, PROJECT_ROLE_OPERATOR)


def test_project_admin_can_invite_anyone_in_project() -> None:
    inviter = _member(PROJECT_ROLE_ADMIN, can_invite=True)
    u = _user(GLOBAL_ROLE_ADMIN)
    for r in (PROJECT_ROLE_ADMIN, PROJECT_ROLE_CO_RESPONSIBLE,
              PROJECT_ROLE_OPERATOR, PROJECT_ROLE_CLIENT):
        assert can_invite_role(u, inviter, r)


def test_co_responsible_with_can_invite_only_invites_below() -> None:
    inviter = _member(PROJECT_ROLE_CO_RESPONSIBLE, can_invite=True)
    u = _user(GLOBAL_ROLE_MEMBER)

    assert can_invite_role(u, inviter, PROJECT_ROLE_OPERATOR)
    assert can_invite_role(u, inviter, PROJECT_ROLE_CLIENT)
    assert not can_invite_role(u, inviter, PROJECT_ROLE_CO_RESPONSIBLE)
    assert not can_invite_role(u, inviter, PROJECT_ROLE_ADMIN)


def test_co_responsible_without_flag_cannot_invite() -> None:
    inviter = _member(PROJECT_ROLE_CO_RESPONSIBLE, can_invite=False)
    u = _user(GLOBAL_ROLE_MEMBER)
    assert not can_invite_role(u, inviter, PROJECT_ROLE_OPERATOR)


def test_operator_cannot_invite_anyone() -> None:
    inviter = _member(PROJECT_ROLE_OPERATOR, can_invite=True)
    u = _user(GLOBAL_ROLE_MEMBER)
    for r in (PROJECT_ROLE_ADMIN, PROJECT_ROLE_CO_RESPONSIBLE,
              PROJECT_ROLE_OPERATOR, PROJECT_ROLE_CLIENT):
        assert not can_invite_role(u, inviter, r)


def test_invalid_target_role_rejected() -> None:
    u = _user(GLOBAL_ROLE_SUPERADMIN)
    assert not can_invite_role(u, None, "wizard")


def test_project_rank_orders_correctly() -> None:
    assert (
        project_rank(PROJECT_ROLE_ADMIN)
        > project_rank(PROJECT_ROLE_CO_RESPONSIBLE)
        > project_rank(PROJECT_ROLE_OPERATOR)
        > project_rank(PROJECT_ROLE_CLIENT)
    )


def test_project_rank_unknown_raises() -> None:
    with pytest.raises(ValueError):
        project_rank("wizard")


def test_admin_invite_implies_global_admin_elevation() -> None:
    assert project_role_implies_global_role(PROJECT_ROLE_ADMIN) == GLOBAL_ROLE_ADMIN
    assert project_role_implies_global_role(PROJECT_ROLE_CO_RESPONSIBLE) is None
    assert project_role_implies_global_role(PROJECT_ROLE_OPERATOR) is None
    assert project_role_implies_global_role(PROJECT_ROLE_CLIENT) is None


def test_default_permissions_per_role() -> None:
    admin = default_member_permissions(PROJECT_ROLE_ADMIN)
    assert admin == {"can_approve_rdo": True, "can_view_financial": True, "can_invite": True}

    co = default_member_permissions(PROJECT_ROLE_CO_RESPONSIBLE)
    assert co["can_invite"] is True
    assert co["can_approve_rdo"] is False

    op = default_member_permissions(PROJECT_ROLE_OPERATOR)
    assert all(v is False for v in op.values())
