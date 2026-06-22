from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core.codes import format_hashtag, parse_code
from core.logger import get_logger
from llm.contrastive_rag import RagBundle
from tg.middleware import require_active_user

if TYPE_CHECKING:
    from tg.bot import BotDependencies

log = get_logger(__name__)


def _deps(context: ContextTypes.DEFAULT_TYPE) -> "BotDependencies":
    return context.application.bot_data["deps"]  # type: ignore[no-any-return]


async def _show_interaction_by_code(
    msg: Message,
    deps: "BotDependencies",
    interaction_id: int,
    *,
    user_id: int,
) -> None:
    rows = await deps.sqlite.fetch_by_ids(
        [interaction_id], requester_user_id=user_id
    )
    if not rows:
        # Pode ser inexistente OU privada de outro usuário — resposta uniforme
        # para não vazar a existência de mensagens alheias.
        await msg.reply_text(f"Não encontrei {format_hashtag(interaction_id)}.")
        return
    row = rows[0]

    score = "—" if row.score is None else str(row.score)
    intent = row.intent or "—"
    tags = ", ".join(row.tags) if row.tags else "—"
    user_msg = (row.user_message or "").strip()
    bot_msg = (row.bot_response or "").strip()

    lines = [
        f"<b>📌 {escape(format_hashtag(interaction_id))}</b>  "
        f"<i>(score={score}, intent={escape(intent)})</i>",
        f"<b>tags:</b> {escape(tags)}",
        f"<b>quando:</b> {escape(row.timestamp or '—')}",
        "",
        f"<b>Você:</b>\n<code>{escape(user_msg[:1500])}</code>",
        "",
        f"<b>Bot:</b>\n<code>{escape(bot_msg[:1500])}</code>",
    ]
    if row.positive_ids:
        lines.append(
            "<b>baseado em (positivos):</b> "
            + " ".join(format_hashtag(i) for i in row.positive_ids)
        )
    if row.negative_ids:
        lines.append(
            "<b>contra-exemplos:</b> "
            + " ".join(format_hashtag(i) for i in row.negative_ids)
        )
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@require_active_user
async def cmd_recall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_user is None:
        return
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Uso:\n"
            "  /recall <texto>   – top hits do RAG\n"
            "  /recall #i<id>    – abre a interação pelo código"
        )
        return

    deps = _deps(context)
    query = " ".join(args).strip()

    # Atalho: /recall #i42 → abre a interação pelo código
    if len(args) == 1:
        target_id = parse_code(args[0])
        if target_id is not None:
            await _show_interaction_by_code(
                update.effective_message, deps, target_id, user_id=update.effective_user.id
            )
            return

    try:
        bundle: RagBundle = await deps.rag.debug_recall(
            query, user_id=update.effective_user.id
        )
    except Exception as exc:  # noqa: BLE001
        await update.effective_message.reply_text(f"Erro no recall: {exc}")
        return

    if not bundle.hits:
        await update.effective_message.reply_text(
            "Nenhum hit. FAISS provavelmente está vazio — converse mais para popular."
        )
        return

    # Carrega o conteúdo dos hits para mostrar um trecho de cada um.
    hit_ids = [h.interaction_id for h in bundle.hits[:15]]
    rows = await deps.sqlite.fetch_by_ids(
        hit_ids, requester_user_id=update.effective_user.id
    )
    by_id = {r.id: r for r in rows}

    bucket_icon = {"positive": "🟢", "negative": "⭕", "neutral": "🔵"}
    lines: list[str] = [
        f"<b>🔎 Recall</b> (dim={bundle.embedding_dim}\n, "
        f"fallback={'sim' if bundle.fallback_used else 'não'})\n",
    ]
    for h in bundle.hits[:15]:
        score_repr = "—" if h.score is None else str(h.score)
        icon = bucket_icon.get(h.bucket, "·")
        row = by_id.get(h.interaction_id)
        snippet = ""
        if row is not None:
            raw = (row.user_message or "").replace("\n", " ").strip()
            snippet = escape(raw[:100] + ("…" if len(raw) > 100 else ""))
        lines.append(
            f"{icon} <b>{escape(format_hashtag(h.interaction_id))}</b> "
            f"sim={h.similarity:.3f} score={score_repr} <i>[{h.bucket}]</i>"
        )
        if snippet:
            lines.append(f"   ↪ <i>{snippet}</i>")
    if bundle.positive_ids:
        lines.append(f"\n<b>positivos</b>: {bundle.positive_ids}")
    if bundle.negative_ids:
        lines.append(f"<b>negativos</b>: {bundle.negative_ids}")
    if bundle.neutral_ids:
        lines.append(f"<b>neutros (fallback)</b>: {bundle.neutral_ids}")

    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML
    )


@require_active_user
async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_user is None:
        return
    deps = _deps(context)
    user_id = update.effective_user.id

    n = 5
    if context.args:
        try:
            n = max(1, min(20, int(context.args[0])))
        except ValueError:
            pass

    rows = await deps.sqlite.list_user_history(user_id, limit=n)
    if not rows:
        await update.effective_message.reply_text("Sem histórico ainda.")
        return

    lines = [f"<b>🗂 Últimas {len(rows)} interações</b>"]
    for r in rows:
        score = "—" if r.score is None else str(r.score)
        intent = r.intent or "—"
        snippet = (r.user_message or "").replace("\n", " ")[:60]
        lines.append(
            f"• {escape(format_hashtag(r.id))}  score={score}  "
            f"intent={escape(intent)}  model={escape(r.model_used or '—')}\n"
            f"   ↪ <i>{escape(snippet)}</i>"
        )
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@require_active_user
async def cmd_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_user is None:
        return
    deps = _deps(context)
    rows = await deps.sqlite.list_user_reminders(
        update.effective_user.id, only_pending=True, limit=20
    )
    if not rows:
        await update.effective_message.reply_text(
            "Sem lembretes pendentes. Eu mesmo posso agendar quando algo "
            "merece follow-up — é só conversar normalmente."
        )
        return

    lines: list[str] = ["<b>⏰ Lembretes pendentes</b>"]
    buttons: list[list[InlineKeyboardButton]] = []
    for r in rows:
        snippet = (r.text or "").replace("\n", " ")
        if len(snippet) > 80:
            snippet = snippet[:79] + "…"
        lines.append(
            f"\n• <code>#{r.id}</code> — {escape(r.scheduled_for)}\n"
            f"  ↪ <i>{escape(snippet)}</i>"
        )
        buttons.append([
            InlineKeyboardButton(
                f"❌ cancelar #{r.id}", callback_data=f"rem:cancel:{r.id}"
            )
        ])
    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
    )


@require_active_user
async def cmd_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_user is None:
        return
    deps = _deps(context)
    user_id = update.effective_user.id

    settings = await deps.sqlite.get_user_settings(user_id)
    try:
        models = await deps.ollama.list_models()
    except Exception as exc:  # noqa: BLE001
        log.warning("Falha ao listar modelos do Ollama: %s", exc)
        models = []
    if not models:
        models = [settings.current_model]

    model_buttons: list[list[InlineKeyboardButton]] = []
    for m in models[:12]:
        marker = "✅ " if m == settings.current_model else ""
        model_buttons.append(
            [InlineKeyboardButton(f"{marker}{m}", callback_data=f"cfg:model:{m}")]
        )

    temps = (0.3, 0.7, 1.0)
    temp_row = [
        InlineKeyboardButton(
            f"{'✅ ' if abs(settings.temperature - t) < 1e-6 else ''}temp {t}",
            callback_data=f"cfg:temp:{t}",
        )
        for t in temps
    ]

    keyboard = InlineKeyboardMarkup(model_buttons + [temp_row])
    await update.effective_message.reply_text(
        f"Configuração atual:\n• Modelo: <b>{escape(settings.current_model)}</b>\n"
        f"• Temperatura: <b>{settings.temperature}</b>\n\nEscolha:",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )
