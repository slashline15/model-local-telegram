from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from tg.kb import main_reply_keyboard

from core.logger import get_logger
from core.permissions import (
    GLOBAL_ROLE_SUPERADMIN,
    GLOBAL_ROLE_MEMBER,
    default_member_permissions,
    project_role_implies_global_role,
)
from tg.middleware import require_active_user

if TYPE_CHECKING:
    from tg.bot import BotDependencies

log = get_logger(__name__)


def _deps(context: ContextTypes.DEFAULT_TYPE) -> "BotDependencies":
    return context.application.bot_data["deps"]  # type: ignore[no-any-return]


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
                "Use /criar_obra pra começar.\n\n"
                "O botão <b>📋 Menu</b> abre o RDO do dia.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_reply_keyboard(),
            )
            return
        await msg.reply_text(
            "👋 Você ainda não está cadastrado. Peça um convite ao admin "
            "da obra e abra o link recebido."
        )
        return

    await msg.reply_text(_WELCOME_TEXT, reply_markup=main_reply_keyboard())


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
        f"O botão <b>📋 Menu</b> abre o RDO do dia.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_reply_keyboard(),
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
