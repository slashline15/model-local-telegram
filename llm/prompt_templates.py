from __future__ import annotations

from dataclasses import dataclass


_SYSTEM_PROMPT_BASE: str = (
    "Você é um assistente útil, direto e em Português do Brasil. "
    "Use os exemplos positivos como guia de estilo e qualidade. "
    "Use os exemplos negativos como sinal explícito do que evitar. "
    "Se não houver exemplos, responda normalmente. "
    "Se não souber, admita; nunca invente fatos. "
    "O bloco [Histórico recente] contém turnos REAIS desta conversa — use-o "
    "para manter contexto, lembrar nomes, fatos e correções anteriores.\n\n"
    "FORMATAÇÃO (a saída é renderizada no Telegram, parse Markdown legacy):\n"
    "- Use **negrito** com asteriscos duplos. Use _itálico_ com underscore. "
    "Use `código` com crase. Listas com '- ' ou '1. '.\n"
    "- NUNCA use LaTeX (`$...$`, `\\rightarrow`, `\\times`, `$$...$$`, etc.). "
    "Se precisar de seta, escreva diretamente: → ⇒ ←. Se precisar de "
    "multiplicação, use × ou *. Se precisar de fração, escreva 'a/b'.\n"
    "- Não use cabeçalhos `#` (Telegram não renderiza). Para títulos use **Negrito**.\n\n"
    "FERRAMENTAS:\n"
    "- Quando usar resultados de tools (ex.: web_search), cite as fontes ao "
    "final no formato:\nFontes:\n[1] título — url\n[2] título — url\n\n"
    "MEMÓRIAS:\n"
    "- Os blocos de exemplos e histórico podem trazer um identificador entre "
    "colchetes, como [memória #i42] ou [#i42]. Se a sua resposta se basear "
    "em uma dessas memórias para lembrar um fato, preferência ou correção, "
    "cite-a no texto entre parênteses como (#i42). Não invente códigos — só "
    "cite os que aparecem no contexto.\n\n"
    "AGENDAMENTO PROATIVO:\n"
    "- Quando o usuário expressar dependência de tempo (ex.: 'tomara que X "
    "entregue até hoje à tarde', 'amanhã preciso falar com Y', 'se até "
    "sexta não chegar...'), use a tool `schedule_reminder` para criar um "
    "lembrete adequado, sem pedir permissão. Calcule `when_iso` a partir "
    "da 'Data/hora atual' acima. Bom senso: 'depois do almoço' ≈ 14h00 "
    "local; 'fim do dia' ≈ 18h00; 'amanhã cedo' ≈ 09h00.\n"
    "- Em seguida, na sua resposta, mencione discretamente o agendamento "
    "(ex.: '_te lembro disso depois do almoço._'). Não agende coisas "
    "óbvias, repetidas ou que o usuário já indicou que não quer."
)

TAG_GENERATOR_SYSTEM: str = (
    "Você classifica a intenção da mensagem do usuário em 1 a 3 tags curtas, "
    "minúsculas, sem espaços (use snake_case), em português. "
    "Exemplos: codigo, duvida, chat, pedido_resumo, traducao, brainstorm, "
    "imagem, audio, erro_tecnico. "
    "Responda EXCLUSIVAMENTE em JSON: {\"tags\": [\"tag1\", \"tag2\"]} — sem texto extra."
)


@dataclass(slots=True, frozen=True)
class FewShotExample:
    user_message: str
    bot_response: str
    code: str | None = None  # ex.: "i42" — para citação rastreável no prompt


def build_system_prompt(
    now_iso: str | None = None,
    style_directive: str = "",
    obra_context: str | None = None,
) -> str:
    parts: list[str] = [_SYSTEM_PROMPT_BASE]
    if now_iso:
        parts.append(f"Data/hora atual: {now_iso}")
    if obra_context and obra_context.strip():
        parts.append(
            "CONTEXTO DE OBRA (operacional):\n" + obra_context.strip()
        )
    if style_directive.strip():
        parts.append(
            "Preferências do usuário (siga à risca):\n" + style_directive.strip()
        )
    return "\n\n".join(parts)


SYSTEM_PROMPT_DEFAULT: str = build_system_prompt()


def _truncate(text: str, max_chars: int = 600) -> str:
    text = text.strip()
    return text if len(text) <= max_chars else text[: max_chars] + "…"


def _examples_block(items: list[FewShotExample]) -> str:
    if not items:
        return "(nenhum exemplo disponível)"
    chunks: list[str] = []
    for i, ex in enumerate(items, start=1):
        header = f"Exemplo {i}"
        if ex.code:
            header += f" [memória #{ex.code}]"
        chunks.append(
            f"{header}:\n"
            f"  Usuário: {_truncate(ex.user_message)}\n"
            f"  Resposta: {_truncate(ex.bot_response)}"
        )
    return "\n\n".join(chunks)


def _history_block(items: list[FewShotExample]) -> str:
    chunks: list[str] = []
    for i, ex in enumerate(items, start=1):
        header = f"Turno {i}"
        if ex.code:
            header += f" [#{ex.code}]"
        chunks.append(
            f"{header}:\n"
            f"  Você: {_truncate(ex.user_message, 500)}\n"
            f"  Bot:  {_truncate(ex.bot_response, 500)}"
        )
    return "\n\n".join(chunks)


def render_contrastive_prompt(
    user_message: str,
    positives: list[FewShotExample],
    negatives: list[FewShotExample],
    history: list[FewShotExample] | None = None,
) -> str:
    """Prompt contrastivo com âncoras [O QUE FAZER] / [O QUE NÃO FAZER]."""
    parts: list[str] = []
    if history:
        parts.append(
            "[Histórico recente — turnos anteriores desta conversa]\n"
            + _history_block(history)
        )
    parts.append("[O QUE FAZER - Bons exemplos]\n" + _examples_block(positives))
    parts.append(
        "[O QUE NÃO FAZER - Evite estas abordagens]\n" + _examples_block(negatives)
    )
    parts.append("[Pergunta Atual]\n" + user_message.strip())
    return "\n\n".join(parts)


def render_neutral_context(
    user_message: str,
    examples: list[FewShotExample],
    history: list[FewShotExample] | None = None,
) -> str:
    """Prompt usado quando ainda não há rating: traz contexto recente sem polaridade."""
    parts: list[str] = []
    if history:
        parts.append(
            "[Histórico recente — turnos anteriores desta conversa]\n"
            + _history_block(history)
        )
    parts.append(
        "[Contexto recente — interações similares ainda não avaliadas]\n"
        + _examples_block(examples)
    )
    parts.append("[Pergunta Atual]\n" + user_message.strip())
    return "\n\n".join(parts)


def render_qa_prompt(
    user_message: str,
    history: list[FewShotExample] | None = None,
) -> str:
    """Prompt simples para perguntas/resumos: histórico + pergunta, sem contrastivo.

    Para `summarize` (e outros casos sem polaridade útil) o template contrastivo
    confunde o modelo — ele tende a sumarizar as próprias âncoras
    `[O QUE FAZER]/[O QUE NÃO FAZER]` em vez do conteúdo.
    """
    parts: list[str] = []
    if history:
        parts.append(
            "[Histórico recente — turnos anteriores desta conversa]\n"
            + _history_block(history)
        )
    parts.append("[Pergunta Atual]\n" + user_message.strip())
    return "\n\n".join(parts)
