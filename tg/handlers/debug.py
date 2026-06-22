# tg/handlers/debug.py

"""
Comandos de diagnóstico — acessíveis apenas a superadmin.

/consumo              — resumo geral de tokens
/consumo_usuario <id> — consumo por usuário
/consumo_obra <uid>   — consumo por obra
/consumo_modelo       — ranking de modelos com custo
/status               — health do sistema
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core.logger import get_logger
from database.models import DailyTokenRow, TokenUsageSummary
from tg.middleware import require_superadmin

log = get_logger(__name__)

_SPARKLINE_CHARS = "▁▂▃▄▅▆▇█"


def _sparkline(values: list[int]) -> str:
    if not values:
        return "—"
    max_val = max(values) or 1
    idx_max = len(_SPARKLINE_CHARS) - 1
    return "".join(
        _SPARKLINE_CHARS[round(v / max_val * idx_max)] for v in values
    )


def _fmt_k(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return str(n)


def _deps(context: ContextTypes.DEFAULT_TYPE):  # type: ignore[no-untyped-def]
    return context.application.bot_data["deps"]  # type: ignore[no-any-return]


def _iso_since(days: int) -> str:
    dt = datetime.now(tz=timezone.utc) - timedelta(days=days)
    return dt.isoformat(timespec="seconds")


@require_superadmin
async def cmd_consumo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    deps = _deps(context)

    # Períodos: hoje, ontem, semana, mês.
    now_utc = datetime.now(tz=timezone.utc)
    today = now_utc.date().isoformat()
    yesterday = (now_utc.date() - timedelta(days=1)).isoformat()
    since_week = _iso_since(7)
    since_month = _iso_since(30)

    tok_today,  cost_today  = await deps.sqlite.token_usage.total_for_period(today + "T00:00:00+00:00")
    tok_yest,   cost_yest   = await deps.sqlite.token_usage.total_for_period(
        yesterday + "T00:00:00+00:00",
        until=today + "T00:00:00+00:00",
    )
    tok_week,   cost_week   = await deps.sqlite.token_usage.total_for_period(since_week)
    tok_month,  cost_month  = await deps.sqlite.token_usage.total_for_period(since_month)

    daily: list[DailyTokenRow] = await deps.sqlite.token_usage.daily_breakdown(days=7)
    spark = _sparkline([d.total_tokens for d in daily])

    by_model: list[TokenUsageSummary] = await deps.sqlite.token_usage.sum_by_model(since=since_week)

    model_lines = "\n".join(
        f"  {escape(s.model):25s} {_fmt_k(s.total_tokens):>6}  ${s.cost_usd:.4f}"
        for s in by_model[:8]
    )

    text = (
        "<b>📊 Consumo de tokens</b>\n\n"
        f"Hoje:   {_fmt_k(tok_today):>8} tokens · ${cost_today:.4f}\n"
        f"Ontem:  {_fmt_k(tok_yest):>8} tokens · ${cost_yest:.4f}\n"
        f"Semana: {_fmt_k(tok_week):>8} tokens · ${cost_week:.4f}\n"
        f"Mês:    {_fmt_k(tok_month):>8} tokens · ${cost_month:.4f}\n\n"
        f"Últimos 7 dias:\n{spark}\n\n"
        f"Por modelo (semana):\n<code>{model_lines or '  —'}</code>"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


@require_superadmin
async def cmd_consumo_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    deps = _deps(context)
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Uso: /consumo_usuario <id ou nome>"
        )
        return

    # Tenta resolver pelo ID numérico ou pelo nome.
    identifier = " ".join(args).strip()
    user_id: int | None = None
    user_name = identifier
    if identifier.isdigit():
        user_id = int(identifier)
        user_obj = await deps.sqlite.users.get_by_id(user_id)
        user_name = user_obj.name if user_obj else identifier
    else:
        # Busca pelo nome (primeiro match, case-insensitive via Python).
        all_users = await deps.sqlite.users.list(status="active", limit=500)
        matched = [u for u in all_users if identifier.lower() in u.name.lower()]
        if not matched:
            await update.effective_message.reply_text(f"Usuário '{identifier}' não encontrado.")
            return
        user_id = matched[0].id
        user_name = matched[0].name

    since_week = _iso_since(7)
    since_month = _iso_since(30)
    summs_w = await deps.sqlite.token_usage.sum_by_user(user_id, since=since_week)
    tok_week = sum(s.total_tokens for s in summs_w)
    cost_week = sum(s.cost_usd for s in summs_w)
    summs_m = await deps.sqlite.token_usage.sum_by_user(user_id, since=since_month)
    tok_month = sum(s.total_tokens for s in summs_m)
    cost_month = sum(s.cost_usd for s in summs_m)

    daily = await deps.sqlite.token_usage.daily_breakdown(days=7, user_id=user_id)
    spark = _sparkline([d.total_tokens for d in daily])

    model_lines = "\n".join(
        f"  {escape(s.model):25s} {_fmt_k(s.total_tokens):>6}  ${s.cost_usd:.4f}"
        for s in summs_w[:6]
    )
    text = (
        f"<b>👤 {escape(user_name)} — consumo</b>\n\n"
        f"Semana: {_fmt_k(tok_week)} tokens · ${cost_week:.4f}\n"
        f"Mês:    {_fmt_k(tok_month)} tokens · ${cost_month:.4f}\n\n"
        f"Últimos 7 dias:\n{spark}\n\n"
        f"Por modelo (semana):\n<code>{model_lines or '  —'}</code>"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


@require_superadmin
async def cmd_consumo_obra(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    deps = _deps(context)
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Uso: /consumo_obra <uid>")
        return

    uid = args[0].lstrip("#")
    project = await deps.sqlite.projects.get_by_uid(uid)
    if project is None:
        await update.effective_message.reply_text(f"Obra #{uid} não encontrada.")
        return

    since_week = _iso_since(7)
    since_month = _iso_since(30)
    summs_w = await deps.sqlite.token_usage.sum_by_project(project.id, since=since_week)
    tok_week = sum(s.total_tokens for s in summs_w)
    cost_week = sum(s.cost_usd for s in summs_w)
    summs_m = await deps.sqlite.token_usage.sum_by_project(project.id, since=since_month)
    tok_month = sum(s.total_tokens for s in summs_m)
    cost_month = sum(s.cost_usd for s in summs_m)

    daily = await deps.sqlite.token_usage.daily_breakdown(days=7, project_id=project.id)
    spark = _sparkline([d.total_tokens for d in daily])

    text = (
        f"<b>🏗 {escape(project.name)} — consumo</b>\n\n"
        f"Semana: {_fmt_k(tok_week)} tokens · ${cost_week:.4f}\n"
        f"Mês:    {_fmt_k(tok_month)} tokens · ${cost_month:.4f}\n\n"
        f"Últimos 7 dias:\n{spark}"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


@require_superadmin
async def cmd_consumo_modelo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    deps = _deps(context)
    since_month = _iso_since(30)
    summs = await deps.sqlite.token_usage.sum_by_model(since=since_month)

    if not summs:
        await update.effective_message.reply_text("Sem dados de consumo no último mês.")
        return

    lines = ["<b>📊 Consumo por modelo (30 dias)</b>\n"]
    for s in summs[:15]:
        lines.append(
            f"• <code>{escape(s.model)}</code> ({s.backend})\n"
            f"  {_fmt_k(s.total_tokens)} tok  ${s.cost_usd:.4f}  ({s.count}× calls)"
        )
    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@require_superadmin
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    deps = _deps(context)

    # Health do Ollama.
    t0 = time.monotonic()
    try:
        report = await deps.ollama.health_check(expected_dim=deps.settings.embedding_dim)
        ollama_ms = int((time.monotonic() - t0) * 1000)
        ollama_icon = "🟢" if report.ollama_reachable else "🔴"
        ollama_line = (
            f"{ollama_icon} Ollama: {'online' if report.ollama_reachable else 'offline'} · "
            f"última inferência {ollama_ms}ms"
        )
        if report.error:
            ollama_line += f"\n   ⚠️ {escape(report.error[:80])}"
    except Exception as exc:  # noqa: BLE001
        ollama_line = f"🔴 Ollama: erro ao checar — {escape(str(exc)[:80])}"

    # FAISS stats.
    faiss_total = deps.faiss.ntotal
    faiss_line = f"📊 FAISS: {faiss_total:,} vetores"

    # DB size.
    db_path = deps.settings.sqlite_path
    db_size_mb = db_path.stat().st_size / (1024 * 1024) if db_path.exists() else 0
    db_line = f"💾 DB: {db_size_mb:.1f} MB"

    text = (
        "<b>🟢 Status do sistema</b>\n\n"
        f"{ollama_line}\n"
        f"{faiss_line}\n"
        f"{db_line}"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)
