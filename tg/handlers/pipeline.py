# tg/handlers/pipeline.py
from __future__ import annotations

import json
import re
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
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from agents.router import AgentRouter
from core.chat_runner import ChatRunResult, run_chat_with_fallback
from core.codes import format_hashtag, parse_code
from core.logger import get_logger
from core.permissions import (
    GLOBAL_ROLE_ADMIN,
    GLOBAL_ROLE_MEMBER,
    GLOBAL_ROLE_SUPERADMIN,
    default_member_permissions,
    project_role_implies_global_role,
)
from core.pipeline import PipelineRecorder
from database.repos.chunks import ChunkInsert
from llm.contrastive_rag import RagBundle
from llm.ollama_client import ChatMessage, ChatResult
from tg.kb import rating_keyboard
from tg.middleware import require_active_user

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

# ─── Chunking ───────────────────────────────────────────────────────────────
# Pesos por classe de documento (conforme Hierarquia de documentos.md).
_CLASS_WEIGHTS: dict[str, float] = {
    "contract": 1.5,
    "spec":     1.4,
    "norm":     1.3,
    "proposal": 1.1,
    "note":     1.0,
    "other":    0.8,
    "meeting":  0.7,
}

# Mapa de hashtags de caption para doc_class.
_CAPTION_CLASS_MAP: dict[str, str] = {
    "#contrato":      "contract",  "#aditivo":       "contract",
    "#memorial":      "spec",      "#projeto":        "spec",      "#especificacao": "spec",
    "#norma":         "norm",      "#nr":             "norm",
    "#proposta":      "proposal",  "#escopo":         "proposal",
    "#nota":          "note",      "#anotacao":       "note",
    "#reuniao":       "meeting",   "#ata":            "meeting",
}

# Boost por role global (nível de acesso conforme Níveis de acesso.md).
_ROLE_BOOST: dict[str, float] = {
    "superadmin": 0.2,
    "admin":      0.2,
    "engineer":   0.1,
    "supervisor": 0.1,
}


def _file_too_big(size_bytes: int | None, limit_mb: int) -> tuple[bool, float]:
    """Retorna (excede_limite, tamanho_em_mb). None ⇒ sem informação, deixa passar."""
    if size_bytes is None:
        return False, 0.0
    mb = size_bytes / (1024 * 1024)
    return mb > limit_mb, mb


def _chunk_text_fixed(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Sliding-window fallback para parágrafos maiores que chunk_size."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start += chunk_size - overlap
    return chunks


def _chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Chunking semântico: respeita quebras de parágrafo. Fallback para sliding-window."""
    import re as _re
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    paragraphs = [p.strip() for p in _re.split(r"\n\n+", text) if p.strip()]
    if not paragraphs:
        return _chunk_text_fixed(text, chunk_size, overlap)
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= chunk_size:
            current = (current + "\n\n" + para).strip()
        else:
            if current:
                chunks.append(current)
            if len(para) > chunk_size:
                chunks.extend(_chunk_text_fixed(para, chunk_size, overlap))
                current = ""
            else:
                current = para
    if current:
        chunks.append(current)
    return chunks or [text]


def _parse_doc_class(caption: str) -> str:
    """Extrai doc_class a partir de hashtags na caption. Fallback = 'note'."""
    for tag, cls in _CAPTION_CLASS_MAP.items():
        if tag in caption.lower():
            return cls
    return "note"


def _sender_boost(role: str) -> float:
    return _ROLE_BOOST.get(role, 0.0)


def _deps(context: ContextTypes.DEFAULT_TYPE) -> "BotDependencies":
    return context.application.bot_data["deps"]  # type: ignore[no-any-return]


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


# Tabela de substituição para artefatos LaTeX que o modelo às vezes vomita.
_LATEX_LITERAL_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (r"\rightarrow", "→"),
    (r"\Rightarrow", "⇒"),
    (r"\leftarrow", "←"),
    (r"\Leftarrow", "⇐"),
    (r"\to", "→"),
    (r"\times", "×"),
    (r"\cdot", "·"),
    (r"\leq", "≤"),
    (r"\geq", "≥"),
    (r"\neq", "≠"),
    (r"\approx", "≈"),
    (r"\pm", "±"),
    (r"\infty", "∞"),
    (r"\alpha", "α"), (r"\beta", "β"), (r"\gamma", "γ"), (r"\delta", "δ"),
    (r"\theta", "θ"), (r"\lambda", "λ"), (r"\mu", "μ"), (r"\sigma", "σ"),
    (r"\pi", "π"), (r"\phi", "φ"), (r"\omega", "ω"),
)

_INLINE_MATH_RE = re.compile(r"\$([^$\n]{1,200})\$")
_DISPLAY_MATH_RE = re.compile(r"\$\$([^$]{1,500})\$\$", re.DOTALL)
_PAREN_MATH_RE = re.compile(r"\\\(([^)]{1,200})\\\)")
_BRACKET_MATH_RE = re.compile(r"\\\[([^\]]{1,500})\\\]", re.DOTALL)


def _sanitize_for_telegram(text: str) -> str:
    """Tira artefatos LaTeX que aparecem crus no app do Telegram."""
    out = text
    for needle, repl in _LATEX_LITERAL_REPLACEMENTS:
        out = out.replace(needle, repl)
    # `$\rightarrow$` já virou `$→$` — agora colapso os $ residuais.
    for rgx in (_DISPLAY_MATH_RE, _PAREN_MATH_RE, _BRACKET_MATH_RE, _INLINE_MATH_RE):
        out = rgx.sub(lambda m: m.group(1).strip(), out)
    return out


async def _safe_reply(msg: Message, text: str, **kwargs: Any) -> Message:
    """Tenta enviar como Markdown; se o parser explodir, manda como texto puro.

    Telegram Markdown legacy é estrito com `_`, `*`, `[` desbalanceados — em vez
    de tentar escapar perfeitamente, deixamos o tipo errado virar plain text.
    """
    try:
        return await msg.reply_text(text=text, parse_mode=ParseMode.MARKDOWN, **kwargs)
    except BadRequest as exc:
        if "parse" not in str(exc).lower() and "entity" not in str(exc).lower():
            raise
        log.info("Markdown falhou (%s) — reenviando como texto puro.", exc)
        return await msg.reply_text(text=text, **kwargs)


# ─────────────────────────── MENSAGENS ───────────────────────────

async def _handle_rdo_text_input(
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

    from datetime import datetime
    dia = datetime.now().astimezone().strftime("%Y-%m-%d")

    try:
        if rdo_type == "efetivo":
            from tg.handlers.rdo.diario import _parse_efetivo_args
            parsed = _parse_efetivo_args(text)
            if parsed is None:
                await msg.reply_text("Não entendi. Use: `Função; qtd` ou `Função qtd`")
                return
            funcao_nome, qtd_raw, empresa_ref = parsed
            funcao = await deps.sqlite.funcoes.get_by_nome(funcao_nome)
            if funcao is None:
                await msg.reply_text(f"Função '{funcao_nome}' não existe. Veja /funcoes.")
                return
            qtd = int(qtd_raw)
            await deps.sqlite.efetivo.insert(
                project_id=project.id, dia=dia, funcao_id=funcao.id,
                empresa_id=None, qtd=qtd, criado_por=user.id,
            )
            await msg.reply_text(f"👷 Efetivo registrado: {qtd}× {funcao.nome} — {dia}")

        elif rdo_type == "atividade":
            from database.repos.atividades import normalizar_estado
            from tg.handlers.rdo.diario import _parse_atividade_args
            parsed2 = _parse_atividade_args(text)
            if parsed2 is None:
                descricao, estado = text, "em_andamento"
            else:
                descricao, estado_raw = parsed2
                from core.exceptions import StorageError
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

        elif rdo_type == "anotacao":
            await deps.sqlite.anotacoes.insert(
                project_id=project.id, dia=dia, texto=text, criado_por=user.id,
            )
            await msg.reply_text(f"📝 Anotação registrada — {dia}")

        else:
            await msg.reply_text("Tipo de entrada não reconhecido.")
    except Exception as exc:
        await msg.reply_text(f"Erro ao registrar: {exc}")


@require_active_user
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if msg is None or user is None or not msg.text:
        return
    text = msg.text.strip()

    # Intercepta awaiting_correction (feedback ruim) — ignora comandos.
    if context.user_data.get("awaiting_correction") and not text.startswith("/"):
        iid = context.user_data.pop("awaiting_correction")
        deps = _deps(context)
        await deps.sqlite.set_correction(iid, text)
        await msg.reply_text("Anotado. O modelo vai evitar isso nas próximas respostas.")
        return

    # Intercepta awaiting_rdo (entrada de texto após menu)
    if context.user_data.get("awaiting_rdo"):
        await _handle_rdo_text_input(update, context, text)
        return

    # Menu RDO inline (botão persistente ou texto)
    if text.lower().strip().lstrip("📋").strip() == "menu":
        from tg.kb import rdo_menu_keyboard
        await msg.reply_text("📋 RDO do dia:", reply_markup=rdo_menu_keyboard())
        return

    await _process_user_input(
        update=update, context=context, user_id=user.id,
        chat_id=msg.chat_id, text=text,
        media_path=None, media_type="text", images_b64=None,
        forced_intent_hint=None,
    )


@require_active_user
async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if msg is None or user is None or not msg.photo:
        return

    deps = _deps(context)
    photo = msg.photo[-1]
    too_big, mb = _file_too_big(photo.file_size, deps.settings.telegram_download_max_mb)
    if too_big:
        await msg.reply_text(
            f"⚠️ Imagem com {mb:.1f} MB excede o limite do Telegram bot API "
            f"({deps.settings.telegram_download_max_mb} MB). Reduza o tamanho."
        )
        return

    try:
        file = await context.bot.get_file(photo.file_id)
        target = deps.settings.media_dir / f"{uuid.uuid4().hex}.jpg"
        await file.download_to_drive(custom_path=str(target))
    except BadRequest as exc:
        log.warning("Falha ao baixar foto: %s", exc)
        await msg.reply_text(f"⚠️ Não consegui baixar essa imagem: {exc}")
        return
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


@require_active_user
async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    user = update.effective_user
    if msg is None or user is None or msg.document is None:
        return

    deps = _deps(context)
    doc = msg.document
    too_big, mb = _file_too_big(doc.file_size, deps.settings.telegram_download_max_mb)
    if too_big:
        await msg.reply_text(
            f"⚠️ Documento com {mb:.1f} MB excede o limite do Telegram bot API "
            f"({deps.settings.telegram_download_max_mb} MB). "
            f"Quebre em partes menores ou compartilhe por outro canal."
        )
        return

    try:
        file = await context.bot.get_file(doc.file_id)
        safe_name = (doc.file_name or f"{uuid.uuid4().hex}.bin").replace("/", "_")
        target = deps.settings.media_dir / f"{uuid.uuid4().hex}_{safe_name}"
        await file.download_to_drive(custom_path=str(target))
    except BadRequest as exc:
        log.warning("Falha ao baixar documento: %s", exc)
        await msg.reply_text(f"⚠️ Não consegui baixar o documento: {exc}")
        return
    log.info("📎 Documento baixado: %s (%d bytes)", target.name, target.stat().st_size)

    extracted = _extract_document_text(target).strip()
    # Não truncamos o texto de extração aqui — o chunking lida com documentos longos.
    user_caption = (msg.caption or "").strip()
    doc_class = _parse_doc_class(user_caption)

    if extracted:
        log.info(
            "📄 Texto extraído de %s: %d chars (classe=%s)",
            target.name, len(extracted), doc_class,
        )
        header = user_caption or f"Analise o documento anexado: {target.name}"
        # Para o prompt LLM, mantemos o limite anterior.
        extracted_for_prompt = extracted[:_DOC_MAX_CHARS]
        suffix = "\n[...conteúdo truncado...]" if len(extracted) > _DOC_MAX_CHARS else ""
        body = (
            f"{header}\n\n"
            f"[Conteúdo extraído de {target.name}]\n{extracted_for_prompt}{suffix}"
        )
    else:
        body = user_caption or f"Documento recebido: {target.name}"

    await _process_user_input(
        update=update, context=context, user_id=user.id,
        chat_id=msg.chat_id, text=body,
        media_path=str(target), media_type="document",
        images_b64=None, forced_intent_hint=None,
        full_doc_text=extracted or None,
        doc_class=doc_class,
    )


@require_active_user
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

    # 1) Limite do Telegram bot API (download via getFile).
    tg_limit = deps.settings.telegram_download_max_mb
    too_big, mb = _file_too_big(voice_or_audio.file_size, tg_limit)
    if too_big:
        await msg.reply_text(
            f"⚠️ Áudio com {mb:.1f} MB excede o limite do Telegram bot API "
            f"({tg_limit} MB). Mande em partes menores."
        )
        return

    # 2) Limite do Whisper (transcrição).
    whisper_limit = deps.settings.whisper_max_mb
    if voice_or_audio.file_size and voice_or_audio.file_size > whisper_limit * 1024 * 1024:
        await msg.reply_text(
            f"⚠️ Áudio com {mb:.1f} MB excede o limite do Whisper "
            f"({whisper_limit} MB). Quebre em partes menores."
        )
        return

    try:
        file = await context.bot.get_file(voice_or_audio.file_id)
        suffix = ".ogg" if msg.voice is not None else ".mp3"
        target: Path = deps.settings.media_dir / f"{uuid.uuid4().hex}{suffix}"
        await file.download_to_drive(custom_path=str(target))
    except BadRequest as exc:
        log.warning("Falha ao baixar áudio: %s", exc)
        await msg.reply_text(f"⚠️ Não consegui baixar o áudio: {exc}")
        return
    log.info("🎤 Áudio baixado: %s (%d bytes)", target.name, target.stat().st_size)

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
    full_doc_text: str | None = None,   # texto completo do doc (para chunking)
    doc_class: str = "note",            # classe do documento
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
    _pending_rdo = None

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
            # Token tracking para classify_intent.
            _prompt_tok = getattr(ir, "prompt_tokens", None) or (len(classify_text) // 4)
            await deps.sqlite.token_usage.insert(
                run_id=rec.run.run_id, user_id=user_id,
                model=deps.settings.ollama_default_model, backend="ollama",
                operation="classify_intent",
                prompt_tokens=_prompt_tok, response_tokens=0,
                project_id=None,  # settings ainda não carregado
            )

        async with rec.step(
            "generate_tags", truncated=len(text) > _CLASSIFY_INPUT_MAX_CHARS
        ) as s:
            tags = await deps.tag_gen.generate(classify_text)
            s.set(tags=tags)
            _tag_prompt_tok = len(classify_text) // 4
            await deps.sqlite.token_usage.insert(
                run_id=rec.run.run_id, user_id=user_id,
                model=deps.settings.ollama_default_model, backend="ollama",
                operation="generate_tags",
                prompt_tokens=_tag_prompt_tok, response_tokens=0,
                project_id=None,
            )

        async with rec.step("route_agent", intent=intent, tags=tags) as s:
            decision = _AGENT_ROUTER.decide(tags + [intent])
            s.set(route=decision.route.value, reason=decision.reason)

        # Monta contexto da obra ativa pra injetar no system_prompt.
        # Sem isso, a IA "esquece" em que obra estamos e não consegue responder
        # perguntas como "qual obra estamos?" ou citar o nome da obra na resposta.
        obra_context: str | None = None
        active_project_id = (
            user_settings.current_project_id if user_settings else None
        )
        if active_project_id is not None:
            proj = await deps.sqlite.projects.get_by_id(active_project_id)
            if proj is not None:
                obra_context = (
                    f"Obra ativa: {proj.name} (#{proj.uid}).\n"
                    f"Use esse nome quando o usuário perguntar a obra. "
                    f"Os comandos /clima, /efetivo, /atividade, /anotacao e "
                    f"/rdo gravam diretamente no banco dessa obra — quando o "
                    f"usuário pedir pra registrar algo do diário, oriente-o "
                    f"a usar esses comandos (cite o exemplo curto). Você "
                    f"NÃO escreve direto no banco pelo chat; quem grava são "
                    f"os comandos."
                )

        async with rec.step(
            "rag_build",
            top_k=deps.settings.rag_top_k,
            n_recent_history=deps.settings.rag_recent_history,
            intent=intent,
        ) as s:
            bundle = await deps.rag.build(
                text,
                user_id=user_id,
                project_id=active_project_id,
                n_recent_history=deps.settings.rag_recent_history,
                intent=intent,
                now_iso=_now_local_iso(),
                obra_context=obra_context,
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
            base_messages: list[ChatMessage] = [
                ChatMessage(role="system", content=bundle.system_prompt),
                ChatMessage(role="user", content=bundle.user_prompt, images_b64=images_b64),
            ]

            notice_msg: Message | None = None

            async def _on_first_failure(exc: Exception, primary_model: str) -> None:
                nonlocal notice_msg
                try:
                    notice_msg = await msg.reply_text(
                        "⚠️ Probleminha técnico no modelo principal. "
                        "Tô tentando outro caminho, já volto…"
                    )
                except Exception as cb_exc:  # noqa: BLE001
                    log.warning("Não consegui avisar usuário: %s", cb_exc)

            async def _dispatch(name: str, args: dict[str, Any]) -> Any:
                return await deps.tools.dispatch(
                    name, args,
                    ctx={
                        "user_id": user_id,
                        "chat_id": chat_id,
                        "project_id": active_project_id,
                        "sqlite": deps.sqlite,
                    },
                )

            run: ChatRunResult = await run_chat_with_fallback(
                ollama=deps.ollama,
                openai=deps.openai_chat,
                base_messages=base_messages,
                primary_model=user_settings.current_model,
                temperature=user_settings.temperature,
                tools=tool_specs,
                tool_dispatcher=_dispatch,
                fallback_models=deps.settings.chat_fallback_models,
                openai_fallback_model=(
                    deps.settings.openai_chat_fallback_model
                    if deps.openai_chat is not None
                    else None
                ),
                max_tool_iter=_TOOL_LOOP_MAX_ITER,
                on_first_failure=_on_first_failure,
            )

            chat_result = run.chat_result
            tool_invocations = run.tool_invocations
            tool_iter = run.tool_iterations
            answer = chat_result.content.strip() or "(modelo retornou vazio)"

            # Limpa o aviso temporário se o fallback resolveu.
            if notice_msg is not None:
                try:
                    await notice_msg.delete()
                except Exception as exc:  # noqa: BLE001
                    log.debug("Não consegui apagar aviso de fallback: %s", exc)

            s.set(
                response_chars=len(answer),
                prompt_tokens=chat_result.prompt_tokens,
                response_tokens=chat_result.response_tokens,
                ollama_total_ms=chat_result.total_duration_ms,
                tool_iterations=tool_iter,
                tool_calls_executed=len(tool_invocations),
                model_used=run.model_used,
                backend=run.backend,
                fell_back=run.fell_back,
                primary_error=run.primary_error,
            )
            # Token tracking para chat.
            await deps.sqlite.token_usage.insert(
                run_id=rec.run.run_id, user_id=user_id,
                interaction_id=interaction_id,
                project_id=user_settings.current_project_id if user_settings else None,
                model=run.model_used or deps.settings.ollama_default_model,
                backend=run.backend or "ollama",
                operation="chat",
                prompt_tokens=chat_result.prompt_tokens or 0,
                response_tokens=chat_result.response_tokens or 0,
                duration_ms=chat_result.total_duration_ms or 0,
            )

        # Verifica se alguma tool retornou pending_rdo (registro a confirmar)
        for _inv in tool_invocations:
            _result = _inv.get("result", {})
            if isinstance(_result, dict) and _result.get("action") == "pending_rdo":
                _pending_rdo = _result
                break
        if _pending_rdo is not None:
            context.user_data["pending_rdo"] = _pending_rdo

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
                project_id=user_settings.current_project_id,
            )
            s.set(interaction_id=interaction_id, project_id=user_settings.current_project_id)

        async with rec.step("index_interaction_embedding") as s:
            embed_model = deps.settings.ollama_embedding_model
            chunk_size = deps.settings.chunk_size
            chunk_overlap = deps.settings.chunk_overlap

            # Texto base para embedding: prefer full doc text se disponível.
            base_text = full_doc_text or f"USER: {text}\nBOT: {answer}"

            # Determina peso para os chunks.
            bot_user_obj = await deps.sqlite.users.get_by_id(user_id)
            role = bot_user_obj.role if bot_user_obj else "worker"
            boost = _sender_boost(role)
            weight = _CLASS_WEIGHTS.get(doc_class, 1.0) * (1.0 + boost)

            raw_chunks = _chunk_text(base_text, chunk_size, chunk_overlap)

            t0 = time.monotonic()
            chunk_inserts: list[ChunkInsert] = []
            vectors: list = []
            for idx, chunk_text_item in enumerate(raw_chunks):
                embed_input = chunk_text_item[:_EMBED_INPUT_MAX_CHARS]
                vec = await deps.ollama.embed(embed_input)
                vectors.append(vec)
                chunk_inserts.append(
                    ChunkInsert(
                        chunk_idx=idx,
                        content=chunk_text_item[:500],  # resumo do chunk no DB
                        doc_class=doc_class,
                        weight=weight,
                    )
                )

            chunk_ids = await deps.sqlite.chunks.insert_many(
                interaction_id, chunk_inserts
            )
            await deps.faiss.add_many(chunk_ids, vectors)

            embed_ms = int((time.monotonic() - t0) * 1000)
            # Token tracking para embedding.
            total_embed_chars = sum(len(c.content) for c in chunk_inserts)
            await deps.sqlite.token_usage.insert(
                run_id=rec.run.run_id, user_id=user_id,
                interaction_id=interaction_id,
                project_id=user_settings.current_project_id if user_settings else None,
                model=embed_model, backend="ollama",
                operation="embedding",
                prompt_tokens=total_embed_chars // 4, response_tokens=0,
                duration_ms=embed_ms,
                quantity_secondary=float(len(raw_chunks)),
            )

            s.set(
                embed_ms=embed_ms,
                vec_dim=int(vectors[0].shape[-1]) if vectors else 0,
                faiss_total=deps.faiss.ntotal,
                n_chunks=len(raw_chunks),
                weight=round(weight, 3),
                doc_class=doc_class,
            )

        async with rec.step("send_reply") as s:
            cleaned = _sanitize_for_telegram(answer)
            tagged = f"{cleaned}\n\n─ {format_hashtag(interaction_id)}"
            if _pending_rdo is not None:
                from tg.kb import confirm_rdo_keyboard
                rdo_type = _pending_rdo.get("type", "registro")
                await _safe_reply(
                    msg, tagged,
                    reply_markup=confirm_rdo_keyboard(
                        confirm_data=f"rdo:confirm:{rdo_type}",
                        skip_data=f"rdo:skip:{rdo_type}",
                    ),
                )
            else:
                await _safe_reply(
                    msg, tagged, reply_markup=rating_keyboard(interaction_id)
                )
            s.set(
                reply_chars=len(tagged),
                sanitized=cleaned != answer,
                code=format_hashtag(interaction_id),
            )

    except Exception as exc:  # noqa: BLE001
        error_text = f"{type(exc).__name__}: {exc}"
        log.exception("Pipeline falhou: %s", error_text)
        try:
            await msg.reply_text(f"⚠️ Erro: {error_text}")
        except Exception:  # noqa: BLE001
            pass

    finally:
        total_ms = rec.run.total_ms
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

        # DebugNotifier.
        if deps.debug_notifier is not None:
            try:
                proj_name: str | None = None
                if user_settings and user_settings.current_project_id:
                    proj = await deps.sqlite.projects.get_by_id(
                        user_settings.current_project_id
                    )
                    proj_name = proj.name if proj else None
                tg_user = getattr(update.effective_user, "first_name", None) or str(user_id)

                if error_text:
                    await deps.debug_notifier.notify_error(
                        run_id=rec.run.run_id,
                        user_name=tg_user,
                        project_name=proj_name,
                        error=error_text,
                        duration_ms=total_ms,
                    )
                else:
                    cost_usd = 0.0
                    if chat_result is not None:
                        cost_usd = await deps.sqlite.model_pricing.calc_cost(
                            model=chat_result.model or deps.settings.ollama_default_model,
                            prompt_tokens=chat_result.prompt_tokens or 0,
                            response_tokens=chat_result.response_tokens or 0,
                        )
                    await deps.debug_notifier.notify_pipeline_run(
                        run_id=rec.run.run_id,
                        user_name=tg_user,
                        project_name=proj_name,
                        model=chat_result.model if chat_result else deps.settings.ollama_default_model,
                        backend="ollama",
                        intent=intent,
                        tags=tags,
                        prompt_tokens=chat_result.prompt_tokens or 0 if chat_result else 0,
                        response_tokens=chat_result.response_tokens or 0 if chat_result else 0,
                        cost_usd=cost_usd,
                        duration_ms=total_ms,
                    )
            except Exception as notify_exc:  # noqa: BLE001
                log.warning("DebugNotifier falhou: %s", notify_exc)


_chunk_text_semantic = _chunk_text  # alias para testes
