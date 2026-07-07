# tg/handlers/doc.py

"""
/doc — entrada explícita e classificada de documento no RAG da obra.

Fluxo (ACL simplificado, decisão 2026-06):
1. Usuário responde (reply) a um documento ou texto com `/doc <classe> [título]`.
2. Só quem tem nível <= nivel_min_classificar da classe pode classificar.
3. Classe sensível pede confirmação explícita — depois de indexado, QUALQUER
   membro da obra lê via RAG. A segurança está nessa decisão consciente.
4. Indexação: interação de log + row em documents + chunks com peso da classe.
"""

from __future__ import annotations

import hashlib
import uuid
from html import escape
from typing import TYPE_CHECKING, Any

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from core.logger import get_logger
from core.permissions import user_level_in_project
from core.uid import gen_uid
from database.models import DocClass
from database.repos.chunks import ChunkInsert
from tg.handlers.pipeline import (
    _EMBED_INPUT_MAX_CHARS,
    _chunk_text,
    _extract_document_text,
    _file_too_big,
)
from tg.kb import doc_confirm_keyboard
from tg.middleware import (
    get_bot_member,
    get_bot_project,
    get_bot_user,
    require_active_project,
)

if TYPE_CHECKING:
    from tg.bot import BotDependencies

log = get_logger(__name__)

# Classes que pedem confirmação antes de indexar (plano dual-RAG parte 2).
_SENSITIVE_CLASSES: frozenset[str] = frozenset({
    "folha_pgto", "planilha_orcamento", "contrato", "proposta",
})

# Conteúdo guardado na interação de log — é o que o RAG mostra no prompt.
_DOC_LOG_MAX_CHARS: int = 4000

_NIVEL_LABEL: dict[int, str] = {1: "N1 admin", 2: "N2 co-resp.", 3: "N3 todos"}


def _deps(context: ContextTypes.DEFAULT_TYPE) -> "BotDependencies":
    return context.application.bot_data["deps"]  # type: ignore[no-any-return]


async def _usage(msg, deps: "BotDependencies") -> None:
    classes = await deps.sqlite.doc_classes.list_active()
    lines = [
        "<b>📁 /doc — indexar documento classificado</b>\n",
        "Responda (reply) a um <b>arquivo</b> ou <b>texto</b> com:",
        "<code>/doc &lt;classe&gt; [título]</code>\n",
        "<b>Classes disponíveis:</b>",
    ]
    for c in classes:
        nivel = _NIVEL_LABEL.get(c.nivel_min_classificar, f"N{c.nivel_min_classificar}")
        aviso = " ⚠️" if c.slug in _SENSITIVE_CLASSES else ""
        lines.append(
            f"• <code>{escape(c.slug)}</code> — {escape(c.label)} "
            f"<i>(peso {c.peso:g}, {escape(nivel)})</i>{aviso}"
        )
    lines.append(
        "\n⚠️ = pede confirmação: depois de indexado, o conteúdo fica "
        "disponível pra <b>todos os membros da obra</b>."
    )
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def _resolve_content(
    msg, context: ContextTypes.DEFAULT_TYPE, deps: "BotDependencies"
) -> tuple[str, str | None, str | None, str | None] | None:
    """Extrai (conteudo, arquivo_path, arquivo_hash, mime) da mensagem citada.

    Retorna None (com aviso já enviado) se não houver conteúdo utilizável.
    """
    reply = msg.reply_to_message
    if reply is None:
        await msg.reply_text(
            "Responda (reply) à mensagem com o documento ou texto que você "
            "quer indexar. Use /doc sem argumentos pra ver as classes."
        )
        return None

    if reply.document is not None:
        doc = reply.document
        too_big, mb = _file_too_big(
            doc.file_size, deps.settings.telegram_download_max_mb
        )
        if too_big:
            await msg.reply_text(
                f"⚠️ Documento com {mb:.1f} MB excede o limite "
                f"({deps.settings.telegram_download_max_mb} MB)."
            )
            return None
        try:
            file = await context.bot.get_file(doc.file_id)
            safe_name = (doc.file_name or f"{uuid.uuid4().hex}.bin").replace("/", "_")
            target = deps.settings.media_dir / f"{uuid.uuid4().hex}_{safe_name}"
            await file.download_to_drive(custom_path=str(target))
        except BadRequest as exc:
            await msg.reply_text(f"⚠️ Não consegui baixar o documento: {exc}")
            return None
        conteudo = _extract_document_text(target).strip()
        if not conteudo:
            await msg.reply_text(
                "⚠️ Não consegui extrair texto desse arquivo (formato não "
                "suportado ou PDF sem camada de texto)."
            )
            return None
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        return conteudo, str(target), sha, doc.mime_type

    texto = (reply.text or reply.caption or "").strip()
    if not texto:
        await msg.reply_text("A mensagem citada não tem texto nem documento.")
        return None
    return texto, None, None, None


@require_active_project
async def cmd_doc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    assert msg is not None
    deps = _deps(context)
    project = get_bot_project(context)
    user = get_bot_user(context)
    member = get_bot_member(context)

    args = context.args or []
    if not args:
        await _usage(msg, deps)
        return

    slug = args[0].lower().strip()
    doc_class = await deps.sqlite.doc_classes.get(slug)
    if doc_class is None or not doc_class.ativo:
        await msg.reply_text(
            f"Classe `{slug}` não existe. Use /doc sem argumentos pra ver a lista.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    nivel = user_level_in_project(user, member)
    if nivel > doc_class.nivel_min_classificar:
        await msg.reply_text(
            f"⛔ Classificar como <b>{escape(doc_class.label)}</b> exige nível "
            f"{_NIVEL_LABEL.get(doc_class.nivel_min_classificar, '?')} nessa obra.",
            parse_mode=ParseMode.HTML,
        )
        return

    resolved = await _resolve_content(msg, context, deps)
    if resolved is None:
        return
    conteudo, arquivo_path, arquivo_hash, mime = resolved
    titulo = " ".join(args[1:]).strip() or conteudo[:60].replace("\n", " ")

    payload: dict[str, Any] = {
        "slug": doc_class.slug,
        "titulo": titulo,
        "conteudo": conteudo,
        "arquivo_path": arquivo_path,
        "arquivo_hash": arquivo_hash,
        "mime": mime,
    }

    if doc_class.slug in _SENSITIVE_CLASSES:
        context.user_data["pending_doc"] = payload
        await msg.reply_text(
            f"⚠️ <b>{escape(doc_class.label)}</b> — este documento ficará "
            f"disponível para <b>todos os membros</b> de "
            f"<b>{escape(project.name)}</b> após indexação.\n\nConfirmar?",
            parse_mode=ParseMode.HTML,
            reply_markup=doc_confirm_keyboard(),
        )
        return

    await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)
    await _index_document(msg, context, payload)


async def on_doc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callbacks `doc:confirm` / `doc:cancel` da confirmação de classe sensível."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    action = (query.data or "").split(":", 1)[-1]

    pending = (context.user_data or {}).pop("pending_doc", None)
    if action == "cancel" or pending is None:
        await query.edit_message_text(
            "❌ Indexação cancelada. Nada foi gravado."
            if pending is not None
            else "Nada pendente — o pedido expirou. Refaça o /doc."
        )
        return

    await query.edit_message_text("⏳ Indexando documento…")
    await _index_document(query.message, context, pending)


async def _index_document(
    msg, context: ContextTypes.DEFAULT_TYPE, payload: dict[str, Any]
) -> None:
    """Grava documento + interação de log + chunks ponderados no FAISS."""
    deps = _deps(context)
    project = get_bot_project(context)
    user = get_bot_user(context)

    doc_class: DocClass | None = await deps.sqlite.doc_classes.get(payload["slug"])
    assert doc_class is not None  # validado no cmd_doc
    conteudo: str = payload["conteudo"]
    titulo: str = payload["titulo"]

    # Interação de log — é o registro que o RAG devolve quando um chunk bate.
    interaction_id = await deps.sqlite.insert_interaction(
        user_id=user.telegram_id,
        chat_id=msg.chat_id if msg is not None else None,
        user_message=f"[Documento: {titulo}] ({doc_class.label})",
        bot_response=conteudo[:_DOC_LOG_MAX_CHARS],
        tags=["doc", doc_class.slug],
        intent="doc_upload",
        model_used=None, temperature=None,
        prompt_tokens=None, response_tokens=None, total_duration_ms=None,
        prompt_used=None,
        positive_ids=[], negative_ids=[],
        retrieved_count=None,
        embedding_model=deps.settings.ollama_embedding_model,
        embedding_dim=deps.settings.embedding_dim,
        tool_calls=[],
        media_path=payload["arquivo_path"], media_type="document",
        error=None, run_id=None,
        project_id=project.id,
    )

    doc_uid = gen_uid()
    document_id = await deps.sqlite.documents.insert(
        uid=doc_uid,
        project_id=project.id,
        doc_class=doc_class.slug,
        titulo=titulo,
        enviado_por=user.id,
        arquivo_path=payload["arquivo_path"],
        arquivo_hash=payload["arquivo_hash"],
        mime=payload["mime"],
        interaction_id=interaction_id,
    )

    raw_chunks = _chunk_text(
        conteudo, deps.settings.chunk_size, deps.settings.chunk_overlap
    )
    chunk_inserts: list[ChunkInsert] = []
    vectors: list = []
    for idx, chunk in enumerate(raw_chunks):
        vec = await deps.ollama.embed(chunk[:_EMBED_INPUT_MAX_CHARS])
        vectors.append(vec)
        chunk_inserts.append(
            ChunkInsert(
                chunk_idx=idx,
                content=chunk[:500],
                doc_class=doc_class.slug,
                weight=doc_class.peso,
                document_id=document_id,
            )
        )
    chunk_ids = await deps.sqlite.chunks.insert_many(interaction_id, chunk_inserts)
    await deps.faiss.add_many(chunk_ids, vectors)

    log.info(
        "/doc indexado: #%s '%s' classe=%s chunks=%d peso=%g obra=%s",
        doc_uid, titulo, doc_class.slug, len(chunk_ids), doc_class.peso, project.uid,
    )
    if msg is not None:
        await msg.reply_text(
            f"✅ Documento <code>#{escape(doc_uid)}</code> "
            f"<b>{escape(titulo)}</b> indexado como "
            f"<b>{escape(doc_class.label)}</b> "
            f"<i>({len(chunk_ids)} chunk(s), peso {doc_class.peso:g})</i>.\n"
            f"Disponível no RAG de <b>{escape(project.name)}</b> pra todos "
            f"os membros.",
            parse_mode=ParseMode.HTML,
        )
