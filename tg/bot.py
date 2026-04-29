from __future__ import annotations

from dataclasses import dataclass

from telegram import BotCommand
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from core.audio_transcriber import WhisperTranscriber
from core.config import Settings
from core.logger import get_logger
from core.reminders import ReminderManager
from database.faiss_mgr import FaissManager
from database.sqlite_mgr import SQLiteManager
from llm.contrastive_rag import ContrastiveRAG
from llm.intent_classifier import IntentClassifier
from llm.ollama_client import OllamaClient
from llm.openai_chat_client import OpenAIChatClient
from llm.tag_generator import TagGenerator
from tg import callbacks, handlers
from tools.registry import ToolRegistry

log = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class BotDependencies:
    settings: Settings
    sqlite: SQLiteManager
    faiss: FaissManager
    ollama: OllamaClient
    tag_gen: TagGenerator
    intent: IntentClassifier
    rag: ContrastiveRAG
    tools: ToolRegistry
    reminders: ReminderManager
    transcriber: WhisperTranscriber | None
    openai_chat: OpenAIChatClient | None


_BOT_COMMANDS: list[BotCommand] = [
    BotCommand("start",   "saudação inicial"),
    BotCommand("help",    "lista de comandos"),
    BotCommand("config",  "modelo + temperatura"),
    BotCommand("stats",   "estatísticas globais"),
    BotCommand("recall",  "debug do RAG (top hits)"),
    BotCommand("history", "suas últimas interações"),
    BotCommand("ping",    "health-check do Ollama"),
    BotCommand("whoami",  "seu user_id e config"),
    BotCommand("reset",     "voltar config ao padrão"),
    BotCommand("reminders", "seus lembretes pendentes"),
]


def build_application(deps: BotDependencies) -> Application:
    app: Application = (
        ApplicationBuilder()
        .token(deps.settings.telegram_bot_token)
        .post_init(_on_post_init)
        .post_shutdown(_on_post_shutdown)
        .build()
    )

    app.bot_data["deps"] = deps

    app.add_handler(CommandHandler("start",   handlers.cmd_start))
    app.add_handler(CommandHandler("help",    handlers.cmd_help))
    app.add_handler(CommandHandler("config",  handlers.cmd_config))
    app.add_handler(CommandHandler("stats",   handlers.cmd_stats))
    app.add_handler(CommandHandler("recall",  handlers.cmd_recall))
    app.add_handler(CommandHandler("history", handlers.cmd_history))
    app.add_handler(CommandHandler("ping",    handlers.cmd_ping))
    app.add_handler(CommandHandler("whoami",  handlers.cmd_whoami))
    app.add_handler(CommandHandler("reset",   handlers.cmd_reset))
    app.add_handler(CommandHandler("reminders", handlers.cmd_reminders))

    app.add_handler(MessageHandler(filters.PHOTO, handlers.on_photo))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handlers.on_voice))
    app.add_handler(MessageHandler(filters.Document.ALL, handlers.on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.on_text))

    app.add_handler(CallbackQueryHandler(callbacks.on_rate, pattern=r"^rate:"))
    app.add_handler(CallbackQueryHandler(callbacks.on_config, pattern=r"^cfg:"))
    app.add_handler(CallbackQueryHandler(callbacks.on_reminder_cancel, pattern=r"^rem:cancel:"))

    return app


async def _on_post_init(app: Application) -> None:
    deps: BotDependencies = app.bot_data["deps"]
    try:
        await app.bot.set_my_commands(_BOT_COMMANDS)
    except Exception as exc:  # noqa: BLE001
        log.warning("Falha ao registrar comandos no Telegram: %s", exc)

    # JobQueue só fica disponível após o build da Application — daí o bind aqui.
    deps.reminders.bind_app(app)
    try:
        n = await deps.reminders.reload_pending()
        if n:
            log.info("Lembretes pendentes reagendados: %d", n)
    except Exception as exc:  # noqa: BLE001
        log.warning("Falha ao reagendar lembretes pendentes: %s", exc)

    # Health-check de Ollama precisa rodar AQUI (loop do telegram-bot).
    try:
        report = await deps.ollama.health_check(
            expected_dim=deps.settings.embedding_dim
        )
        log.info(
            "Ollama health: reachable=%s models=%d chat=%s emb=%s dim_live=%s",
            report.ollama_reachable,
            len(report.models_available),
            report.chat_model_present,
            report.embedding_model_present,
            report.embedding_dim_live,
        )
        if report.error:
            log.warning("Ollama health WARNING: %s", report.error)
        if not report.chat_model_present:
            log.warning(
                "Modelo de chat '%s' ausente em /api/tags. Rode: ollama pull %s",
                deps.settings.ollama_default_model,
                deps.settings.ollama_default_model,
            )
        if not report.embedding_model_present:
            log.warning(
                "Modelo de embedding '%s' ausente em /api/tags. Rode: ollama pull %s",
                deps.settings.ollama_embedding_model,
                deps.settings.ollama_embedding_model,
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("health_check do Ollama falhou: %s", exc)

    log.info(
        "Bot iniciado. Comandos registrados: %s",
        ", ".join(c.command for c in _BOT_COMMANDS),
    )


async def _on_post_shutdown(app: Application) -> None:
    deps: BotDependencies = app.bot_data["deps"]
    await deps.ollama.close()
    if deps.openai_chat is not None:
        await deps.openai_chat.close()
    log.info("Bot finalizado.")
