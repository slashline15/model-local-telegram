# tg/handlers/ — pacote de handlers organizado por domínio.
#
# Ponto de entrada único: register_all_handlers(app) registra todos os
# CommandHandlers, MessageHandlers e CallbackQueryHandlers.
#
# Re-exports para retrocompat de testes que fazem:
#   from tg.handlers import _chunk_text, _file_too_big

from __future__ import annotations

from typing import TYPE_CHECKING

from tg.handlers.pipeline import _chunk_text, _file_too_big  # noqa: F401 — test compat

if TYPE_CHECKING:
    from telegram.ext import Application


def register_all_handlers(app: "Application") -> None:
    from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters

    from tg import callbacks
    from tg.handlers import debug, doc, pipeline, recall, system
    from tg.handlers.projects import on_obra_select
    from tg.handlers.rdo import cadastros, cronograma, diario

    # ── Sistema ────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",      system.cmd_start))
    app.add_handler(CommandHandler("help",       system.cmd_help))
    app.add_handler(CommandHandler("ping",       system.cmd_ping))
    app.add_handler(CommandHandler("whoami",     system.cmd_whoami))
    app.add_handler(CommandHandler("stats",      system.cmd_stats))
    app.add_handler(CommandHandler("reset",      system.cmd_reset))

    # ── Recall / histórico ────────────────────────────────────────────────
    app.add_handler(CommandHandler("config",     recall.cmd_config))
    app.add_handler(CommandHandler("recall",     recall.cmd_recall))
    app.add_handler(CommandHandler("history",    recall.cmd_history))
    app.add_handler(CommandHandler("reminders",  recall.cmd_reminders))

    # ── Projetos (obras) ──────────────────────────────────────────────────
    from tg.handlers.projects import (
        cmd_criar_obra, cmd_invite, cmd_membros, cmd_obra, cmd_obras,
    )
    app.add_handler(CommandHandler("criar_obra", cmd_criar_obra))
    app.add_handler(CommandHandler("obras",      cmd_obras))
    app.add_handler(CommandHandler("obra",       cmd_obra))
    app.add_handler(CommandHandler("invite",     cmd_invite))
    app.add_handler(CommandHandler("membros",    cmd_membros))

    # ── RDO — cadastros ───────────────────────────────────────────────────
    app.add_handler(CommandHandler("funcoes",    cadastros.cmd_funcoes))
    app.add_handler(CommandHandler("empresas",   cadastros.cmd_empresas))
    app.add_handler(CommandHandler("empresa",    cadastros.cmd_empresa))
    app.add_handler(CommandHandler("colabs",     cadastros.cmd_colabs))
    app.add_handler(CommandHandler("colab",      cadastros.cmd_colab))

    # ── RDO — diário ──────────────────────────────────────────────────────
    app.add_handler(CommandHandler("clima",       diario.cmd_clima))
    app.add_handler(CommandHandler("climas",      diario.cmd_climas))
    app.add_handler(CommandHandler("efetivo",     diario.cmd_efetivo))
    app.add_handler(CommandHandler("efetivos",    diario.cmd_efetivos))
    app.add_handler(CommandHandler("atividade",   diario.cmd_atividade))
    app.add_handler(CommandHandler("atividades",  diario.cmd_atividades))
    app.add_handler(CommandHandler("anotacao",    diario.cmd_anotacao))
    app.add_handler(CommandHandler("anotacoes",   diario.cmd_anotacoes))
    app.add_handler(CommandHandler("rdo",         diario.cmd_rdo))

    # ── RDO — cronograma ──────────────────────────────────────────────────
    app.add_handler(CommandHandler("cronograma",  cronograma.cmd_cronograma))

    # ── Documentos classificados ──────────────────────────────────────────
    app.add_handler(CommandHandler("doc",         doc.cmd_doc))

    # ── Mídia (pipeline principal) ────────────────────────────────────────
    app.add_handler(MessageHandler(filters.PHOTO, pipeline.on_photo))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, pipeline.on_voice))
    app.add_handler(MessageHandler(filters.Document.ALL, pipeline.on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, pipeline.on_text))

    # ── Callbacks inline ──────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(callbacks.on_rate,             pattern=r"^rate:"))
    app.add_handler(CallbackQueryHandler(callbacks.on_feedback,         pattern=r"^fb:"))
    app.add_handler(CallbackQueryHandler(callbacks.on_rdo,              pattern=r"^rdo:"))
    app.add_handler(CallbackQueryHandler(callbacks.on_config,           pattern=r"^cfg:"))
    app.add_handler(CallbackQueryHandler(callbacks.on_reminder_cancel,  pattern=r"^rem:cancel:"))
    app.add_handler(CallbackQueryHandler(on_obra_select,                pattern=r"^obra:set:"))
    app.add_handler(CallbackQueryHandler(doc.on_doc_callback,           pattern=r"^doc:"))

    # ── Debug (superadmin) ────────────────────────────────────────────────
    app.add_handler(CommandHandler("consumo",         debug.cmd_consumo))
    app.add_handler(CommandHandler("consumo_usuario", debug.cmd_consumo_usuario))
    app.add_handler(CommandHandler("consumo_obra",    debug.cmd_consumo_obra))
    app.add_handler(CommandHandler("consumo_modelo",  debug.cmd_consumo_modelo))
    app.add_handler(CommandHandler("status",          debug.cmd_status))
