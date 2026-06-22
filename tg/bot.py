# tg/bot.py

from __future__ import annotations

import ssl
import sys
from dataclasses import dataclass

from telegram import BotCommand, Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ContextTypes,
)
from telegram.request import HTTPXRequest

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
from tg.debug_notifier import DebugNotifier
from tg.handlers import register_all_handlers
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
    debug_notifier: DebugNotifier | None


_BOT_COMMANDS: list[BotCommand] = [
    BotCommand("start",      "saudação / consumir convite"),
    BotCommand("help",       "lista de comandos"),
    BotCommand("obras",      "suas obras"),
    BotCommand("obra",       "obra ativa (sem args = mostra)"),
    BotCommand("criar_obra", "criar obra (admin)"),
    BotCommand("invite",     "gerar convite pra obra ativa"),
    BotCommand("membros",    "membros da obra ativa"),
    BotCommand("funcoes",    "catálogo de funções"),
    BotCommand("empresas",   "empresas da obra ativa"),
    BotCommand("empresa",    "empresa add Nome; CNPJ; own|third"),
    BotCommand("colabs",     "colaboradores [função]"),
    BotCommand("colab",      "colab add Nome; Função; Empresa"),
    BotCommand("config",     "modelo + temperatura"),
    BotCommand("stats",      "estatísticas globais"),
    BotCommand("recall",     "debug do RAG (top hits)"),
    BotCommand("history",    "suas últimas interações"),
    BotCommand("ping",       "health-check do Ollama"),
    BotCommand("whoami",     "seu user_id e config"),
    BotCommand("reset",      "voltar config ao padrão"),
    BotCommand("reminders",  "seus lembretes pendentes"),
]


def _windows_ssl_request(
    read_timeout: float = 60.0,
    write_timeout: float = 60.0,
    connect_timeout: float = 30.0,
    pool_timeout: float = 30.0,
    media_write_timeout: float = 600.0,
    connection_pool_size: int = 1,
) -> HTTPXRequest:
    """HTTPXRequest que usa o Windows certificate store (inclui CAs do sistema, ex: Kaspersky)."""
    return HTTPXRequest(
        read_timeout=read_timeout,
        write_timeout=write_timeout,
        connect_timeout=connect_timeout,
        pool_timeout=pool_timeout,
        media_write_timeout=media_write_timeout,
        connection_pool_size=connection_pool_size,
        httpx_kwargs={"verify": ssl.create_default_context()},
    )


def build_application(deps: BotDependencies) -> Application:
    s = deps.settings

    if sys.platform == "win32":
        base_req = _windows_ssl_request(
            read_timeout=s.telegram_read_timeout_s,
            write_timeout=s.telegram_write_timeout_s,
            connect_timeout=s.telegram_connect_timeout_s,
            pool_timeout=s.telegram_pool_timeout_s,
            media_write_timeout=s.telegram_media_write_timeout_s,
        )
        upd_req = _windows_ssl_request(
            read_timeout=s.telegram_get_updates_read_timeout_s,
            write_timeout=s.telegram_write_timeout_s,
            connect_timeout=s.telegram_connect_timeout_s,
            pool_timeout=s.telegram_pool_timeout_s,
            media_write_timeout=s.telegram_media_write_timeout_s,
        )
        app: Application = (
            ApplicationBuilder()
            .token(s.telegram_bot_token)
            .request(base_req)
            .get_updates_request(upd_req)
            .post_init(_on_post_init)
            .post_shutdown(_on_post_shutdown)
            .build()
        )
        log.info("Windows detectado: HTTPXRequest usando ssl.create_default_context()")
    else:
        app: Application = (
            ApplicationBuilder()
            .token(s.telegram_bot_token)
            .read_timeout(s.telegram_read_timeout_s)
            .write_timeout(s.telegram_write_timeout_s)
            .connect_timeout(s.telegram_connect_timeout_s)
            .pool_timeout(s.telegram_pool_timeout_s)
            .media_write_timeout(s.telegram_media_write_timeout_s)
            .get_updates_read_timeout(s.telegram_get_updates_read_timeout_s)
            .post_init(_on_post_init)
            .post_shutdown(_on_post_shutdown)
            .build()
        )

    app.bot_data["deps"] = deps
    app.add_error_handler(_on_error)
    register_all_handlers(app)

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
    if deps.debug_notifier is not None:
        await deps.debug_notifier.close()
    log.info("Bot finalizado.")


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Error handler global: loga curto e tenta avisar o usuário sem propagar."""
    err = context.error
    # Ruído de polling: o PTB já retenta sozinho — só logamos como warning.
    if isinstance(err, (NetworkError, TimedOut)):
        log.warning("Rede instável (%s): %s", type(err).__name__, err)
        return

    log.error("Erro não tratado em handler: %s", err, exc_info=err)
    if isinstance(update, Update) and update.effective_message is not None:
        try:
            await update.effective_message.reply_text(
                "⚠️ Erro técnico ao processar isso. Tenta de novo daqui a pouco — "
                "se persistir, me avise."
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("Falha ao notificar usuário sobre erro: %s", exc)
