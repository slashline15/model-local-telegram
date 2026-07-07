# tg/kb.py
# Fábrica central de InlineKeyboardMarkup — evita criar teclados inline dispersos.

from __future__ import annotations

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


def rating_keyboard(interaction_id: int) -> InlineKeyboardMarkup:
    """Teclado binário 👍 Bom / 👎 Ruim após resposta da IA."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("👍 Bom",  callback_data=f"rate:{interaction_id}:5"),
        InlineKeyboardButton("👎 Ruim", callback_data=f"rate:{interaction_id}:1"),
    ]])


def feedback_comment_keyboard(interaction_id: int) -> InlineKeyboardMarkup:
    """Pergunta se quer deixar comentário sobre a resposta ruim."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✏️ Comentar agora", callback_data=f"fb:comment:{interaction_id}"),
        InlineKeyboardButton("Pular",              callback_data=f"fb:skip:{interaction_id}"),
    ]])


def awaiting_correction_keyboard(interaction_id: int) -> InlineKeyboardMarkup:
    """Botão de cancelamento enquanto aguarda o comentário de correção."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Cancelar comentário", callback_data=f"fb:cancel:{interaction_id}"),
    ]])


def main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Teclado persistente com atalho para o menu RDO."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📋 Menu")]],
        resize_keyboard=True,
        is_persistent=True,
    )


def rdo_menu_keyboard() -> InlineKeyboardMarkup:
    """Menu principal do RDO do dia."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("☀️ Clima",     callback_data="rdo:menu:clima"),
            InlineKeyboardButton("👷 Efetivo",   callback_data="rdo:menu:efetivo"),
        ],
        [
            InlineKeyboardButton("⚒️ Atividade", callback_data="rdo:menu:atividade"),
            InlineKeyboardButton("📝 Anotação",  callback_data="rdo:menu:anotacao"),
        ],
        [
            InlineKeyboardButton("📊 Ver RDO",   callback_data="rdo:menu:ver"),
            InlineKeyboardButton("📅 Cronograma",callback_data="rdo:menu:cronograma"),
        ],
    ])


def clima_keyboard() -> InlineKeyboardMarkup:
    """Botões de condição climática."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("☀️ Claro",   callback_data="rdo:clima:sol"),
        InlineKeyboardButton("⛅ Nublado", callback_data="rdo:clima:nublado"),
        InlineKeyboardButton("🌧️ Chuva",  callback_data="rdo:clima:chuva"),
        InlineKeyboardButton("🌫️ Névoa",  callback_data="rdo:clima:nevoa"),
    ]])


def confirm_rdo_keyboard(
    confirm_data: str, skip_data: str, count: int = 1
) -> InlineKeyboardMarkup:
    """Confirmação de registro(s) RDO extraído(s) de foto/texto."""
    label = f"✅ Registrar ({count})" if count > 1 else "✅ Registrar"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(label,          callback_data=confirm_data),
        InlineKeyboardButton("❌ Ignorar",   callback_data=skip_data),
    ]])


def doc_confirm_keyboard() -> InlineKeyboardMarkup:
    """Confirmação de indexação de documento sensível (/doc)."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Indexar",  callback_data="doc:confirm"),
        InlineKeyboardButton("❌ Cancelar", callback_data="doc:cancel"),
    ]])


def obras_keyboard(
    projects: list,  # list[Project] — evita import circular
    active_id: int | None,
) -> InlineKeyboardMarkup:
    """Lista de obras com botão de seleção por UID."""
    buttons = []
    for p in projects:
        label = ("✅ " if p.id == active_id else "▶ ") + p.name
        buttons.append([InlineKeyboardButton(label, callback_data=f"obra:set:{p.uid}")])
    return InlineKeyboardMarkup(buttons)
