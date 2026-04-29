from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from core.exceptions import OllamaError, OllamaTimeoutError
from core.logger import get_logger
from llm.ollama_client import ChatMessage, ChatResult, OllamaClient
from llm.openai_chat_client import OpenAIChatClient

log = get_logger(__name__)

# Erros que disparam fallback. Mantemos a lista explícita pra não engolir bugs.
_RETRIABLE = (OllamaError, OllamaTimeoutError)

ToolDispatcher = Callable[[str, dict[str, Any]], Awaitable[Any]]
FailureCallback = Callable[[Exception, str], Awaitable[None]]


@dataclass(slots=True)
class ChatRunResult:
    chat_result: ChatResult
    model_used: str
    backend: str  # "ollama" | "openai"
    fell_back: bool
    tool_invocations: list[dict[str, Any]] = field(default_factory=list)
    tool_iterations: int = 0
    primary_error: str | None = None


async def _run_ollama_with_tools(
    *,
    ollama: OllamaClient,
    base_messages: list[ChatMessage],
    model: str,
    temperature: float,
    tools: list[dict[str, Any]] | None,
    tool_dispatcher: ToolDispatcher,
    max_tool_iter: int,
) -> tuple[ChatResult, list[dict[str, Any]], int]:
    """Roda chat + tool loop completo no Ollama. Pode levantar OllamaError."""
    messages = list(base_messages)
    chat_result = await ollama.chat(
        messages=messages, model=model, temperature=temperature, tools=tools,
    )

    tool_invocations: list[dict[str, Any]] = []
    tool_iter = 0

    while chat_result.tool_calls and tool_iter < max_tool_iter:
        tool_iter += 1
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
                result = await tool_dispatcher(tname, args)
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
            messages.append(ChatMessage(role="tool", content=result_str, name=tname))

        chat_result = await ollama.chat(
            messages=messages, model=model, temperature=temperature, tools=tools,
        )

    return chat_result, tool_invocations, tool_iter


async def run_chat_with_fallback(
    *,
    ollama: OllamaClient,
    openai: OpenAIChatClient | None,
    base_messages: list[ChatMessage],
    primary_model: str,
    temperature: float,
    tools: list[dict[str, Any]] | None,
    tool_dispatcher: ToolDispatcher,
    fallback_models: list[str],
    openai_fallback_model: str | None,
    max_tool_iter: int = 3,
    on_first_failure: FailureCallback | None = None,
) -> ChatRunResult:
    """Tenta primary com tools; se falhar, percorre fallbacks (sem tools).

    Estratégia:
    1. Primary (Ollama, com tools+loop)
    2. Cada `fallback_models` (Ollama, sem tools, base_messages limpas)
    3. `openai_fallback_model` (OpenAI, sem tools, se cliente disponível)

    Se algum estágio passar, retorna. Se tudo falhar, re-raise a última exceção.
    """
    last_exc: Exception | None = None

    # 1) Primary com tools.
    try:
        chat_result, invocations, iters = await _run_ollama_with_tools(
            ollama=ollama,
            base_messages=base_messages,
            model=primary_model,
            temperature=temperature,
            tools=tools,
            tool_dispatcher=tool_dispatcher,
            max_tool_iter=max_tool_iter,
        )
        return ChatRunResult(
            chat_result=chat_result,
            model_used=primary_model,
            backend="ollama",
            fell_back=False,
            tool_invocations=invocations,
            tool_iterations=iters,
        )
    except _RETRIABLE as exc:
        last_exc = exc
        log.warning("Primary chat falhou em %s: %s — tentando fallbacks.", primary_model, exc)
        if on_first_failure is not None:
            try:
                await on_first_failure(exc, primary_model)
            except Exception as cb_exc:  # noqa: BLE001
                log.warning("on_first_failure callback falhou: %s", cb_exc)

    primary_err = f"{type(last_exc).__name__}: {last_exc}" if last_exc else None

    # 2) Fallbacks Ollama (sem tools). Reusa só system+user originais.
    for fb in fallback_models:
        if not fb or fb == primary_model:
            continue
        try:
            log.info("Tentando fallback Ollama: %s", fb)
            result = await ollama.chat(
                messages=base_messages,
                model=fb,
                temperature=temperature,
                tools=None,
            )
            return ChatRunResult(
                chat_result=result,
                model_used=fb,
                backend="ollama",
                fell_back=True,
                tool_invocations=[],
                tool_iterations=0,
                primary_error=primary_err,
            )
        except _RETRIABLE as exc:
            last_exc = exc
            log.warning("Fallback %s falhou: %s", fb, exc)

    # 3) Fallback OpenAI (sem tools).
    if openai is not None and openai_fallback_model:
        try:
            log.info("Tentando fallback OpenAI: %s", openai_fallback_model)
            result = await openai.chat(
                messages=base_messages,
                model=openai_fallback_model,
                temperature=temperature,
            )
            return ChatRunResult(
                chat_result=result,
                model_used=openai_fallback_model,
                backend="openai",
                fell_back=True,
                tool_invocations=[],
                tool_iterations=0,
                primary_error=primary_err,
            )
        except _RETRIABLE as exc:
            last_exc = exc
            log.warning("Fallback OpenAI falhou: %s", exc)

    assert last_exc is not None
    raise last_exc
