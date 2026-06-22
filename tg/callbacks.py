# tg/callbacks.py

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from telegram import CallbackQuery, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from core.logger import get_logger
from tg.kb import (
    clima_keyboard,
    confirm_rdo_keyboard,
    feedback_comment_keyboard,
    rdo_menu_keyboard,
)

if TYPE_CHECKING:
    from tg.bot import BotDependencies

log = get_logger(__name__)


def _deps(context: ContextTypes.DEFAULT_TYPE) -> "BotDependencies":
    return context.application.bot_data["deps"]  # type: ignore[no-any-return]


async def _safe_clear_keyboard(query: CallbackQuery) -> None:
    """Remove o teclado inline; ignora `Message is not modified` (clique repetido)."""
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except BadRequest as exc:
        if "not modified" in str(exc).lower():
            return
        log.warning("Falha ao limpar teclado: %s", exc)


async def _safe_edit_text(query: CallbackQuery, text: str, **kwargs) -> None:
    try:
        await query.edit_message_text(text, **kwargs)
    except BadRequest as exc:
        if "not modified" in str(exc).lower():
            return
        log.warning("Falha ao editar mensagem: %s", exc)


# ──────────────────────────────────────────────────────────────────────────────
# Feedback binário (👍 / 👎)
# ──────────────────────────────────────────────────────────────────────────────

async def on_rate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback 'rate:<id>:<score>'. Score 5=bom, 1=ruim."""
    query = update.callback_query
    if query is None or query.data is None:
        return

    try:
        _, raw_id, raw_score = query.data.split(":", 2)
        interaction_id = int(raw_id)
        score = int(raw_score)
    except (ValueError, IndexError):
        log.warning("callback_data inválido: %r", query.data)
        await query.answer()
        return

    deps = _deps(context)
    try:
        await deps.sqlite.update_score(interaction_id, score)
    except Exception as exc:  # noqa: BLE001
        log.exception("Falha ao salvar score")
        await query.answer(text=f"Erro ao salvar: {exc}", show_alert=True)
        await _safe_clear_keyboard(query)
        return

    if score >= 4:
        await query.answer(text="Ótimo! Marcado como bom.", show_alert=False)
        await _safe_clear_keyboard(query)
    else:
        await query.answer(text="Registrado. Quer explicar o que ficou ruim?", show_alert=False)
        try:
            await query.edit_message_reply_markup(
                reply_markup=feedback_comment_keyboard(interaction_id)
            )
        except BadRequest as exc:
            if "not modified" not in str(exc).lower():
                log.warning("Falha ao trocar teclado de feedback: %s", exc)


async def on_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback 'fb:comment:<id>' e 'fb:skip:<id>'."""
    query = update.callback_query
    if query is None or query.data is None:
        return

    try:
        _, action, raw_id = query.data.split(":", 2)
        interaction_id = int(raw_id)
    except (ValueError, IndexError):
        await query.answer()
        return

    if action == "comment":
        context.user_data["awaiting_correction"] = interaction_id
        await query.answer(text="Envie seu comentário agora.", show_alert=False)
        await _safe_edit_text(query, "Aguardando seu comentário...")
    elif action == "skip":
        await query.answer(show_alert=False)
        await _safe_clear_keyboard(query)


# ──────────────────────────────────────────────────────────────────────────────
# RDO inline
# ──────────────────────────────────────────────────────────────────────────────

async def on_rdo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback 'rdo:{acao}:{payload}'."""
    query = update.callback_query
    if query is None or query.data is None:
        return

    parts = query.data.split(":", 2)
    if len(parts) < 2:
        await query.answer()
        return

    section = parts[1]
    payload = parts[2] if len(parts) > 2 else ""
    await query.answer()

    deps = _deps(context)

    # ── menu principal ──────────────────────────────────────────────────────
    if section == "menu":
        if payload == "clima":
            await _safe_edit_text(query, "Qual foi a condição climática?",
                                  reply_markup=clima_keyboard())
        elif payload == "efetivo":
            context.user_data["awaiting_rdo"] = {"type": "efetivo"}
            await _safe_edit_text(query, "Informe: Função; quantidade (ex: Pedreiro; 3)")
        elif payload == "atividade":
            context.user_data["awaiting_rdo"] = {"type": "atividade"}
            await _safe_edit_text(query, "Descreva a atividade realizada:")
        elif payload == "anotacao":
            context.user_data["awaiting_rdo"] = {"type": "anotacao"}
            await _safe_edit_text(query, "Digite a anotação:")
        elif payload == "ver":
            await _show_rdo_hoje(query, context, deps)
        elif payload == "cronograma":
            await _show_cronograma(query, context, deps)

    # ── registro de clima por botão ────────────────────────────────────────
    elif section == "clima":
        from tg.middleware import get_bot_project, get_bot_user
        try:
            project = get_bot_project(context)
            user = get_bot_user(context)
        except Exception:
            await _safe_edit_text(query, "Selecione uma obra ativa primeiro (/obras).")
            return
        dia = datetime.now().astimezone().strftime("%Y-%m-%d")
        condicao_label = {"sol": "Claro ☀️", "nublado": "Nublado ⛅",
                          "chuva": "Chuva 🌧️", "nevoa": "Névoa 🌫️"}.get(payload, payload)
        try:
            await deps.sqlite.clima.insert(
                project_id=project.id, dia=dia, condicao=payload,
                hora_inicio=None, hora_fim=None, criado_por=user.id,
            )
            await _safe_edit_text(query, f"✅ Clima registrado: {condicao_label} — {dia}")
        except Exception as exc:
            await _safe_edit_text(query, f"Erro ao registrar clima: {exc}")

    # ── confirmação de pending_rdo (via IA ou foto) ────────────────────────
    elif section == "confirm":
        pending = context.user_data.pop("pending_rdo", None)
        if pending is None:
            await _safe_edit_text(query, "Nada pendente para confirmar.")
            return
        await _save_pending_rdo(query, context, deps, pending)

    elif section == "skip":
        context.user_data.pop("pending_rdo", None)
        await _safe_edit_text(query, "❌ Registro descartado.")

    elif section == "pending" and payload == "ajustar":
        pending = context.user_data.get("pending_rdo")
        if pending is None:
            await _safe_edit_text(query, "Nada pendente para ajustar.")
            return
        rdo_type = pending.get("type", "registro")
        await _safe_edit_text(
            query,
            f"Envie os dados corrigidos para {rdo_type} "
            f"(o registro anterior ainda está pendente)."
        )
        context.user_data["awaiting_rdo"] = {"type": rdo_type}


async def _save_pending_rdo(
    query: CallbackQuery,
    context: ContextTypes.DEFAULT_TYPE,
    deps: "BotDependencies",
    pending: dict,
) -> None:
    """Persiste um pending_rdo confirmado pelo usuário."""
    from tg.middleware import get_bot_project, get_bot_user
    try:
        project = get_bot_project(context)
        user = get_bot_user(context)
    except Exception:
        await _safe_edit_text(query, "Obra ativa não encontrada. Use /obras.")
        return

    dia = pending.get("dia") or datetime.now().astimezone().strftime("%Y-%m-%d")
    rdo_type = pending.get("type")

    try:
        if rdo_type == "atividade":
            await deps.sqlite.atividades.insert(
                project_id=project.id, dia=dia,
                estado=pending.get("estado", "em_andamento"),
                descricao=pending["descricao"], criado_por=user.id,
            )
            await _safe_edit_text(query, f"✅ Atividade registrada — {dia}")

        elif rdo_type == "clima":
            await deps.sqlite.clima.insert(
                project_id=project.id, dia=dia,
                condicao=pending["condicao"],
                hora_inicio=pending.get("hora_inicio"),
                hora_fim=pending.get("hora_fim"),
                criado_por=user.id,
            )
            await _safe_edit_text(query, f"✅ Clima registrado — {dia}")

        elif rdo_type == "efetivo":
            funcao = await deps.sqlite.funcoes.get_by_nome(pending["funcao"])
            if funcao is None:
                await _safe_edit_text(
                    query, f"Função '{pending['funcao']}' não encontrada. Use /funcoes."
                )
                return
            await deps.sqlite.efetivo.insert(
                project_id=project.id, dia=dia,
                funcao_id=funcao.id, empresa_id=None,
                qtd=int(pending["qtd"]), criado_por=user.id,
            )
            await _safe_edit_text(query, f"✅ Efetivo registrado — {dia}")

        elif rdo_type == "anotacao":
            await deps.sqlite.anotacoes.insert(
                project_id=project.id, dia=dia,
                texto=pending["texto"], criado_por=user.id,
            )
            await _safe_edit_text(query, f"✅ Anotação registrada — {dia}")

        else:
            await _safe_edit_text(query, f"Tipo '{rdo_type}' não suportado.")

    except Exception as exc:
        log.exception("Falha ao salvar pending_rdo tipo=%s", rdo_type)
        await _safe_edit_text(query, f"Erro ao registrar: {exc}")


async def _show_rdo_hoje(
    query: CallbackQuery,
    context: ContextTypes.DEFAULT_TYPE,
    deps: "BotDependencies",
) -> None:
    from tg.middleware import get_bot_project
    try:
        project = get_bot_project(context)
    except Exception:
        await _safe_edit_text(query, "Selecione uma obra ativa primeiro.")
        return

    dia = datetime.now().astimezone().strftime("%Y-%m-%d")
    lines: list[str] = [f"<b>📊 RDO — {dia}</b>", f"<i>{project.name}</i>", ""]

    try:
        climas = await deps.sqlite.clima.list_by_dia(project.id, dia)
        if climas:
            lines.append("<b>☀️ Clima</b>")
            for c in climas:
                hora = f" {c.hora_inicio}–{c.hora_fim}" if c.hora_inicio else ""
                lines.append(f"  • {c.condicao}{hora}")
            lines.append("")
    except Exception:
        pass

    try:
        efetivos = await deps.sqlite.efetivo.list_by_dia(project.id, dia)
        if efetivos:
            lines.append("<b>👷 Efetivo</b>")
            for e in efetivos:
                lines.append(f"  • {e.qtd}× #{e.funcao_id}")
            lines.append("")
    except Exception:
        pass

    try:
        atividades = await deps.sqlite.atividades.list_by_dia(project.id, dia)
        if atividades:
            lines.append("<b>⚒️ Atividades</b>")
            icons = {"concluida": "✅", "em_andamento": "🔄",
                     "atrasada": "⏰", "impedida": "🚫"}
            for a in atividades:
                lines.append(f"  {icons.get(a.estado, '•')} {a.descricao}")
            lines.append("")
    except Exception:
        pass

    try:
        anotacoes = await deps.sqlite.anotacoes.list_by_dia(project.id, dia)
        if anotacoes:
            lines.append("<b>📝 Anotações</b>")
            for a in anotacoes:
                lines.append(f"  • {a.texto[:120]}")
    except Exception:
        pass

    if len(lines) <= 3:
        lines.append("Nenhum registro para hoje ainda.")

    lines.append("")
    lines.append("📋 <i>Toque no menu para adicionar mais.</i>")

    await _safe_edit_text(
        query, "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=rdo_menu_keyboard(),
    )


async def _show_cronograma(
    query: CallbackQuery,
    context: ContextTypes.DEFAULT_TYPE,
    deps: "BotDependencies",
) -> None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from tg.middleware import get_bot_project
    try:
        project = get_bot_project(context)
    except Exception:
        await _safe_edit_text(query, "Selecione uma obra ativa primeiro.")
        return

    try:
        etapas = await deps.sqlite.cronograma.list_for_project(project.id)
    except Exception as exc:
        await _safe_edit_text(query, f"Erro ao carregar cronograma: {exc}")
        return

    if not etapas:
        lines = [f"<b>📅 Cronograma — {project.name}</b>", "",
                 "Nenhuma etapa cadastrada."]
    else:
        lines = [f"<b>📅 Cronograma — {project.name}</b>", ""]
        for i, e in enumerate(etapas, 1):
            data = f" → {e.data_prevista_termino}" if e.data_prevista_termino else ""
            lines.append(f"  {i}. {e.etapa}{data}")

    buttons = [[InlineKeyboardButton("➕ Nova etapa", callback_data="rdo:cronograma:nova")],
               [InlineKeyboardButton("↩ Voltar", callback_data="rdo:menu:ver")]]

    await _safe_edit_text(
        query, "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Outros callbacks existentes
# ──────────────────────────────────────────────────────────────────────────────

async def on_reminder_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback 'rem:cancel:<id>'."""
    query = update.callback_query
    if query is None or query.data is None or update.effective_user is None:
        return

    try:
        _, _, raw_id = query.data.split(":", 2)
        reminder_id = int(raw_id)
    except (ValueError, IndexError):
        await query.answer()
        return

    deps = _deps(context)
    ok = await deps.reminders.cancel(reminder_id, user_id=update.effective_user.id)
    await query.answer(
        text=("Lembrete cancelado." if ok else "Não consegui cancelar (já foi?)."),
        show_alert=False,
    )
    await _safe_clear_keyboard(query)


async def on_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback 'cfg:model:<name>' e 'cfg:temp:<float>'."""
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
