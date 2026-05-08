from __future__ import annotations

import asyncio
import sys

from core.audio_transcriber import WhisperTranscriber
from core.config import get_settings
from core.logger import get_logger, setup_logging
from core.reminders import ReminderManager
from database.backup import create_backup
from database.faiss_mgr import FaissManager
from database.sqlite_mgr import SQLiteManager
from llm.contrastive_rag import ContrastiveRAG
from llm.intent_classifier import IntentClassifier
from llm.ollama_client import OllamaClient
from llm.openai_chat_client import OpenAIChatClient
from llm.tag_generator import TagGenerator
from tg.bot import BotDependencies, build_application
from tg.debug_notifier import DebugNotifier
from tools import reminders as reminder_tools
from tools import web_search
from tools.registry import ToolRegistry




async def _bootstrap() -> BotDependencies:
    settings = get_settings()
    setup_logging(level=settings.log_level, log_file=settings.log_file, use_color=True)
    log = get_logger(__name__)

    if settings.sqlite_backup_enabled:
        create_backup(
            settings.sqlite_path,
            settings.sqlite_backup_dir,
            max_keep=settings.sqlite_backup_max_keep,
        )

    sqlite = SQLiteManager(
        db_path=settings.sqlite_path,
        default_model=settings.ollama_default_model,
    )
    await sqlite.init_schema()

    faiss = FaissManager(
        dim=settings.embedding_dim,
        index_path=settings.faiss_index_path,
        id_map_path=settings.faiss_id_map_path,
    )
    await faiss.init()

    ollama = OllamaClient(
        host=settings.ollama_host,
        default_model=settings.ollama_default_model,
        embedding_model=settings.ollama_embedding_model,
        request_timeout_s=settings.ollama_request_timeout_s,
    )

    tag_gen = TagGenerator(ollama=ollama, classifier_model=settings.ollama_default_model)
    intent = IntentClassifier(ollama=ollama, classifier_model=settings.ollama_default_model)

    rag = ContrastiveRAG(
        ollama=ollama,
        sqlite=sqlite,
        faiss=faiss,
        chunks=sqlite.chunks,
        top_k=settings.rag_top_k,
        max_positive=settings.rag_max_positive,
        max_negative=settings.rag_max_negative,
        max_neutral=settings.rag_max_neutral,
        positive_threshold=settings.rag_positive_score_threshold,
        negative_threshold=settings.rag_negative_score_threshold,
        embedding_model=settings.ollama_embedding_model,
    )

    # Health-check do Ollama é feito em `tg.bot._on_post_init`, dentro do loop
    # do python-telegram-bot. Fazê-lo aqui criaria a aiohttp.ClientSession no
    # loop de bootstrap (que será fechado), causando "Event loop is closed".

    reminders = ReminderManager(sqlite=sqlite)

    registry = ToolRegistry()
    web_search.register(registry)
    reminder_tools.register(registry, manager=reminders)

    transcriber: WhisperTranscriber | None = None
    openai_chat: OpenAIChatClient | None = None
    if settings.openai_api_key:
        transcriber = WhisperTranscriber(
            api_key=settings.openai_api_key,
            api_base=settings.openai_api_base,
            model=settings.openai_whisper_model,
            timeout_s=settings.openai_whisper_timeout_s,
            max_size_mb=settings.whisper_max_mb,
        )
        if settings.openai_chat_fallback_model:
            openai_chat = OpenAIChatClient(
                api_key=settings.openai_api_key,
                api_base=settings.openai_api_base,
            )
            log.info(
                "Fallback OpenAI ativo: modelo=%s",
                settings.openai_chat_fallback_model,
            )
    else:
        log.warning("OPENAI_API_KEY ausente — transcrição de áudio e fallback OpenAI desativados.")

    if settings.chat_fallback_models:
        log.info("Fallback Ollama: %s", ", ".join(settings.chat_fallback_models))

    # Debug notifier — só ativa se token e chat_id estiverem configurados.
    debug_notifier: DebugNotifier | None = None
    if settings.debug_mode and settings.telegram_debug_bot_token:
        # Chat ID do debug bot: por convenção = telegram_id do superadmin.
        debug_chat_id = settings.bootstrap_superadmin_telegram_id
        if debug_chat_id:
            debug_notifier = DebugNotifier(
                token=settings.telegram_debug_bot_token,
                chat_id=debug_chat_id,
                min_cost_usd=settings.debug_notify_min_cost_usd,
                sample_rate=settings.debug_notify_sample_rate,
                on_error=settings.debug_notify_on_error,
                on_latency_ms=settings.debug_notify_on_latency_ms,
            )
            log.info("DebugNotifier ativo (chat_id=%s).", debug_chat_id)
        else:
            log.warning(
                "DEBUG_MODE=true mas BOOTSTRAP_SUPERADMIN_TELEGRAM_ID não definido — "
                "debug_notifier desativado."
            )

    return BotDependencies(
        settings=settings,
        sqlite=sqlite,
        faiss=faiss,
        ollama=ollama,
        tag_gen=tag_gen,
        intent=intent,
        rag=rag,
        tools=registry,
        reminders=reminders,
        transcriber=transcriber,
        openai_chat=openai_chat,
        debug_notifier=debug_notifier,
    )


def main() -> None:
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    deps = loop.run_until_complete(_bootstrap())
    app = build_application(deps)
    app.run_polling(allowed_updates=None)


if __name__ == "__main__":
    main()
