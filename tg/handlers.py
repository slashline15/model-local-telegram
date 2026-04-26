from __future__ import annotations

import json
import time
import uuid
from datetime import datetime
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from agents.router import AgentRouter
from core.logger import get_logger
from core.pipeline import PipelineRecorder
from llm.contrastive_rag import RagBundle
from llm.ollama_client import ChatMessage, ChatResult

if TYPE_CHECKING:
    from tg.bot import BotDependencies

log = get_logger(__name__)

_AGENT_ROUTER: AgentRouter = AgentRouter()

# Substrings comuns em modelos Ollama com suporte a visão.
_VISION_PATTERNS: tuple[str, ...] = (
    "llava", "vision", "-vl", "moondream", "bakllava",
    "minicpm-v", "qwen2.5-vl", "qwen2-vl", "gemma3", "gemma4", "llama3.2-vision",
    "pixtral", "phi3.5-vision",
)

_TEXT_DOC_SUFFIXES: frozenset[str] = frozenset({
    ".txt", ".md", ".csv", ".log", ".json", ".yaml", ".yml", ".ini", ".cfg",
    ".py", ".js", ".ts", ".html", ".xml", ".sql",
})

_DOC_MAX_CHARS: int = 8000
_TOOL_LOOP_MAX_ITER: int = 3
# nomic-embed-text aguenta ~8k tokens; ~3k chars dá larga folga.
_EMBED_INPUT_MAX_CHARS: int = 3000
# Classificação e tagging só precisam dos primeiros ~2k chars para decidir.
_CLASSIFY_INPUT_MAX_CHARS: int = 2000


def _deps(context: ContextTypes.DEFAULT_TYPE) -> "BotDependencies":
    return context.application.bot_data["deps"]  # type: ignore[no-any-return]


def _rating_keyboard(interaction_id: int) -> InlineKeyboardMarkup:
    row = [
        InlineKeyboardButton(text=f"⭐ {n}", callback_data=f"rate:{interaction_id}:{n}")
        for n in (1, 2, 3, 4, 5)
    ]
    return InlineKeyboardMarkup([row])


def _model_supports_vision(model: str) -> bool:
    m = model.lower()
    return any(p in m for p in _VISION_PATTERNS)


def _extract_document_text(path: Path) -> str:
    """Tenta extrair texto de PDF / arquivos texto. Retorna '' se não der."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader  # import tardio: evita custo no boot

            reader = PdfReader(str(path))
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        if suffix in _TEXT_DOC_SUFFIXES:
            return path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        log.warning("Falha ao extrair texto de %s: %s", path.name, exc)
    return ""


def _now_local_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ─────────────────────────── COMANDOS ───────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(
        "Olá! Eu aprendo com seu feedback (Contrastive RAG).\n\n"
        "Envie texto, imagem, documento ou áudio. Após cada resposta, "
        "use as estrelas (⭐ 1 a ⭐ 5) para me ensinar.\n\n"
        "Comandos:\n"
        "/help   – lista de comandos\n"
        "/config – modelo + temperatura\n"
        "/stats  – estatísticas do banco\n"
        "/recall – ver o que o RAG recuperaria\n"
        "/history – suas últimas interações\n"
        "/ping   – health-check do Ollama\n"
        "/whoami – seus dados\n"
        "/reset  – restaurar configurações padrão"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(
        "/start    – mensagem inicial\n"
        "/config   – modelo + temperatura\n"
        "/stats    – estatísticas globais\n"
        "/recall <texto>   – debug do RAG (top hits)\n"
        "/history [n]      – últimas n interações suas (default 5)\n"
        "/ping     – health-check Ollama (modelos + dim do embedding)\n"
        "/whoami   – seu user_id e configuração ativa\n"
        "/reset    – volta sua configuração ao padrão\n\n"
        "Mande texto / foto / documento / áudio para conversar."
    )


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


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    deps = _deps(context)
    snap = await deps.sqlite.stats(faiss_indexed=deps.faiss.ntotal)
    avg = f"{snap.avg_latency_ms:.0f}ms" if snap.avg_latency_ms is not None else "—"
    text = (
        "<b>📊 Estatísticas</b>\n"
        f"• Interações:        {snap.total_interactions}\n"
        f"• Avaliadas:         {snap.rated} "
        f"(👍 {snap.positives} / 👎 {snap.negatives})\n"
        f"• Usuários únicos:   {snap.distinct_users}\n"
        f"• Intents distintas: {snap.distinct_intents}\n"
        f"• Latência média:    {avg}\n"
        f"• FAISS indexado:    {snap.faiss_indexed} vetores\n"
        f"• Último run_id:     <code>{snap.last_run_id or '—'}</code>"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_recall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_user is None:
        return
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Uso: /recall <texto>\nMostra o que o RAG recuperaria para essa busca."
        )
        return

    deps = _deps(context)
    query = " ".join(args).strip()

    try:
        bundle: RagBundle = await deps.rag.debug_recall(query)
    except Exception as exc:  # noqa: BLE001
        await update.effective_message.reply_text(f"Erro no recall: {exc}")
        return

    if not bundle.hits:
        await update.effective_message.reply_text(
            "Nenhum hit. FAISS provavelmente está vazio — converse mais para popular."
        )
        return

    lines: list[str] = [
        f"<b>🔎 Recall</b> (dim={bundle.embedding_dim}, "
        f"fallback={'sim' if bundle.fallback_used else 'não'})\n",
    ]
    for h in bundle.hits[:15]:
        score_repr = "—" if h.score is None else str(h.score)
        lines.append(
            f"• id={h.interaction_id}  sim={h.similarity:.3f}  "
            f"score={score_repr}  bucket={h.bucket}"
        )
    if bundle.positive_ids:
        lines.append(f"\n<b>positivos</b>: {bundle.positive_ids}")
    if bundle.negative_ids:
        lines.append(f"<b>negativos</b>: {bundle.negative_ids}")
    if bundle.neutral_ids:
        lines.append(f"<b>neutros (fallback)</b>: {bundle.neutral_ids}")

    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML
    )


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
            f"• id={r.id}  score={score}  intent={escape(intent)}  "
            f"model={escape(r.model_used or '—')}\n"
            f"   ↪ <i>{escape(snippet)}</i>"
        )
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    deps = _deps(context)
    report = await deps.ollama.health_check(expected_dim=deps.settings.embedding_dim)
    ok = report.ollama_reachable and report.chat_model_present and report.embedding_model_present
    head = "🟢" if ok else "🟡" if report.ollama_reachable else "🔴"

    text = (
        f"{head} <b>Ollama health</b>\n"
        f"• alcançável: {report.ollama_reachable}\n"
        f"• modelos: {len(report.models_available)}\n"
        f"• chat model presente: {report.chat_model_present} "
        f"(<code>{escape(deps.settings.ollama_default_model)}</code>)\n"
        f"• embed model presente: {report.embedding_model_present} "
        f"(<code>{escape(deps.settings.ollama_embedding_model)}</code>)\n"
        f"• dim live: {report.embedding_dim_live}  | esperada: {deps.settings.embedding_dim}\n"
        f"• FAISS ntotal: {deps.faiss.ntotal}"
    )
    if report.error:
        text += f"\n⚠️ {escape(report.error)}"
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_user is None:
        return
    deps = _deps(context)
    u = update.effective_user
    s = await deps.sqlite.get_user_settings(u.id)
    text = (
        f"<b>👤 Você</b>\n"
        f"• user_id: <code>{u.id}</code>\n"
        f"• username: @{escape(u.username or '—')}\n"
        f"• modelo:    <code>{escape(s.current_model)}</code>\n"
        f"• temperatura: {s.temperature}\n"
        f"• criado em:   {s.created_at or '—'}\n"
        f"• atualizado:  {s.updated_at or '—'}"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None or update.effective_user is None:
        return
    deps = _deps(context)
    s = await deps.sqlite.reset_user_settings(update.effective_user.id)
    await update.effective_message.reply_text(
        f"Configuração resetada. Agora: modelo=<code>{escape(s.current_model)}</code>, "
        f"temperatura={s.temperature}",
        parse_mode=ParseMode.HTML,
    )


# ─────────────────────────── MENSAGENS ───────────────────────────

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if msg is None or user is None or not msg.text:
        return
    await _process_user_input(
        update=update, context=context, user_id=user.id,
        chat_id=msg.chat_id, text=msg.text,
        media_path=None, media_type="text", images_b64=None,
        forced_intent_hint=None,
    )


async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if msg is None or user is None or not msg.photo:
        return

    deps = _deps(context)
    photo = msg.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    target = deps.settings.media_dir / f"{uuid.uuid4().hex}.jpg"
    await file.download_to_drive(custom_path=str(target))
    log.info("📷 Foto baixada: %s (%d bytes)", target.name, target.stat().st_size)

    user_settings = await deps.sqlite.get_user_settings(user.id)
    if not _model_supports_vision(user_settings.current_model):
        log.warning(
            "Foto recebida com modelo sem visão (%s).", user_settings.current_model
        )
        await msg.reply_text(
            f"⚠️ O modelo atual ({user_settings.current_model}) provavelmente não "
            "enxerga imagens. Use /config e escolha um modelo de visão "
            "(ex.: llava, llama3.2-vision, gemma3, qwen2.5-vl)."
        )

    caption = (msg.caption or "").strip() or "Descreva e analise esta imagem."
    image_b64 = deps.ollama.encode_image_b64(target)

    await _process_user_input(
        update=update, context=context, user_id=user.id,
        chat_id=msg.chat_id, text=caption,
        media_path=str(target), media_type="photo",
        images_b64=[image_b64],
        forced_intent_hint="image_analysis",
    )


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if msg is None or user is None or msg.document is None:
        return

    deps = _deps(context)
    doc = msg.document
    file = await context.bot.get_file(doc.file_id)
    safe_name = (doc.file_name or f"{uuid.uuid4().hex}.bin").replace("/", "_")
    target = deps.settings.media_dir / f"{uuid.uuid4().hex}_{safe_name}"
    await file.download_to_drive(custom_path=str(target))
    log.info("📎 Documento baixado: %s", target.name)

    extracted = _extract_document_text(target).strip()
    truncated = False
    if len(extracted) > _DOC_MAX_CHARS:
        extracted = extracted[: _DOC_MAX_CHARS]
        truncated = True

    user_caption = (msg.caption or "").strip()
    if extracted:
        log.info(
            "📄 Texto extraído de %s: %d chars%s",
            target.name, len(extracted), " (truncado)" if truncated else "",
        )
        header = user_caption or f"Analise o documento anexado: {target.name}"
        suffix = "\n[...conteúdo truncado...]" if truncated else ""
        body = (
            f"{header}\n\n"
            f"[Conteúdo extraído de {target.name}]\n{extracted}{suffix}"
        )
    else:
        body = user_caption or f"Documento recebido: {target.name}"

    await _process_user_input(
        update=update, context=context, user_id=user.id,
        chat_id=msg.chat_id, text=body,
        media_path=str(target), media_type="document",
        images_b64=None, forced_intent_hint=None,
    )


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if msg is None or user is None:
        return

    deps = _deps(context)
    if deps.transcriber is None:
        await msg.reply_text(
            "Transcrição indisponível: configure OPENAI_API_KEY no .env."
        )
        return

    voice_or_audio = msg.voice or msg.audio
    if voice_or_audio is None:
        return

    file = await context.bot.get_file(voice_or_audio.file_id)
    suffix = ".ogg" if msg.voice is not None else ".mp3"
    target: Path = deps.settings.media_dir / f"{uuid.uuid4().hex}{suffix}"
    await file.download_to_drive(custom_path=str(target))
    log.info("🎤 Áudio baixado: %s", target.name)

    await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)

    try:
        text = await deps.transcriber.transcribe(target, language="pt")
    except Exception as exc:  # noqa: BLE001
        log.exception("Falha na transcrição")
        await msg.reply_text(f"Não consegui transcrever o áudio: {exc}")
        return

    log.info("📝 Transcrição (%d chars): %r", len(text), text[:100])
    await msg.reply_text(f"📝 Transcrição: {text}")

    await _process_user_input(
        update=update, context=context, user_id=user.id,
        chat_id=msg.chat_id, text=text,
        media_path=str(target), media_type="voice",
        images_b64=None, forced_intent_hint="voice_transcribed",
    )


# ─────────────────────────── PIPELINE ───────────────────────────

async def _process_user_input(
    *,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    chat_id: int,
    text: str,
    media_path: str | None,
    media_type: str,
    images_b64: list[str] | None,
    forced_intent_hint: str | None,
) -> None:
    msg: Message | None = update.effective_message
    if msg is None:
        return

    deps = _deps(context)
    rec = PipelineRecorder(user_id=user_id, chat_id=chat_id)

    interaction_id: int | None = None
    error_text: str | None = None
    user_settings = None
    tags: list[str] = []
    intent: str = "other"
    bundle: RagBundle | None = None
    chat_result: ChatResult | None = None
    answer: str = ""

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

        async with rec.step(
            "load_user_settings", user_id=user_id, media_type=media_type
        ) as s:
            user_settings = await deps.sqlite.get_user_settings(user_id)
            s.set(model=user_settings.current_model, temperature=user_settings.temperature)

        classify_text = text[:_CLASSIFY_INPUT_MAX_CHARS]
        async with rec.step(
            "classify_intent",
            text_len=len(text),
            truncated=len(text) > _CLASSIFY_INPUT_MAX_CHARS,
        ) as s:
            ir = await deps.intent.classify(classify_text, hint=forced_intent_hint)
            intent = ir.intent
            s.set(intent=ir.intent, confidence=round(ir.confidence, 3), reason=ir.reason[:80])

        async with rec.step(
            "generate_tags", truncated=len(text) > _CLASSIFY_INPUT_MAX_CHARS
        ) as s:
            tags = await deps.tag_gen.generate(classify_text)
            s.set(tags=tags)

        async with rec.step("route_agent", intent=intent, tags=tags) as s:
            decision = _AGENT_ROUTER.decide(tags + [intent])
            s.set(route=decision.route.value, reason=decision.reason)

        async with rec.step(
            "rag_build",
            top_k=deps.settings.rag_top_k,
            n_recent_history=deps.settings.rag_recent_history,
            intent=intent,
        ) as s:
            bundle = await deps.rag.build(
                text,
                user_id=user_id,
                n_recent_history=deps.settings.rag_recent_history,
                intent=intent,
                now_iso=_now_local_iso(),
            )
            s.set(
                hits=len(bundle.hits),
                positives=len(bundle.positives),
                negatives=len(bundle.negatives),
                neutral=len(bundle.neutral),
                history=len(bundle.history),
                fallback_used=bundle.fallback_used,
                embedding_dim=bundle.embedding_dim,
            )

        tool_invocations: list[dict[str, Any]] = []
        async with rec.step(
            "ollama_chat",
            model=user_settings.current_model,
            temperature=user_settings.temperature,
            tools=len(deps.tools.specs_for_ollama()),
        ) as s:
            tool_specs = deps.tools.specs_for_ollama() or None
            messages: list[ChatMessage] = [
                ChatMessage(role="system", content=bundle.system_prompt),
                ChatMessage(role="user", content=bundle.user_prompt, images_b64=images_b64),
            ]
            chat_result = await deps.ollama.chat(
                messages=messages,
                model=user_settings.current_model,
                temperature=user_settings.temperature,
                tools=tool_specs,
            )

            tool_iter = 0
            while chat_result.tool_calls and tool_iter < _TOOL_LOOP_MAX_ITER:
                tool_iter += 1
                # echo do turno do assistente que pediu as tools
                messages.append(
                    ChatMessage(
                        role="assistant",
                        content=chat_result.content or "",
                        tool_calls=chat_result.tool_calls,
                    )
                )
                for call in chat_result.tool_calls:
                    fn = call.get("function") or {}
                    tname = str(fn.get("name") or "")
                    raw_args = fn.get("arguments") or {}
                    args = raw_args if isinstance(raw_args, dict) else {}
                    log.info(
                        "🔧 tool call #%d: %s(%s)",
                        tool_iter, tname, json.dumps(args, ensure_ascii=False),
                    )
                    try:
                        result = await deps.tools.dispatch(tname, args)
                        result_str = json.dumps(result, ensure_ascii=False, default=str)
                        ok = True
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Tool %s falhou: %s", tname, exc)
                        result_str = json.dumps(
                            {"error": f"{type(exc).__name__}: {exc}"},
                            ensure_ascii=False,
                        )
                        ok = False
                    tool_invocations.append({
                        "iteration": tool_iter,
                        "name": tname,
                        "arguments": args,
                        "ok": ok,
                        "result": result_str[:1000],
                    })
                    messages.append(
                        ChatMessage(role="tool", content=result_str, name=tname)
                    )
                chat_result = await deps.ollama.chat(
                    messages=messages,
                    model=user_settings.current_model,
                    temperature=user_settings.temperature,
                    tools=tool_specs,
                )

            answer = chat_result.content.strip() or "(modelo retornou vazio)"
            s.set(
                response_chars=len(answer),
                prompt_tokens=chat_result.prompt_tokens,
                response_tokens=chat_result.response_tokens,
                ollama_total_ms=chat_result.total_duration_ms,
                tool_iterations=tool_iter,
                tool_calls_executed=len(tool_invocations),
            )

        async with rec.step("save_interaction") as s:
            interaction_id = await deps.sqlite.insert_interaction(
                user_id=user_id, chat_id=chat_id,
                user_message=text, bot_response=answer,
                tags=tags, intent=intent,
                model_used=chat_result.model,
                temperature=user_settings.temperature,
                prompt_tokens=chat_result.prompt_tokens,
                response_tokens=chat_result.response_tokens,
                total_duration_ms=chat_result.total_duration_ms,
                prompt_used=bundle.user_prompt,
                positive_ids=bundle.positive_ids,
                negative_ids=bundle.negative_ids,
                retrieved_count=len(bundle.hits),
                embedding_model=bundle.embedding_model,
                embedding_dim=bundle.embedding_dim or None,
                tool_calls=tool_invocations,
                media_path=media_path,
                media_type=media_type,
                error=None,
                run_id=rec.run.run_id,
            )
            s.set(interaction_id=interaction_id)

        async with rec.step("index_interaction_embedding") as s:
            raw_embed_text = f"USER: {text}\nBOT: {answer}"
            embed_text = raw_embed_text[:_EMBED_INPUT_MAX_CHARS]
            embed_truncated = len(raw_embed_text) > _EMBED_INPUT_MAX_CHARS
            if embed_truncated:
                log.info(
                    "Embedding: input truncado %d → %d chars",
                    len(raw_embed_text), _EMBED_INPUT_MAX_CHARS,
                )
            t0 = time.monotonic()
            vec = await deps.ollama.embed(embed_text)
            await deps.faiss.add(interaction_id, vec)
            s.set(
                embed_ms=int((time.monotonic() - t0) * 1000),
                vec_dim=int(vec.shape[-1]),
                faiss_total=deps.faiss.ntotal,
                input_chars=len(embed_text),
                truncated=embed_truncated,
            )

        async with rec.step("send_reply") as s:
            await msg.reply_text(text=answer, reply_markup=_rating_keyboard(interaction_id))
            s.set(reply_chars=len(answer))

    except Exception as exc:  # noqa: BLE001
        error_text = f"{type(exc).__name__}: {exc}"
        log.exception("Pipeline falhou: %s", error_text)
        try:
            await msg.reply_text(f"⚠️ Erro: {error_text}")
        except Exception:  # noqa: BLE001
            pass

    finally:
        log.info("\n%s", rec.summary())
        try:
            await deps.sqlite.save_pipeline_steps(
                run_id=rec.run.run_id,
                user_id=user_id,
                chat_id=chat_id,
                interaction_id=interaction_id,
                steps=rec.to_rows(),
            )
        except Exception as exc:  # noqa: BLE001
            log.error("Não foi possível persistir pipeline_steps: %s", exc)
