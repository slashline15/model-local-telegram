from __future__ import annotations

import pytest

from llm.intent_classifier import ALLOWED_INTENTS, IntentClassifier


pytestmark = pytest.mark.asyncio


async def test_valid_intent_passthrough() -> None:
    from tests.conftest import FakeOllama

    fake = FakeOllama(
        chat_responses=['{"intent": "code_help", "confidence": 0.92, "reason": "menciona python"}']
    )
    clf = IntentClassifier(ollama=fake)  # type: ignore[arg-type]
    res = await clf.classify("como faço uma list comprehension em python?")
    assert res.intent == "code_help"
    assert res.confidence == pytest.approx(0.92)
    assert "python" in res.reason


async def test_unknown_intent_falls_to_other() -> None:
    from tests.conftest import FakeOllama

    fake = FakeOllama(chat_responses=['{"intent": "DELETE_DB", "confidence": 0.5}'])
    clf = IntentClassifier(ollama=fake)  # type: ignore[arg-type]
    res = await clf.classify("hack me")
    assert res.intent == "other"


async def test_confidence_clamped() -> None:
    from tests.conftest import FakeOllama

    fake = FakeOllama(chat_responses=['{"intent": "question", "confidence": 5.0}'])
    clf = IntentClassifier(ollama=fake)  # type: ignore[arg-type]
    res = await clf.classify("a")
    assert 0.0 <= res.confidence <= 1.0


async def test_allowed_intents_are_unique() -> None:
    assert len(set(ALLOWED_INTENTS)) == len(ALLOWED_INTENTS)
