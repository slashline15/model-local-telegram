# tg/handlers_obra.py

"""Comandos do diário de obra: clima, efetivo, atividades, anotações, RDO.

MVP da Fase 4 (refundação 2026-05) — passo 4 do plano. Cada comando grava
direto na sua tabela operacional; `interaction_id` fica NULL aqui (vinda
por comando, não por classify_intent). Data assumida = hoje, salvo se
o usuário passar `--data YYYY-MM-DD` no final.
"""

from __future__ import annotations

import re
from datetime import datetime
from html import escape
from typing import TYPE_CHECKING

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core.exceptions import StorageError
from core.logger import get_logger
from database.repos.atividades import normalizar_estado
from database.repos.clima import validar_condicao
from tg.middleware import (
    get_bot_project,
    get_bot_user,
    require_active_project,
)

if TYPE_CHECKING:
    from tg.bot import BotDependencies

log = get_logger(__name__)


def _deps(context: ContextTypes.DEFAULT_TYPE) -> "BotDependencies":
    return context.application.bot_data["deps"]  # type: ignore[no-any-return]


def _today_local() -> str:
    """Data local YYYY-MM-DD (espelha _now_local_iso de handlers.py)."""
    return datetime.now().astimezone().strftime("%Y-%m-%d")


_DATA_FLAG_RE = re.compile(r"--data\s+(\d{4}-\d{2}-\d{2})", re.IGNORECASE)


def _extract_data_override(args_raw: str) -> tuple[str, str]:
    """Remove `--data YYYY-MM-DD` do texto e devolve (texto_limpo, dia).

    Sem flag → usa hoje.
    """
    m = _DATA_FLAG_RE.search(args_raw)
    if not m:
        return args_raw.strip(), _today_local()
    cleaned = (args_raw[: m.start()] + args_raw[m.end():]).strip()
    return cleaned, m.group(1)


_HORA_RANGE_RE = re.compile(r"^(\d{1,2}:\d{2})(?:\s*-\s*(\d{1,2}:\d{2}))?$")


def _parse_hora_range(text: str) -> tuple[str | None, str | None]:
    """Aceita 'HH:MM' ou 'HH:MM-HH:MM'. Vazio = (None, None)."""
    s = text.strip()
    if not s:
        return None, None
    m = _HORA_RANGE_RE.match(s)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def _parse_efetivo_args(args_raw: str) -> tuple[str, str, str | None] | None:
    """Aceita `Função; qtd[; Empresa]` ou `Função qtd` (último token numérico).

    Retorna (funcao_nome, qtd_raw, empresa_ref) ou None se inválido.
    """
    parts = [p.strip() for p in args_raw.split(";") if p.strip()]
    if len(parts) >= 2:
        funcao = parts[0]
        qtd = parts[1]
        empresa = parts[2] if len(parts) >= 3 else None
        return funcao, qtd, empresa
    tokens = args_raw.split()
    if len(tokens) >= 2 and tokens[-1].lstrip("-").isdigit():
        return " ".join(tokens[:-1]), tokens[-1], None
    return None


def _parse_atividade_args(args_raw: str) -> tuple[str, str] | None:
    """Aceita `Descrição; estado` ou `Descrição <estado>` (último token).

    Retorna (descricao, estado_raw) ou None se inválido.
    """
    parts = [p.strip() for p in args_raw.split(";") if p.strip()]
    if len(parts) >= 2:
        return parts[0], parts[1]
    tokens = args_raw.rsplit(maxsplit=1)
    if len(tokens) == 2 and tokens[0].strip() and tokens[1].strip():
        return tokens[0].strip(), tokens[1].strip()
    return None


# ────────────────── /clima ──────────────────

@require_active_project
async def cmd_clima(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/clima sol|nublado|chuva [HH:MM-HH:MM] [--data YYYY-MM-DD]`"""
    msg = update.effective_message
    assert msg is not None
    project = get_bot_project(context)
    user = get_bot_user(context)
    deps = _deps(context)

    args_raw = " ".join(context.args or []).strip()
    if not args_raw:
        await msg.reply_text(
            "Uso: <code>/clima sol|nublado|chuva [HH:MM-HH:MM]</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    args_raw, dia = _extract_data_override(args_raw)
    parts = args_raw.split(maxsplit=1)
    condicao_raw = parts[0]
    hora_raw = parts[1] if len(parts) > 1 else ""

    try:
        condicao = validar_condicao(condicao_raw)
    except StorageError as exc:
        await msg.reply_text(str(exc))
        return

    hora_ini, hora_fim = _parse_hora_range(hora_raw)
    if hora_raw and hora_ini is None:
        await msg.reply_text(
            "Hora inválida. Use formato <code>HH:MM</code> ou "
            "<code>HH:MM-HH:MM</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        await deps.sqlite.clima.insert(
            project_id=project.id, dia=dia, condicao=condicao,
            hora_inicio=hora_ini, hora_fim=hora_fim, criado_por=user.id,
        )
    except StorageError as exc:
        await msg.reply_text(f"Erro: {exc}")
        return

    janela = ""
    if hora_ini and hora_fim:
        janela = f" das {hora_ini} às {hora_fim}"
    elif hora_ini:
        janela = f" a partir das {hora_ini}"

    await msg.reply_text(
        f"☁️ Clima registrado em <b>{escape(dia)}</b>: "
        f"<b>{escape(condicao)}</b>{escape(janela)}.",
        parse_mode=ParseMode.HTML,
    )


@require_active_project
async def cmd_climas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/climas` — últimos registros climáticos da obra."""
    msg = update.effective_message
    assert msg is not None
    project = get_bot_project(context)
    deps = _deps(context)

    rows = await deps.sqlite.clima.list_recent(project.id, limit=10)
    if not rows:
        await msg.reply_text(
            f"Sem registros de clima em <b>{escape(project.name)}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = [f"<b>☁️ Clima · {escape(project.name)}</b>\n"]
    for c in rows:
        janela = ""
        if c.hora_inicio and c.hora_fim:
            janela = f" ({c.hora_inicio}–{c.hora_fim})"
        elif c.hora_inicio:
            janela = f" (a partir de {c.hora_inicio})"
        lines.append(f"• {escape(c.dia)} — <b>{escape(c.condicao)}</b>{janela}")
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ────────────────── /efetivo ──────────────────

@require_active_project
async def cmd_efetivo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/efetivo Função; qtd[; Empresa]` — Empresa por nome ou #UID."""
    msg = update.effective_message
    assert msg is not None
    project = get_bot_project(context)
    user = get_bot_user(context)
    deps = _deps(context)

    args_raw = " ".join(context.args or []).strip()
    if not args_raw:
        await msg.reply_text(
            "Uso: <code>/efetivo Função; qtd[; Empresa]</code>\n"
            "Exemplo: <code>/efetivo Pedreiro 5</code> ou "
            "<code>/efetivo Pedreiro; 5; Construtora X</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    args_raw, dia = _extract_data_override(args_raw)
    parsed = _parse_efetivo_args(args_raw)
    if parsed is None:
        await msg.reply_text(
            "Não entendi. Use <code>/efetivo Pedreiro 5</code> ou "
            "<code>/efetivo Pedreiro; 5; Empresa</code>.",
            parse_mode=ParseMode.HTML,
        )
        return
    funcao_nome, qtd_raw, empresa_ref = parsed

    try:
        qtd = int(qtd_raw)
    except ValueError:
        await msg.reply_text(f"Qtd inválida: <code>{escape(qtd_raw)}</code>.",
                             parse_mode=ParseMode.HTML)
        return

    funcao = await deps.sqlite.funcoes.get_by_nome(funcao_nome)
    if funcao is None:
        await msg.reply_text(
            f"Função <b>{escape(funcao_nome)}</b> não existe. Veja /funcoes.",
            parse_mode=ParseMode.HTML,
        )
        return

    empresa_id: int | None = None
    if empresa_ref:
        empresa = None
        if empresa_ref.startswith("#"):
            empresa = await deps.sqlite.empresas.get_by_uid(empresa_ref[1:])
        if empresa is None:
            empresa = await deps.sqlite.empresas.find_by_nome(project.id, empresa_ref)
        if empresa is None or empresa.project_id != project.id:
            await msg.reply_text(
                f"Empresa <b>{escape(empresa_ref)}</b> não existe nessa obra. "
                f"Veja /empresas.",
                parse_mode=ParseMode.HTML,
            )
            return
        empresa_id = empresa.id

    try:
        await deps.sqlite.efetivo.insert(
            project_id=project.id, dia=dia, funcao_id=funcao.id,
            empresa_id=empresa_id, qtd=qtd, criado_por=user.id,
        )
    except StorageError as exc:
        await msg.reply_text(f"Erro: {exc}")
        return

    empresa_str = f" @ {escape(empresa_ref)}" if empresa_ref else ""
    await msg.reply_text(
        f"👷 Efetivo registrado em <b>{escape(dia)}</b>: "
        f"<b>{qtd}</b> {escape(funcao.nome)}{empresa_str}.",
        parse_mode=ParseMode.HTML,
    )


@require_active_project
async def cmd_efetivos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/efetivos` — efetivo do dia atual (ou data passada via --data)."""
    msg = update.effective_message
    assert msg is not None
    project = get_bot_project(context)
    deps = _deps(context)

    args_raw = " ".join(context.args or []).strip()
    _, dia = _extract_data_override(args_raw)

    rows = await deps.sqlite.efetivo.list_for_dia(project.id, dia)
    if not rows:
        await msg.reply_text(
            f"Sem efetivo registrado em <b>{escape(project.name)}</b> "
            f"no dia <b>{escape(dia)}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    f_cache: dict[int, str] = {}
    e_cache: dict[int, str] = {}
    total = 0
    lines = [f"<b>👷 Efetivo · {escape(project.name)} · {escape(dia)}</b>\n"]
    for r in rows:
        if r.funcao_id not in f_cache:
            f = await deps.sqlite.funcoes.get_by_id(r.funcao_id)
            f_cache[r.funcao_id] = f.nome if f else "?"
        empresa_str = ""
        if r.empresa_id is not None:
            if r.empresa_id not in e_cache:
                e = await deps.sqlite.empresas.get_by_id(r.empresa_id)
                e_cache[r.empresa_id] = e.nome if e else "?"
            empresa_str = f" @ {escape(e_cache[r.empresa_id])}"
        lines.append(
            f"• <b>{r.qtd}</b> {escape(f_cache[r.funcao_id])}{empresa_str}"
        )
        total += r.qtd
    lines.append(f"\n<i>Total: {total} pessoa(s)</i>")
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ────────────────── /atividade ──────────────────

@require_active_project
async def cmd_atividade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/atividade Descrição; estado` — estado: concluida|em_andamento|atrasada|impedida"""
    msg = update.effective_message
    assert msg is not None
    project = get_bot_project(context)
    user = get_bot_user(context)
    deps = _deps(context)

    args_raw = " ".join(context.args or []).strip()
    if not args_raw:
        await msg.reply_text(
            "Uso: <code>/atividade Descrição; estado</code>\n"
            "Estados: <code>concluida | em_andamento | atrasada | impedida</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    args_raw, dia = _extract_data_override(args_raw)
    parsed = _parse_atividade_args(args_raw)
    if parsed is None:
        await msg.reply_text(
            "Não entendi. Use <code>/atividade Concretagem laje; em_andamento</code> "
            "ou <code>/atividade Pintura sala 3 concluida</code>.",
            parse_mode=ParseMode.HTML,
        )
        return
    descricao, estado_raw = parsed
    try:
        estado = normalizar_estado(estado_raw)
    except StorageError as exc:
        await msg.reply_text(str(exc))
        return

    try:
        aid = await deps.sqlite.atividades.insert(
            project_id=project.id, dia=dia, estado=estado,
            descricao=descricao, criado_por=user.id,
        )
    except StorageError as exc:
        await msg.reply_text(f"Erro: {exc}")
        return

    icon = {
        "concluida": "✅", "em_andamento": "🔄",
        "atrasada": "⏰", "impedida": "🚫",
    }.get(estado, "•")
    await msg.reply_text(
        f"{icon} Atividade <code>#{aid}</code> registrada em "
        f"<b>{escape(dia)}</b> — <i>{escape(estado)}</i>.\n"
        f"{escape(descricao)}",
        parse_mode=ParseMode.HTML,
    )


@require_active_project
async def cmd_atividades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/atividades` — atividades do dia (default hoje)."""
    msg = update.effective_message
    assert msg is not None
    project = get_bot_project(context)
    deps = _deps(context)

    args_raw = " ".join(context.args or []).strip()
    _, dia = _extract_data_override(args_raw)

    rows = await deps.sqlite.atividades.list_for_dia(project.id, dia)
    if not rows:
        await msg.reply_text(
            f"Sem atividades em <b>{escape(project.name)}</b> "
            f"no dia <b>{escape(dia)}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    icons = {
        "concluida": "✅", "em_andamento": "🔄",
        "atrasada": "⏰", "impedida": "🚫",
    }
    lines = [f"<b>📋 Atividades · {escape(project.name)} · {escape(dia)}</b>\n"]
    for a in rows:
        icon = icons.get(a.estado, "•")
        lines.append(
            f"{icon} <code>#{a.id}</code> {escape(a.descricao)} "
            f"<i>({escape(a.estado)})</i>"
        )
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ────────────────── /anotacao ──────────────────

@require_active_project
async def cmd_anotacao(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/anotacao <texto>` — anotação livre do diário."""
    msg = update.effective_message
    assert msg is not None
    project = get_bot_project(context)
    user = get_bot_user(context)
    deps = _deps(context)

    args_raw = " ".join(context.args or []).strip()
    if not args_raw:
        await msg.reply_text(
            "Uso: <code>/anotacao &lt;texto livre&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    args_raw, dia = _extract_data_override(args_raw)
    if not args_raw:
        await msg.reply_text("Texto da anotação não pode ser vazio.")
        return

    try:
        nid = await deps.sqlite.anotacoes.insert(
            project_id=project.id, dia=dia, texto=args_raw,
            criado_por=user.id,
        )
    except StorageError as exc:
        await msg.reply_text(f"Erro: {exc}")
        return

    await msg.reply_text(
        f"📝 Anotação <code>#{nid}</code> registrada em "
        f"<b>{escape(dia)}</b>.",
        parse_mode=ParseMode.HTML,
    )


@require_active_project
async def cmd_anotacoes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/anotacoes` — últimas anotações visíveis pro user."""
    msg = update.effective_message
    assert msg is not None
    project = get_bot_project(context)
    user = get_bot_user(context)
    deps = _deps(context)

    rows = await deps.sqlite.anotacoes.list_recent(
        project.id, limit=10, requester_user_id=user.telegram_id,
    )
    if not rows:
        await msg.reply_text(
            f"Sem anotações em <b>{escape(project.name)}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = [f"<b>📝 Anotações · {escape(project.name)}</b>\n"]
    for a in rows:
        flag = "🔒 " if a.visibilidade == "privada" else ""
        snippet = a.texto if len(a.texto) <= 200 else a.texto[:200] + "…"
        lines.append(
            f"{flag}<code>#{a.id}</code> <i>{escape(a.dia)}</i> "
            f"— {escape(snippet)}"
        )
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ────────────────── /rdo ──────────────────

@require_active_project
async def cmd_rdo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/rdo [YYYY-MM-DD]` — consolidação do dia (default hoje)."""
    msg = update.effective_message
    assert msg is not None
    project = get_bot_project(context)
    user = get_bot_user(context)
    deps = _deps(context)

    args_raw = " ".join(context.args or []).strip()
    _, dia = _extract_data_override(args_raw)
    # Aceita também `/rdo 2026-05-12` direto, sem `--data`.
    if not args_raw.startswith("--") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", args_raw):
        dia = args_raw

    climas = await deps.sqlite.clima.list_for_dia(project.id, dia)
    efetivos = await deps.sqlite.efetivo.list_for_dia(project.id, dia)
    atividades = await deps.sqlite.atividades.list_for_dia(project.id, dia)
    anotacoes = await deps.sqlite.anotacoes.list_for_dia(
        project.id, dia, requester_user_id=user.telegram_id,
    )

    lines: list[str] = [
        f"<b>📒 RDO · {escape(project.name)}</b>",
        f"<i>Dia: {escape(dia)}</i>",
        "",
    ]

    # Clima
    if climas:
        lines.append("<b>☁️ Clima</b>")
        for c in climas:
            janela = ""
            if c.hora_inicio and c.hora_fim:
                janela = f" ({c.hora_inicio}–{c.hora_fim})"
            elif c.hora_inicio:
                janela = f" (a partir de {c.hora_inicio})"
            lines.append(f"• {escape(c.condicao)}{janela}")
        lines.append("")

    # Efetivo
    if efetivos:
        lines.append("<b>👷 Efetivo</b>")
        f_cache: dict[int, str] = {}
        total = 0
        for r in efetivos:
            if r.funcao_id not in f_cache:
                f = await deps.sqlite.funcoes.get_by_id(r.funcao_id)
                f_cache[r.funcao_id] = f.nome if f else "?"
            lines.append(f"• {r.qtd}× {escape(f_cache[r.funcao_id])}")
            total += r.qtd
        lines.append(f"<i>Total: {total}</i>")
        lines.append("")

    # Atividades
    if atividades:
        lines.append("<b>📋 Atividades</b>")
        icons = {
            "concluida": "✅", "em_andamento": "🔄",
            "atrasada": "⏰", "impedida": "🚫",
        }
        for a in atividades:
            icon = icons.get(a.estado, "•")
            lines.append(
                f"{icon} {escape(a.descricao)} <i>({escape(a.estado)})</i>"
            )
        lines.append("")

    # Anotações
    if anotacoes:
        lines.append("<b>📝 Anotações</b>")
        for a in anotacoes:
            flag = "🔒 " if a.visibilidade == "privada" else ""
            snippet = a.texto if len(a.texto) <= 300 else a.texto[:300] + "…"
            lines.append(f"{flag}• {escape(snippet)}")
        lines.append("")

    if len(lines) <= 3:
        lines.append("<i>Sem registros nesse dia.</i>")

    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
