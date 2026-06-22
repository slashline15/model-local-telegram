# tg/handlers/feedback.py
# Interceptor de texto para estados multi-turn: awaiting_correction e awaiting_rdo.
# Chamado no topo de on_text em pipeline.py antes de entrar no pipeline principal.

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import ContextTypes

from core.logger import get_logger

if TYPE_CHECKING:
    from tg.bot import BotDependencies

log = get_logger(__name__)


def _deps(context: ContextTypes.DEFAULT_TYPE) -> "BotDependencies":
    return context.application.bot_data["deps"]  # type: ignore[no-any-return]


async def try_intercept(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> bool:
    """
    Tenta interceptar a mensagem de texto para estados multi-turn.
    Retorna True se a mensagem foi consumida (não deve ir ao pipeline).
    """
    msg = update.effective_message
    assert msg is not None

    # ── Fluxo de nova etapa de cronograma ─────────────────────────────────
    if context.user_data.get("awaiting_cronograma"):
        from tg.handlers.rdo.cronograma import handle_cronograma_text
        return await handle_cronograma_text(update, context, text)

    # ── Comentário sobre resposta ruim ────────────────────────────────────
    if context.user_data.get("awaiting_correction"):
        iid = context.user_data.pop("awaiting_correction")
        deps = _deps(context)
        await deps.sqlite.set_correction(iid, text)
        await msg.reply_text("Anotado. O modelo vai evitar isso nas próximas respostas.")
        return True

    # ── Entrada de texto após menu RDO ────────────────────────────────────
    if context.user_data.get("awaiting_rdo"):
        await _handle_rdo_text(update, context, text)
        return True

    # ── Menu RDO inline ───────────────────────────────────────────────────
    if text.lower() in ("menu", "📋 menu"):
        from tg.kb import rdo_menu_keyboard
        await msg.reply_text("📋 RDO do dia:", reply_markup=rdo_menu_keyboard())
        return True

    return False


async def _handle_rdo_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
) -> None:
    """Processa texto enviado após seleção de item RDO no menu inline."""
    msg = update.effective_message
    assert msg is not None
    rdo_state = context.user_data.pop("awaiting_rdo")
    rdo_type = rdo_state.get("type")
    deps = _deps(context)

    from tg.middleware import get_bot_project, get_bot_user
    try:
        project = get_bot_project(context)
        user = get_bot_user(context)
    except Exception:
        await msg.reply_text("Selecione uma obra ativa primeiro (/obras).")
        return

    dia = datetime.now().astimezone().strftime("%Y-%m-%d")

    try:
        if rdo_type == "efetivo":
            await _reg_efetivo(msg, deps, project, user, dia, text)
        elif rdo_type == "atividade":
            await _reg_atividade(msg, deps, project, user, dia, text)
        elif rdo_type == "anotacao":
            await deps.sqlite.anotacoes.insert(
                project_id=project.id, dia=dia, texto=text, criado_por=user.id,
            )
            await msg.reply_text(f"📝 Anotação registrada — {dia}")
        else:
            await msg.reply_text("Tipo de entrada não reconhecido.")
    except Exception as exc:
        log.exception("Falha ao registrar RDO via texto tipo=%s", rdo_type)
        await msg.reply_text(f"Erro ao registrar: {exc}")


async def _reg_efetivo(msg, deps, project, user, dia: str, text: str) -> None:
    from tg.handlers.rdo.diario import _parse_efetivo_args
    parsed = _parse_efetivo_args(text)
    if parsed is None:
        await msg.reply_text(
            "Não entendi. Use o formato: `Função; quantidade`\n"
            "Exemplo: `Pedreiro; 3`"
        )
        return
    funcao_nome, qtd_raw, _empresa_ref = parsed
    funcao = await deps.sqlite.funcoes.get_by_nome(funcao_nome)
    if funcao is None:
        await msg.reply_text(f"Função '{funcao_nome}' não existe. Veja /funcoes.")
        return
    await deps.sqlite.efetivo.insert(
        project_id=project.id, dia=dia, funcao_id=funcao.id,
        empresa_id=None, qtd=int(qtd_raw), criado_por=user.id,
    )
    await msg.reply_text(f"👷 {int(qtd_raw)}× {funcao.nome} registrado — {dia}")


async def _reg_atividade(msg, deps, project, user, dia: str, text: str) -> None:
    from tg.handlers.rdo.diario import _parse_atividade_args
    from database.repos.atividades import normalizar_estado
    from core.exceptions import StorageError
    parsed = _parse_atividade_args(text)
    if parsed is None:
        descricao, estado = text.strip(), "em_andamento"
    else:
        descricao, estado_raw = parsed
        try:
            estado = normalizar_estado(estado_raw)
        except StorageError:
            estado = "em_andamento"
    await deps.sqlite.atividades.insert(
        project_id=project.id, dia=dia, estado=estado,
        descricao=descricao, criado_por=user.id,
    )
    icons = {"concluida": "✅", "em_andamento": "🔄", "atrasada": "⏰", "impedida": "🚫"}
    await msg.reply_text(f"{icons.get(estado, '•')} Atividade registrada — {dia}")
