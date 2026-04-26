from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import CallbackQuery, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from core.logger import get_logger

if TYPE_CHECKING:
    from tg.bot import BotDependencies

log = get_logger(__name__)


def _deps(context: ContextTypes.DEFAULT_TYPE) -> "BotDependencies":
    return context.application.bot_data["deps"]  # type: ignore[no-any-return]


async def _safe_clear_keyboard(query: CallbackQuery) -> None:
    """Remove o teclado inline; ignora `Message is not modified` (clique repetido)."""
    try:
        await _safe_clear_keyboard(query)
    except BadRequest as exc:
        if "not modified" in str(exc).lower():
            return
        log.warning("Falha ao limpar teclado: %s", exc)


async def on_rate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback dos botões 'rate:<id>:<score>'."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()

    try:
        _, raw_id, raw_score = query.data.split(":", 2)
        interaction_id = int(raw_id)
        score = int(raw_score)
    except (ValueError, IndexError):
        log.warning("callback_data inválido: %r", query.data)
        return

    deps = _deps(context)
    try:
        await deps.sqlite.update_score(interaction_id, score)
    except Exception as exc:  # noqa: BLE001
        log.exception("Falha ao salvar score")
        await _safe_clear_keyboard(query)
        if query.message is not None:
            await query.message.reply_text(f"Erro ao salvar avaliação: {exc}")
        return

    await query.edit_message_reply_markup(reply_markup=None)
    if query.message is not None:
        await query.message.reply_text(
            f"Avaliação registrada: {'⭐' * score} ({score}/5)."
        )


async def on_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback dos botões 'cfg:model:<name>' e 'cfg:temp:<float>'."""
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return
    await query.answer()

    parts = query.data.split(":", 2)
    if len(parts) != 3:
        return
    _, kind, value = parts

    deps = _deps(context)
    user_id = update.effective_user.id

    try:
        if kind == "model":
            await deps.sqlite.set_user_model(user_id, value)
            note = f"Modelo definido: <b>{value}</b>"
        elif kind == "temp":
            await deps.sqlite.set_user_temperature(user_id, float(value))
            note = f"Temperatura definida: <b>{value}</b>"
        else:
            return
    except Exception as exc:  # noqa: BLE001
        log.exception("Falha ao atualizar config")
        if query.message is not None:
            await query.message.reply_text(f"Erro ao salvar configuração: {exc}")
        return

    if query.message is not None:
        await query.message.reply_text(note, parse_mode="HTML")
