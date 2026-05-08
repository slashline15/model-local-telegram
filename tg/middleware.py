# tg/middleware.py

"""
Decorators que validam autorização antes de cada handler rodar.

- `require_active_user`: bloqueia quem não está cadastrado / inativo.
- `require_active_project`: também exige que o user tenha obra ativa.
- `require_project_admin`: exige que o user seja admin da obra ativa.

Resultado disponível em `context.user_data`:
- `bot_user`     → database.models.User
- `bot_project`  → database.models.Project (se require_active_project rodou)
- `bot_member`   → database.models.ProjectMember | None
"""

from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes

from core.logger import get_logger
from database.models import Project, ProjectMember, User

if TYPE_CHECKING:
    from tg.bot import BotDependencies

log = get_logger(__name__)

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[Any]]


def _deps(context: ContextTypes.DEFAULT_TYPE) -> "BotDependencies":
    return context.application.bot_data["deps"]  # type: ignore[no-any-return]


def get_bot_user(context: ContextTypes.DEFAULT_TYPE) -> User:
    """Para uso DENTRO de handlers já decorados com `require_active_user`."""
    user = (context.user_data or {}).get("bot_user")
    if not isinstance(user, User):
        raise RuntimeError(
            "get_bot_user chamado sem decorator require_active_user."
        )
    return user


def get_bot_project(context: ContextTypes.DEFAULT_TYPE) -> Project:
    proj = (context.user_data or {}).get("bot_project")
    if not isinstance(proj, Project):
        raise RuntimeError(
            "get_bot_project chamado sem decorator require_active_project."
        )
    return proj


def get_bot_member(context: ContextTypes.DEFAULT_TYPE) -> ProjectMember | None:
    """Vínculo do user com a obra ativa. None se ele só é superadmin global."""
    return (context.user_data or {}).get("bot_member")  # type: ignore[no-any-return]


def require_active_user(handler: Handler) -> Handler:
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Any:
        if update.effective_user is None or update.effective_message is None:
            return None
        deps = _deps(context)
        bot_user = await deps.sqlite.users.get_by_telegram_id(update.effective_user.id)
        if bot_user is None or bot_user.status != "active":
            await update.effective_message.reply_text(
                "⛔ Acesso não autorizado. Peça um convite a um admin "
                "(`/invite` deles) e use o link recebido."
            )
            return None
        if context.user_data is not None:
            context.user_data["bot_user"] = bot_user
        return await handler(update, context)

    return wrapper


def require_active_project(handler: Handler) -> Handler:
    @require_active_user
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Any:
        deps = _deps(context)
        bot_user = get_bot_user(context)
        settings = await deps.sqlite.settings.get(bot_user.telegram_id)
        if settings.current_project_id is None:
            await update.effective_message.reply_text(  # type: ignore[union-attr]
                "Você não tem obra ativa. Use `/obras` pra listar e "
                "`/obra #UID` pra escolher."
            )
            return None
        project = await deps.sqlite.projects.get_by_id(settings.current_project_id)
        if project is None:
            # Obra apagada/inválida — limpa silenciosamente.
            await deps.sqlite.settings.set_current_project(bot_user.telegram_id, None)
            await update.effective_message.reply_text(  # type: ignore[union-attr]
                "Sua obra ativa não existe mais. Escolha outra com `/obras`."
            )
            return None
        member = await deps.sqlite.members.get(
            project_id=project.id, user_id=bot_user.id
        )
        if member is None and bot_user.role != "superadmin":
            # Você não é mais membro mas a obra ainda está marcada como atual.
            await deps.sqlite.settings.set_current_project(bot_user.telegram_id, None)
            await update.effective_message.reply_text(  # type: ignore[union-attr]
                "Você não é mais membro dessa obra. Escolha outra com `/obras`."
            )
            return None
        if context.user_data is not None:
            context.user_data["bot_project"] = project
            context.user_data["bot_member"] = member
        return await handler(update, context)

    return wrapper


def require_project_admin(handler: Handler) -> Handler:
    @require_active_project
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Any:
        bot_user = get_bot_user(context)
        project = get_bot_project(context)
        if bot_user.role == "superadmin" or project.admin_id == bot_user.id:
            return await handler(update, context)
        await update.effective_message.reply_text(  # type: ignore[union-attr]
            "⛔ Só o admin da obra pode fazer isso."
        )
        return None

    return wrapper


def require_superadmin(handler: Handler) -> Handler:
    """Só permite acesso a usuários com role='superadmin'."""
    @require_active_user
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Any:
        bot_user = get_bot_user(context)
        if bot_user.role != "superadmin":
            await update.effective_message.reply_text(  # type: ignore[union-attr]
                "⛔ Só superadmin."
            )
            return None
        return await handler(update, context)

    return wrapper

