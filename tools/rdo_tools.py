# tools/rdo_tools.py
# Tools de RDO chamáveis pelo modelo para registrar dados a partir de texto/foto.
# O modelo chama essas tools ao identificar dados estruturáveis na conversa.
# IMPORTANTE: sempre pede confirmação ao usuário antes de executar (via `pending_rdo`).

from __future__ import annotations

from database.repos.atividades import normalizar_estado
from database.repos.clima import validar_condicao
from tools.registry import ToolRegistry, ToolSpec


async def _registrar_atividade(
    descricao: str,
    estado: str = "em_andamento",
    dia: str | None = None,
    _ctx: dict | None = None,
) -> dict:
    """Extrai dados de atividade para confirmação pelo usuário."""
    _ctx = _ctx or {}
    try:
        estado_norm = normalizar_estado(estado)
    except Exception:
        estado_norm = "em_andamento"
    return {
        "action": "pending_rdo",
        "type": "atividade",
        "descricao": descricao.strip(),
        "estado": estado_norm,
        "dia": dia,
        "project_id": _ctx.get("project_id"),
        "user_id": _ctx.get("user_id"),
    }


async def _registrar_clima(
    condicao: str,
    hora_inicio: str | None = None,
    hora_fim: str | None = None,
    dia: str | None = None,
    _ctx: dict | None = None,
) -> dict:
    """Extrai dados de clima para confirmação pelo usuário."""
    _ctx = _ctx or {}
    try:
        condicao_norm = validar_condicao(condicao)
    except Exception:
        condicao_norm = condicao.lower().strip()
    return {
        "action": "pending_rdo",
        "type": "clima",
        "condicao": condicao_norm,
        "hora_inicio": hora_inicio,
        "hora_fim": hora_fim,
        "dia": dia,
        "project_id": _ctx.get("project_id"),
        "user_id": _ctx.get("user_id"),
    }


async def _registrar_efetivo(
    funcao: str,
    qtd: int,
    empresa: str | None = None,
    dia: str | None = None,
    _ctx: dict | None = None,
) -> dict:
    """Extrai dados de efetivo para confirmação pelo usuário."""
    _ctx = _ctx or {}
    return {
        "action": "pending_rdo",
        "type": "efetivo",
        "funcao": funcao.strip(),
        "qtd": int(qtd),
        "empresa": empresa,
        "dia": dia,
        "project_id": _ctx.get("project_id"),
        "user_id": _ctx.get("user_id"),
    }


async def _registrar_anotacao(
    texto: str,
    natureza: str = "registro",
    dia: str | None = None,
    _ctx: dict | None = None,
) -> dict:
    """Extrai dados de anotação para confirmação pelo usuário."""
    _ctx = _ctx or {}
    return {
        "action": "pending_rdo",
        "type": "anotacao",
        "texto": texto.strip(),
        "natureza": natureza,
        "dia": dia,
        "project_id": _ctx.get("project_id"),
        "user_id": _ctx.get("user_id"),
    }


def register(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(
        name="registrar_atividade",
        description=(
            "Registra uma atividade no RDO da obra ativa. Use quando o usuário descrever "
            "o que foi feito na obra (ex: 'concretamos os pilares', 'instalamos a cobertura'). "
            "O sistema pedirá confirmação antes de salvar."
        ),
        parameters={
            "type": "object",
            "properties": {
                "descricao": {"type": "string", "description": "Descrição da atividade"},
                "estado": {
                    "type": "string",
                    "enum": ["concluida", "em_andamento", "atrasada", "impedida"],
                    "description": "Estado da atividade (padrão: em_andamento)",
                },
                "dia": {
                    "type": "string",
                    "description": "Data no formato YYYY-MM-DD (padrão: hoje)",
                },
            },
            "required": ["descricao"],
        },
        handler=_registrar_atividade,
    ))
    registry.register(ToolSpec(
        name="registrar_clima",
        description=(
            "Registra condição climática no RDO da obra ativa. Use quando o usuário mencionar "
            "tempo, chuva, sol, clima. O sistema pedirá confirmação antes de salvar."
        ),
        parameters={
            "type": "object",
            "properties": {
                "condicao": {
                    "type": "string",
                    "enum": ["sol", "nublado", "chuva", "nevoa"],
                    "description": "Condição do tempo",
                },
                "hora_inicio": {"type": "string", "description": "Horário de início HH:MM"},
                "hora_fim":    {"type": "string", "description": "Horário de fim HH:MM"},
                "dia":         {"type": "string", "description": "Data YYYY-MM-DD"},
            },
            "required": ["condicao"],
        },
        handler=_registrar_clima,
    ))
    registry.register(ToolSpec(
        name="registrar_efetivo",
        description=(
            "Registra efetivo (mão de obra) no RDO da obra ativa. Use quando o usuário informar "
            "quantas pessoas de uma função trabalharam. O sistema pedirá confirmação."
        ),
        parameters={
            "type": "object",
            "properties": {
                "funcao":  {"type": "string",  "description": "Nome da função (ex: Pedreiro)"},
                "qtd":     {"type": "integer", "description": "Quantidade de pessoas"},
                "empresa": {"type": "string",  "description": "Nome ou #UID da empresa (opcional)"},
                "dia":     {"type": "string",  "description": "Data YYYY-MM-DD"},
            },
            "required": ["funcao", "qtd"],
        },
        handler=_registrar_efetivo,
    ))
    registry.register(ToolSpec(
        name="registrar_anotacao",
        description=(
            "Registra uma anotação livre no diário de obra. Use para ocorrências, eventos ou "
            "informações que não se encaixam em atividade/efetivo/clima. Pedirá confirmação."
        ),
        parameters={
            "type": "object",
            "properties": {
                "texto":    {"type": "string", "description": "Texto da anotação"},
                "natureza": {
                    "type": "string",
                    "enum": ["registro", "ocorrencia", "evento"],
                    "description": "Tipo da anotação (padrão: registro)",
                },
                "dia": {"type": "string", "description": "Data YYYY-MM-DD"},
            },
            "required": ["texto"],
        },
        handler=_registrar_anotacao,
    ))
