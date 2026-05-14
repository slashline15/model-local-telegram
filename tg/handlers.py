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


def _chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Divide text em chunks sobrepostos. Retorna ao menos [text] se text for curto."""
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start += chunk_size - overlap
    return chunks


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


# ─────────────────────────── COMANDOS ───────────────────────────

_WELCOME_TEXT: str = (
    "Olá! Sou seu copiloto de obra.\n\n"
    "Comandos principais:\n"
    "/obras – suas obras\n"
    "/obra #UID – escolher obra ativa\n"
    "/criar_obra <nome> – criar obra (admins)\n"
    "/invite <role> – gerar convite\n"
    "/membros – membros da obra ativa\n\n"
    "/help – lista completa"
)


def _telegram_display_name(update: Update) -> str:
    u = update.effective_user
    if u is None:
        return "Usuário"
    parts = [p for p in (u.first_name, u.last_name) if p]
    return " ".join(parts) or (u.username or f"tg:{u.id}")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Saudação inicial OU consumo de deep-link de convite (`/start <token>`)."""
    msg = update.effective_message
    tg_user = update.effective_user
    if msg is None or tg_user is None:
        return

    deps = _deps(context)
    token = (context.args or [None])[0]

    # ─── Caminho A: deep link com token de convite ───
    if token:
        await _consume_invite(update, context, token=token)
        return

    # ─── Caminho B: /start sem token ───
    existing = await deps.sqlite.users.get_by_telegram_id(tg_user.id)

    if existing is None:
        bootstrap_id = deps.settings.bootstrap_superadmin_telegram_id
        if bootstrap_id and bootstrap_id == tg_user.id:
            await deps.sqlite.users.register(
                telegram_id=tg_user.id,
                name=_telegram_display_name(update),
                role=GLOBAL_ROLE_SUPERADMIN,
            )
            log.warning("Bootstrap: %s promovido a superadmin.", tg_user.id)
            await msg.reply_text(
                "🎖 Você foi registrado como <b>superadmin</b>. "
                "Use /criar_obra pra começar.",
                parse_mode=ParseMode.HTML,
            )
            return
        await msg.reply_text(
            "👋 Você ainda não está cadastrado. Peça um convite ao admin "
            "da obra e abra o link recebido."
        )
        return

    await msg.reply_text(_WELCOME_TEXT)


async def _consume_invite(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, token: str,
) -> None:
    msg = update.effective_message
    tg_user = update.effective_user
    assert msg is not None and tg_user is not None
    deps = _deps(context)

    invite = await deps.sqlite.invites.get_by_token(token)
    if invite is None:
        await msg.reply_text("Convite inválido. Peça outro ao admin.")
        return
    if invite.used_at is not None:
        await msg.reply_text("Esse convite já foi usado.")
        return
    if invite.project_id is None:
        # Convite de plataforma (sem obra) — ainda não suportado nesta versão.
        await msg.reply_text("Convite mal-formado: sem obra associada.")
        return

    # Garante user cadastrado (idempotente via ON CONFLICT).
    user = await deps.sqlite.users.register(
        telegram_id=tg_user.id,
        name=_telegram_display_name(update),
        invited_by=invite.created_by,
    )

    # Consumo atômico: se outro user já marcou, retorna False.
    consumed = await deps.sqlite.invites.mark_used(invite.id, used_by=user.id)
    if not consumed:
        await msg.reply_text("Esse convite acabou de ser usado por outra pessoa.")
        return

    # Permissões padrão pelo role do convite.
    perms = default_member_permissions(invite.role)
    await deps.sqlite.members.add(
        project_id=invite.project_id,
        user_id=user.id,
        role=invite.role,
        invite_id=invite.id,
        **perms,
    )

    # Eleva role global se o convite for de admin de obra.
    new_global = project_role_implies_global_role(invite.role)
    if new_global and user.role == GLOBAL_ROLE_MEMBER:
        await deps.sqlite.users.update_role(user.id, new_global)

    # Convite com role 'admin' transfere o admin_id da obra.
    if invite.role == "admin":
        await deps.sqlite.projects.set_admin(invite.project_id, user.id)

    # Define a obra como ativa do recém-chegado.
    await deps.sqlite.settings.set_current_project(tg_user.id, invite.project_id)

    project = await deps.sqlite.projects.get_by_id(invite.project_id)
    proj_name = project.name if project else "?"
    proj_uid = project.uid if project else "?"

    await msg.reply_text(
        f"✅ Bem-vindo! Você entrou em <b>{escape(proj_name)}</b> "
        f"<code>#{escape(proj_uid)}</code> como <i>{escape(invite.role)}</i>.\n\n"
        f"{_WELCOME_TEXT}",
        parse_mode=ParseMode.HTML,
    )


@require_active_user
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    # Texto puro — sem parse_mode pra evitar BadRequest silencioso por
    # caractere especial. Telegram renderiza mesmo assim.
    texto = (
        "📒 Bot RDO — comandos disponíveis\n"
        "\n"
        "── Obras & convites ──\n"
        "/obras                   suas obras (clique pra ativar)\n"
        "/obra #UID               ativar uma obra\n"
        "/criar_obra <nome>       criar obra (admin global)\n"
        "/invite <role>           convidar pra obra ativa\n"
        "/membros                 membros da obra ativa\n"
        "\n"
        "── Cadastro estrutural (admin da obra) ──\n"
        "/funcoes                 catálogo de funções\n"
        "/empresas                empresas da obra\n"
        "/empresa add Nome[; CNPJ[; own|third]]\n"
        "/colabs [função]         colaboradores\n"
        "/colab add Nome; Função; Empresa[; Apelido]\n"
        "\n"
        "── Diário da obra (qualquer membro) ──\n"
        "/clima sol|nublado|chuva [HH:MM-HH:MM]\n"
        "/climas                  últimos registros climáticos\n"
        "/efetivo Função qtd [Empresa]   (também aceita ; entre campos)\n"
        "/efetivos [--data 2026-05-12]\n"
        "/atividade Descrição; estado    estado: concluida|em_andamento|atrasada|impedida\n"
        "/atividades\n"
        "/anotacao <texto livre>\n"
        "/anotacoes\n"
        "/rdo [YYYY-MM-DD]        consolidação do dia\n"
        "\n"
        "── Conversa & IA ──\n"
        "/config                  modelo + temperatura\n"
        "/stats                   estatísticas globais\n"
        "/recall <texto>          debug do RAG\n"
        "/recall #i<id>           abre uma interação\n"
        "/history [n]             suas n últimas mensagens\n"
        "/reminders               seus lembretes\n"
        "/ping                    health Ollama\n"
        "/whoami                  seu cadastro\n"
        "/reset                   config ao padrão\n"
        "\n"
        "── Debug (superadmin) ──\n"
        "/consumo /consumo_usuario /consumo_obra /consumo_modelo /status\n"
        "\n"
        "Dicas:\n"
        "• Mande texto / foto / documento / áudio livremente.\n"
        "• Comandos de cadastro usam a obra ativa (veja /obras).\n"
        "• Use --data YYYY-MM-DD no final pra registrar em dia passado."
    )
    await update.effective_message.reply_text(texto)


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


@require_active_user
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    deps = _deps(context)
    snap = await deps.sqlite.stats(faiss_indexed=deps.faiss.ntotal)
    avg = f"{snap.avg_latency_ms:.0f}ms" if snap.avg_latency_ms is not None else "—"
    text = (
        "<b>📊 Estatísticas</b>\n\n"
        f"• Interações:        {snap.total_interactions}\n"
        f"• Avaliadas:         {snap.rated} "
        f"(🟢 {snap.positives} / ⭕ {snap.negatives})\n"
        f"• Usuários únicos:   {snap.distinct_users}\n"
        f"• Intents distintas: {snap.distinct_intents}\n"
        f"• Latência média:    {avg}\n"
        f"• FAISS indexado:    {snap.faiss_indexed} vetores\n"
        f"• Último run_id:     <code>{snap.last_run_id or '—'}</code>"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


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


@require_active_user
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

@require_active_user
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
                    name, args, ctx={"user_id": user_id, "chat_id": chat_id},
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
            await _safe_reply(
                msg, tagged, reply_markup=_rating_keyboard(interaction_id)
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
