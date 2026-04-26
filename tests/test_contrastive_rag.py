from __future__ import annotations

import numpy as np
import pytest

from database.faiss_mgr import FaissManager
from database.sqlite_mgr import SQLiteManager
from llm.contrastive_rag import ContrastiveRAG


pytestmark = pytest.mark.asyncio


async def _seed(
    sqlite: SQLiteManager,
    faiss: FaissManager,
    *,
    content: str,
    score: int | None,
) -> int:
    iid = await sqlite.insert_interaction(
        user_id=1, chat_id=1, user_message=f"q: {content}", bot_response=f"r: {content}",
        tags=[], intent=None, model_used=None, temperature=None,
        prompt_tokens=None, response_tokens=None, total_duration_ms=None,
        prompt_used=None, positive_ids=[], negative_ids=[],
        retrieved_count=None, embedding_model="fake-embed", embedding_dim=8,
        tool_calls=[], media_path=None, media_type="text",
        error=None, run_id=None,
    )
    if score is not None:
        await sqlite.update_score(iid, score)

    seed = abs(hash(content)) % (2**32)
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(8).astype(np.float32)
    v = v / float(np.linalg.norm(v))
    await faiss.add(iid, v)
    return iid


async def test_empty_index_returns_empty_bundle(
    sqlite_mgr: SQLiteManager, faiss_mgr: FaissManager
) -> None:
    from tests.conftest import FakeOllama

    rag = ContrastiveRAG(
        ollama=FakeOllama(),  # type: ignore[arg-type]
        sqlite=sqlite_mgr, faiss=faiss_mgr,
    )
    bundle = await rag.build("alguma pergunta")
    assert bundle.positives == [] and bundle.negatives == [] and bundle.hits == []
    assert "[Pergunta Atual]" in bundle.user_prompt


async def test_separates_positive_and_negative(
    sqlite_mgr: SQLiteManager, faiss_mgr: FaissManager
) -> None:
    from tests.conftest import FakeOllama

    pos_id = await _seed(sqlite_mgr, faiss_mgr, content="alvo", score=5)
    neg_id = await _seed(sqlite_mgr, faiss_mgr, content="alvo_ruim", score=1)
    await _seed(sqlite_mgr, faiss_mgr, content="distractor1", score=None)
    await _seed(sqlite_mgr, faiss_mgr, content="distractor2", score=3)

    rag = ContrastiveRAG(
        ollama=FakeOllama(),  # type: ignore[arg-type]
        sqlite=sqlite_mgr, faiss=faiss_mgr,
        top_k=10, max_positive=3, max_negative=2,
    )
    bundle = await rag.build("qualquer texto")
    assert pos_id in bundle.positive_ids
    assert neg_id in bundle.negative_ids
    assert not bundle.fallback_used


async def test_fallback_when_no_scored_interactions(
    sqlite_mgr: SQLiteManager, faiss_mgr: FaissManager
) -> None:
    from tests.conftest import FakeOllama

    for c in ("a", "b", "c"):
        await _seed(sqlite_mgr, faiss_mgr, content=c, score=None)

    rag = ContrastiveRAG(
        ollama=FakeOllama(),  # type: ignore[arg-type]
        sqlite=sqlite_mgr, faiss=faiss_mgr,
        top_k=10, max_neutral=2,
    )
    bundle = await rag.build("qualquer")
    assert bundle.fallback_used
    assert len(bundle.neutral) == 2
    assert "[Contexto recente" in bundle.user_prompt
