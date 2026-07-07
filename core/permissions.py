# core/permissions.py

"""
Hierarquia de roles e regras de quem pode convidar quem.

Dois eixos de role:
- `users.role` (global): superadmin | admin | member
  Quem pode CRIAR uma obra. Convite com role='admin' eleva 'member' → 'admin'.
- `project_members.role` (por obra): admin | co_responsible | operator | client
  Quem manda DENTRO de uma obra específica. Cada obra tem exatamente 1 admin
  (replicado em `projects.admin_id`).

Regra de convite (dentro de uma obra):
- admin da obra → pode convidar qualquer role
- co_responsible com can_invite=True → convida operator/client (abaixo dele)
- demais → não convidam
"""

from __future__ import annotations

from database.models import ProjectMember, User

# Roles globais de usuário (`users.role`).
GLOBAL_ROLE_SUPERADMIN: str = "superadmin"
GLOBAL_ROLE_ADMIN: str = "admin"
GLOBAL_ROLE_MEMBER: str = "member"

GLOBAL_ROLES: frozenset[str] = frozenset({
    GLOBAL_ROLE_SUPERADMIN, GLOBAL_ROLE_ADMIN, GLOBAL_ROLE_MEMBER,
})

# Roles por obra (`project_members.role`).
PROJECT_ROLE_ADMIN: str = "admin"
PROJECT_ROLE_CO_RESPONSIBLE: str = "co_responsible"
PROJECT_ROLE_OPERATOR: str = "operator"
PROJECT_ROLE_CLIENT: str = "client"

PROJECT_ROLES: frozenset[str] = frozenset({
    PROJECT_ROLE_ADMIN, PROJECT_ROLE_CO_RESPONSIBLE,
    PROJECT_ROLE_OPERATOR, PROJECT_ROLE_CLIENT,
})

# Maior número = maior poder. Usado para comparações "abaixo de mim".
_PROJECT_RANK: dict[str, int] = {
    PROJECT_ROLE_ADMIN: 100,
    PROJECT_ROLE_CO_RESPONSIBLE: 50,
    PROJECT_ROLE_OPERATOR: 10,
    PROJECT_ROLE_CLIENT: 0,
}


def project_rank(role: str) -> int:
    """Posição na hierarquia interna da obra (maior = mais poder)."""
    if role not in _PROJECT_RANK:
        raise ValueError(f"Role de obra desconhecido: {role!r}")
    return _PROJECT_RANK[role]


def can_create_project(user: User) -> bool:
    """Apenas superadmin e admin global criam obras."""
    return user.role in (GLOBAL_ROLE_SUPERADMIN, GLOBAL_ROLE_ADMIN)


def can_invite_role(
    inviter_user: User,
    inviter_member: ProjectMember | None,
    target_role: str,
) -> bool:
    """Quem pode convidar quem para a obra atual.

    `inviter_member` é o vínculo do inviter com a obra (None se ele só é
    superadmin sem ser membro formal).
    """
    if target_role not in PROJECT_ROLES:
        return False

    # Superadmin global passa por cima de tudo.
    if inviter_user.role == GLOBAL_ROLE_SUPERADMIN:
        return True

    # Sem vínculo com a obra ⇒ não convida.
    if inviter_member is None:
        return False

    # Admin da obra convida qualquer role.
    if inviter_member.role == PROJECT_ROLE_ADMIN:
        return True

    # Co-responsável só convida quem está abaixo dele e precisa de can_invite.
    if inviter_member.role == PROJECT_ROLE_CO_RESPONSIBLE and inviter_member.can_invite:
        return project_rank(target_role) < project_rank(PROJECT_ROLE_CO_RESPONSIBLE)

    return False


def project_role_implies_global_role(project_role: str) -> str | None:
    """Convite com role de obra que requer elevação do role global do convidado.

    Apenas convite de admin de obra eleva 'member' → 'admin' (pra permitir que
    a pessoa crie outras obras no futuro). Demais não mexem no role global.
    """
    if project_role == PROJECT_ROLE_ADMIN:
        return GLOBAL_ROLE_ADMIN
    return None


# Nível de acesso N1/N2/N3 dentro da obra (convenção da refundação 2026-05:
# 1=N1 admin, 2=N2 co-responsável, 3=N3 operacional). Comparação <= libera.
_PROJECT_LEVEL: dict[str, int] = {
    PROJECT_ROLE_ADMIN: 1,
    PROJECT_ROLE_CO_RESPONSIBLE: 2,
    PROJECT_ROLE_OPERATOR: 3,
    PROJECT_ROLE_CLIENT: 3,
}


def user_level_in_project(user: User, member: ProjectMember | None) -> int:
    """Nível N1/N2/N3 do usuário na obra. Superadmin global = N1 sempre."""
    if user.role == GLOBAL_ROLE_SUPERADMIN:
        return 1
    if member is None:
        return 3
    return _PROJECT_LEVEL.get(member.role, 3)


def default_member_permissions(role: str) -> dict[str, bool]:
    """Permissões padrão ao adicionar um membro com determinado role."""
    if role == PROJECT_ROLE_ADMIN:
        return {"can_approve_rdo": True, "can_view_financial": True, "can_invite": True}
    if role == PROJECT_ROLE_CO_RESPONSIBLE:
        return {"can_approve_rdo": False, "can_view_financial": False, "can_invite": True}
    return {"can_approve_rdo": False, "can_view_financial": False, "can_invite": False}
