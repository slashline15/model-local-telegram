# tg/handlers_rdo.py

"""Comandos de cadastro: funções (catálogo), empresas, colaboradores."""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from core.exceptions import StorageError
from core.logger import get_logger
from core.uid import gen_uid
from database.repos.empresas import EMPRESA_TIPO_OWN, EMPRESA_TIPO_THIRD_PARTY
from tg.middleware import (
    get_bot_project,
    get_bot_user,
    require_active_project,
    require_active_user,
    require_project_admin,
)

if TYPE_CHECKING:
    from tg.bot import BotDependencies

log = get_logger(__name__)


def _deps(context: ContextTypes.DEFAULT_TYPE) -> "BotDependencies":
    return context.application.bot_data["deps"]  # type: ignore[no-any-return]


# ────────────────── /funcoes ──────────────────

@require_active_user
async def cmd_funcoes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    assert msg is not None
    deps = _deps(context)
    rows = await deps.sqlite.funcoes.list_active()
    if not rows:
        await msg.reply_text("Catálogo de funções vazio (?).")
        return
    lines = ["<b>📋 Funções cadastradas</b>\n"]
    for f in rows:
        lines.append(f"• {escape(f.nome)}")
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ────────────────── /empresas e /empresa add ──────────────────

_TIPO_ALIASES: dict[str, str] = {
    "own":         EMPRESA_TIPO_OWN,
    "propria":     EMPRESA_TIPO_OWN,
    "própria":     EMPRESA_TIPO_OWN,
    "proprio":     EMPRESA_TIPO_OWN,
    "próprio":     EMPRESA_TIPO_OWN,
    "third":       EMPRESA_TIPO_THIRD_PARTY,
    "third_party": EMPRESA_TIPO_THIRD_PARTY,
    "terceiro":    EMPRESA_TIPO_THIRD_PARTY,
    "terceira":    EMPRESA_TIPO_THIRD_PARTY,
}


def _normalize_tipo(arg: str) -> str | None:
    return _TIPO_ALIASES.get(arg.lower().strip())


@require_active_project
async def cmd_empresas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    assert msg is not None
    project = get_bot_project(context)
    deps = _deps(context)

    rows = await deps.sqlite.empresas.list_for_project(project.id)
    if not rows:
        await msg.reply_text(
            f"Sem empresas cadastradas em <b>{escape(project.name)}</b>.\n"
            "Use <code>/empresa add Nome; CNPJ; own|third</code> "
            "(CNPJ e tipo opcionais).",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = [f"<b>🏢 Empresas · {escape(project.name)}</b>\n"]
    for e in rows:
        marker = "🏠 própria" if e.tipo == EMPRESA_TIPO_OWN else "🤝 terceira"
        cnpj = f" · {escape(e.cnpj)}" if e.cnpj else ""
        lines.append(
            f"• <code>#{escape(e.uid)}</code> {escape(e.nome)} "
            f"<i>({marker}{cnpj})</i>"
        )
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@require_project_admin
async def cmd_empresa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/empresa add Nome; CNPJ; own|third` — só admin da obra."""
    msg = update.effective_message
    assert msg is not None
    project = get_bot_project(context)
    user = get_bot_user(context)
    deps = _deps(context)

    args_raw = " ".join(context.args or []).strip()
    if not args_raw or not args_raw.lower().startswith("add"):
        await msg.reply_text(
            "Uso: <code>/empresa add Nome[; CNPJ[; own|third]]</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    payload = args_raw[3:].strip()  # remove 'add'
    parts = [p.strip() for p in payload.split(";")]
    if not parts or not parts[0]:
        await msg.reply_text("Nome da empresa é obrigatório.")
        return

    nome = parts[0]
    cnpj = parts[1] if len(parts) > 1 and parts[1] else None
    tipo_raw = parts[2] if len(parts) > 2 and parts[2] else None
    tipo = _normalize_tipo(tipo_raw) if tipo_raw else EMPRESA_TIPO_THIRD_PARTY
    if tipo is None:
        await msg.reply_text(
            f"Tipo inválido: `{tipo_raw}`. Use `own` ou `third`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Evita duplicata óbvia.
    if await deps.sqlite.empresas.find_by_nome(project.id, nome):
        await msg.reply_text(f"Já existe empresa com nome <b>{escape(nome)}</b> nessa obra.",
                             parse_mode=ParseMode.HTML)
        return

    try:
        emp = await deps.sqlite.empresas.create(
            uid=gen_uid(), project_id=project.id,
            nome=nome, cnpj=cnpj, tipo=tipo, created_by=user.id,
        )
    except StorageError as exc:
        await msg.reply_text(f"Erro: {exc}")
        return

    label = "🏠 própria" if emp.tipo == EMPRESA_TIPO_OWN else "🤝 terceira"
    await msg.reply_text(
        f"✅ Empresa criada <code>#{escape(emp.uid)}</code> "
        f"<b>{escape(emp.nome)}</b> <i>({label})</i>",
        parse_mode=ParseMode.HTML,
    )


# ────────────────── /colabs e /colab add ──────────────────

@require_active_project
async def cmd_colabs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/colabs [funcao]` — opcionalmente filtra por nome de função."""
    msg = update.effective_message
    assert msg is not None
    project = get_bot_project(context)
    deps = _deps(context)

    funcao_filter = " ".join(context.args).strip() if context.args else ""
    funcao_id: int | None = None
    if funcao_filter:
        f = await deps.sqlite.funcoes.get_by_nome(funcao_filter)
        if f is None:
            await msg.reply_text(f"Função `{funcao_filter}` não encontrada. Veja /funcoes.",
                                 parse_mode=ParseMode.MARKDOWN)
            return
        funcao_id = f.id

    rows = await deps.sqlite.colaboradores.list_for_project(
        project.id, funcao_id=funcao_id,
    )
    if not rows:
        msg_text = (
            f"Sem colaboradores em <b>{escape(project.name)}</b>"
            + (f" com função <i>{escape(funcao_filter)}</i>" if funcao_filter else "")
            + ".\n\nUse <code>/colab add Nome; Função; Empresa[; Apelido]</code>."
        )
        await msg.reply_text(msg_text, parse_mode=ParseMode.HTML)
        return

    # Resolve funcoes/empresas em lote (evita N+1 sério).
    f_cache: dict[int, str] = {}
    e_cache: dict[int, str] = {}
    lines = [f"<b>👷 Colaboradores · {escape(project.name)}</b>"]
    if funcao_filter:
        lines[0] += f" <i>({escape(funcao_filter)})</i>"
    lines.append("")

    for c in rows:
        if c.funcao_id is not None and c.funcao_id not in f_cache:
            f = await deps.sqlite.funcoes.get_by_id(c.funcao_id)
            f_cache[c.funcao_id] = f.nome if f else "?"
        if c.empresa_id not in e_cache:
            e = await deps.sqlite.empresas.get_by_id(c.empresa_id)
            e_cache[c.empresa_id] = e.nome if e else "?"

        funcao_name = f_cache.get(c.funcao_id, "—") if c.funcao_id else "—"
        apelido = f' "{escape(c.apelido)}"' if c.apelido else ""
        lines.append(
            f"• <code>#{escape(c.uid)}</code> {escape(c.nome)}{apelido} "
            f"— <i>{escape(funcao_name)}</i> @ {escape(e_cache[c.empresa_id])}"
        )
    await msg.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@require_project_admin
async def cmd_colab(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/colab add Nome; Função; Empresa[; Apelido]` — só admin da obra."""
    msg = update.effective_message
    assert msg is not None
    project = get_bot_project(context)
    user = get_bot_user(context)
    deps = _deps(context)

    args_raw = " ".join(context.args or []).strip()
    if not args_raw or not args_raw.lower().startswith("add"):
        await msg.reply_text(
            "Uso: <code>/colab add Nome; Função; Empresa[; Apelido]</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    payload = args_raw[3:].strip()
    parts = [p.strip() for p in payload.split(";")]
    if len(parts) < 3 or not all(parts[:3]):
        await msg.reply_text("Faltou campo. `Nome; Função; Empresa` são obrigatórios.",
                             parse_mode=ParseMode.MARKDOWN)
        return

    nome = parts[0]
    funcao_nome = parts[1]
    empresa_ref = parts[2]
    apelido = parts[3] if len(parts) > 3 and parts[3] else None

    funcao = await deps.sqlite.funcoes.get_by_nome(funcao_nome)
    if funcao is None:
        await msg.reply_text(
            f"Função <b>{escape(funcao_nome)}</b> não existe no catálogo. "
            f"Veja /funcoes.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Aceita #UID ou nome exato pra empresa.
    empresa = None
    if empresa_ref.startswith("#"):
        empresa = await deps.sqlite.empresas.get_by_uid(empresa_ref[1:])
    if empresa is None:
        empresa = await deps.sqlite.empresas.find_by_nome(project.id, empresa_ref)
    if empresa is None or empresa.project_id != project.id:
        await msg.reply_text(
            f"Empresa <b>{escape(empresa_ref)}</b> não existe nessa obra. "
            f"Veja /empresas ou cadastre com /empresa add.",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        c = await deps.sqlite.colaboradores.create(
            uid=gen_uid(), project_id=project.id, empresa_id=empresa.id,
            funcao_id=funcao.id, nome=nome, apelido=apelido,
            created_by=user.id,
        )
    except StorageError as exc:
        await msg.reply_text(f"Erro: {exc}")
        return

    apelido_str = f' "{escape(c.apelido)}"' if c.apelido else ""
    await msg.reply_text(
        f"✅ Colaborador <code>#{escape(c.uid)}</code> "
        f"<b>{escape(c.nome)}</b>{apelido_str} "
        f"— <i>{escape(funcao.nome)}</i> @ {escape(empresa.nome)}",
        parse_mode=ParseMode.HTML,
    )
