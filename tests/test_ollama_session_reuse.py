"""Garante que OllamaClient sobrevive a troca de event loop.

Reproduz o bug do bootstrap: a session é criada num loop A (asyncio.run),
o loop é fechado, e depois um loop B tenta usar o mesmo cliente.
"""
from __future__ import annotations

import asyncio

from llm.ollama_client import OllamaClient


def _make_client() -> OllamaClient:
    return OllamaClient(
        host="http://127.0.0.1:1",  # nada precisa estar respondendo
        default_model="x",
        embedding_model="y",
        request_timeout_s=1,
    )


def test_session_recreated_when_loop_changes() -> None:
    client = _make_client()

    async def open_session_in_loop_a() -> int:
        session = await client._get_session()
        return id(session)

    async def open_session_in_loop_b() -> int:
        session = await client._get_session()
        return id(session)

    sid_a = asyncio.run(open_session_in_loop_a())  # cria + fecha loop A
    sid_b = asyncio.run(open_session_in_loop_b())  # loop B precisa nova session

    assert sid_a != sid_b, "session deveria ter sido recriada no novo loop"
