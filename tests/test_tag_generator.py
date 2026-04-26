from __future__ import annotations

import pytest

from llm.tag_generator import TagGenerator


pytestmark = pytest.mark.asyncio


async def test_returns_clean_tags() -> None:
    from tests.conftest import FakeOllama

    fake = FakeOllama(chat_responses=['{"tags": ["codigo", "duvida"]}'])
    gen = TagGenerator(ollama=fake)  # type: ignore[arg-type]
    tags = await gen.generate("como uso o asyncio?")
    assert tags == ["codigo", "duvida"]


async def test_handles_invalid_json() -> None:
    from tests.conftest import FakeOllama

    fake = FakeOllama(chat_responses=["isto não é json"])
    gen = TagGenerator(ollama=fake)  # type: ignore[arg-type]
    tags = await gen.generate("qualquer")
    assert tags == ["chat"]


async def test_filters_invalid_entries() -> None:
    from tests.conftest import FakeOllama

    fake = FakeOllama(
        chat_responses=['{"tags": ["OK_tag", "com espaço", 42, "tag-com-hifen"]}']
    )
    gen = TagGenerator(ollama=fake)  # type: ignore[arg-type]
    tags = await gen.generate("x")
    assert "ok_tag" in tags
    assert "com_espaço" not in tags  # acento bloqueia
    assert "tag_com_hifen" in tags
    assert all(isinstance(t, str) for t in tags)


async def test_empty_input_short_circuits() -> None:
    from tests.conftest import FakeOllama

    fake = FakeOllama()
    gen = TagGenerator(ollama=fake)  # type: ignore[arg-type]
    assert await gen.generate("   ") == ["chat"]
    assert fake.chat_calls == [], "não deveria chamar Ollama"
