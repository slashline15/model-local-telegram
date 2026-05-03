# tg/handlers_projects.py

"""Comandos de Obras, Membros e Convites."""

from __future__ import annotations

import uuid
from html import escape
from typing import TYPE_CHECKING

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core.logger import get_logger
from core.permissions import (
    PROJECT_ROLE_ADMIN,
    PROJECT_ROLE_CO_RESPONSIBLE,
    PROJECT_ROLE_OPERATOR,
    PROJECT_ROLE_CLIENT,
    PROJECT_ROLES,
    can_create_project,
    can_invite_role,
    default_member_permissions,
)
from core.uid import gen_uid
from tg.middleware import (
    get_bot_member,
    get_bot_project,
    get_bot_user,
    require_active_project,
    require_active_user,
)

if TYPE_CHECKING:
    from tg.bot import BotDependencies

log = get_logger(__name__)


def _deps(context: ContextTypes.DEFAULT_TYPE) -> "BotDependencies":
    return context.application.bot_data["deps"]  # type: ignore[no-any-return]


def _strip_uid(arg: str) -> str:
    """Aceita `/obra #ABCD1234` ou `/obra ABCD1234`."""
    return arg[1:] if arg.startswith("#") else arg


# ────────────────── /criar_obra <nome> ──────────────────

@require_active_user
async def cmd_criar_obra(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    assert msg is not None
    user = get_bot_user(context)

    if not can_create_project(user):
        await msg.reply_text(
            "⛔ Só superadmin/admin pode criar obras. "
            "Peça pra um admin te promover (recebendo um convite com role admin)."
        )
        return

    if not context.args:
        await msg.reply_text("Uso: `/criar_obra Nome da Obra`", parse_mode=ParseMode.MARKDOWN)
        return
    name = " ".join(context.args).strip()
    if not name:
        await msg.reply_text("Nome da obra não pode ser vazio.")
        return

    deps = _deps(context)
    proj = await deps.sqlite.projects.create(
        uid=gen_uid(), name=name, created_by=user.id,
    )
    # Já marca como obra ativa do criador (quality of life).
    await deps.sqlite.settings.set_current_project(user.telegram_id, proj.id)

    await msg.reply_text(
        f"✅ Obra criada: <b>{escape(proj.name)}</b>\n"
        f"Código: <code>#{escape(proj.uid)}</code>\n"
        f"Você é o admin. Use /invite pra trazer o time.",
        parse_mode=ParseMode.HTML,
    )


# ────────────────── /obras ──────────────────

@require_active_user
async def cmd_obras(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    assert msg is not None
    user = get_bot_user(context)
    deps = _deps(context)

    projects = await deps.sqlite.projects.list_for_user(user.id, status=None)
    if not projects:
        await msg.reply_text(
            "Você ainda não está em nenhuma obra. "
            "Crie com `/criar_obra <nome>` (se for admin) ou aguarde um convite."
        )
        return

    settings = await deps.sqlite.settings.get(user.telegram_id)
    cur_id = settings.current_project_id

    lines = ["<b>🏗 Suas obras</b>\n"]
    for p in projects:
        marker = "▶ " if p.id == cur_id else "  "
        member = await deps.sqlite.members.get(project_id=p.id, user_id=user.id)
        role = member.role if member else "—"
        admin_mark = " (admin)" if p.admin_id == user.id else ""
        lines.append(
            f"{marker}<code>#{escape(p.uid)}</code> · {escape(p.name)} "
            f"· papel: <i>{escape(role)}{admin_mark}</i>"
        )
    lines.append("\nDefina ativa: <code>/obra #UID</code>")

    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ────────────────── /obra (sem args = mostra; com #UID = define) ──────────────────

@require_active_user
async def cmd_obra(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    assert msg is not None
    user = get_bot_user(context)
    deps = _deps(context)

    if not context.args:
        settings = await deps.sqlite.settings.get(user.telegram_id)
        if settings.current_project_id is None:
            await msg.reply_text(
                "Sem obra ativa. Use `/obras` pra listar e `/obra #UID` pra escolher.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        proj = await deps.sqlite.projects.get_by_id(settings.current_project_id)
        if proj is None:
            await msg.reply_text("Obra ativa inválida — escolha outra com /obras.")
            return
        await msg.reply_text(
            f"Obra ativa: <b>{escape(proj.name)}</b> "
            f"<code>#{escape(proj.uid)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    target_uid = _strip_uid(context.args[0])
    proj = await deps.sqlite.projects.get_by_uid(target_uid)
    if proj is None:
        await msg.reply_text(f"Obra `#{target_uid}` não encontrada.", parse_mode=ParseMode.MARKDOWN)
        return

    member = await deps.sqlite.members.get(project_id=proj.id, user_id=user.id)
    is_super = user.role == "superadmin"
    if member is None and not is_super:
        await msg.reply_text("Você não é membro dessa obra.")
        return

    await deps.sqlite.settings.set_current_project(user.telegram_id, proj.id)
    await msg.reply_text(
        f"✅ Obra ativa agora: <b>{escape(proj.name)}</b> "
        f"<code>#{escape(proj.uid)}</code>",
        parse_mode=ParseMode.HTML,
    )


# ────────────────── /invite <role> ──────────────────

_ROLE_ALIASES: dict[str, str] = {
    "admin":           PROJECT_ROLE_ADMIN,
    "coresp":          PROJECT_ROLE_CO_RESPONSIBLE,
    "co_responsible":  PROJECT_ROLE_CO_RESPONSIBLE,
    "co":              PROJECT_ROLE_CO_RESPONSIBLE,
    "engenheiro":      PROJECT_ROLE_CO_RESPONSIBLE,
    "supervisor":      PROJECT_ROLE_CO_RESPONSIBLE,
    "operator":        PROJECT_ROLE_OPERATOR,
    "op":              PROJECT_ROLE_OPERATOR,
    "operacao":        PROJECT_ROLE_OPERATOR,
    "operação":        PROJECT_ROLE_OPERATOR,
    "worker":          PROJECT_ROLE_OPERATOR,
    "client":          PROJECT_ROLE_CLIENT,
    "cliente":         PROJECT_ROLE_CLIENT,
}


def _normalize_role(arg: str) -> str | None:
    key = arg.lower().lstrip("@").strip()
    if key in PROJECT_ROLES:
        return key
    return _ROLE_ALIASES.get(key)


@require_active_project
async def cmd_invite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    assert msg is not None
    user = get_bot_user(context)
    project = get_bot_project(context)
    member = get_bot_member(context)
    deps = _deps(context)

    if not context.args:
        await msg.reply_text(
            "Uso: `/invite <role>`\n"
            "Roles: `admin`, `co_responsible` (alias `engenheiro`), "
            "`operator` (alias `operacao`), `client`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    target_role = _normalize_role(context.args[0])
    if target_role is None:
        await msg.reply_text(f"Role desconhecido: `{context.args[0]}`", parse_mode=ParseMode.MARKDOWN)
        return

    if not can_invite_role(user, member, target_role):
        await msg.reply_text(
            f"⛔ Você não tem permissão pra convidar `{target_role}` "
            f"nesta obra.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    invite = await deps.sqlite.invites.create(
        uid=gen_uid(),
        token=uuid.uuid4().hex,
        role=target_role,
        created_by=user.id,
        project_id=project.id,
    )

    bot_username = context.bot.username
    deep_link = f"https://t.me/{bot_username}?start={invite.token}"
    await msg.reply_text(
        f"✅ Convite criado <code>#{escape(invite.uid)}</code> "
        f"({escape(target_role)}) para <b>{escape(project.name)}</b>.\n\n"
        f"Mande este link pro convidado:\n{deep_link}\n\n"
        f"<i>Convite é de uso único.</i>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


# ────────────────── /membros ──────────────────

@require_active_project
async def cmd_membros(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    assert msg is not None
    project = get_bot_project(context)
    deps = _deps(context)

    members = await deps.sqlite.members.list_for_project(project.id)
    if not members:
        await msg.reply_text("Sem membros nessa obra (estado inválido — reporta isso).")
        return

    lines = [f"<b>👥 Membros · {escape(project.name)}</b> "
             f"<code>#{escape(project.uid)}</code>\n"]
    for m in members:
        u = await deps.sqlite.users.get_by_id(m.user_id)
        name = escape(u.name) if u else f"user#{m.user_id}"
        flags = []
        if m.can_approve_rdo:    flags.append("aprova RDO")
        if m.can_view_financial: flags.append("vê $$$")
        if m.can_invite:         flags.append("convida")
        flag_str = f" · {', '.join(flags)}" if flags else ""
        lines.append(f"• {name} — <i>{escape(m.role)}</i>{flag_str}")

    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
