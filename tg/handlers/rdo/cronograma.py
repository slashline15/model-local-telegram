# tg/handlers/rdo/cronograma.py
# Handler para o sub-fluxo de nova etapa do cronograma (via awaiting_cronograma_etapa).

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core.logger import get_logger
from tg.middleware import get_bot_project, get_bot_user, require_active_project

if TYPE_CHECKING:
    from tg.bot import BotDependencies

log = get_logger(__name__)


def _deps(context: ContextTypes.DEFAULT_TYPE) -> "BotDependencies":
    return context.application.bot_data["deps"]  # type: ignore[no-any-return]


@require_active_project
async def cmd_cronograma(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/cronograma — lista etapas da obra ativa."""
    msg = update.effective_message
    assert msg is not None
    deps = _deps(context)
    project = get_bot_project(context)

    etapas = await deps.sqlite.cronograma.list_for_project(project.id)
    if not etapas:
        lines = [f"<b>📅 Cronograma — {project.name}</b>", "", "Nenhuma etapa cadastrada."]
    else:
        lines = [f"<b>📅 Cronograma — {project.name}</b>", ""]
        for i, e in enumerate(etapas, 1):
            data = f" → {e.data_prevista_termino}" if e.data_prevista_termino else ""
            lines.append(f"  {i}. {e.etapa}{data}")

    buttons = [[InlineKeyboardButton("➕ Nova etapa", callback_data="rdo:cronograma:nova")]]
    await msg.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def handle_nova_etapa_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Callback 'rdo:cronograma:nova' — inicia fluxo de criação de etapa."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    context.user_data["awaiting_cronograma"] = {"step": "nome"}
    try:
        await query.edit_message_text("Digite o nome da nova etapa:")
    except Exception:
        if query.message is not None:
            await query.message.reply_text("Digite o nome da nova etapa:")


async def handle_cronograma_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> bool:
    """
    Intercepta mensagens de texto para o fluxo multi-step de criação de etapa.
    Retorna True se a mensagem foi consumida; False caso contrário.
    """
    state = context.user_data.get("awaiting_cronograma")
    if state is None:
        return False

    msg = update.effective_message
    assert msg is not None
    deps = _deps(context)

    step = state.get("step")

    if step == "nome":
        state["nome"] = text.strip()
        state["step"] = "data"
        await msg.reply_text(
            "Data prevista de término? (YYYY-MM-DD ou 'pular')"
        )
        return True

    if step == "data":
        data_fim = None if text.lower() in ("pular", "nao", "não", "-") else text.strip()
        context.user_data.pop("awaiting_cronograma", None)

        try:
            project = get_bot_project(context)
            user = get_bot_user(context)
        except Exception:
            await msg.reply_text("Obra ativa não encontrada.")
            return True

        try:
            import uuid as _uuid
            from datetime import datetime
            await deps.sqlite.cronograma.create(
                uid=str(_uuid.uuid4()),
                project_id=project.id,
                parent_id=None,
                etapa=state["nome"],
                descricao=None,
                data_prevista_inicio=None,
                data_prevista_termino=data_fim,
                ordem=0,
            )
            await msg.reply_text(
                f"✅ Etapa <b>{state['nome']}</b> criada.",
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            log.exception("Falha ao criar etapa")
            await msg.reply_text(f"Erro ao criar etapa: {exc}")
        return True

    return False
