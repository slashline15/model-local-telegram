from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import pytest_asyncio

from database.faiss_mgr import FaissManager
from database.repos.chunks import ChunkInsert
from database.sqlite_mgr import SQLiteManager
from llm.contrastive_rag import ContrastiveRAG

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def global_faiss(tmp_path: Path) -> FaissManager:
    mgr = FaissManager(
        dim=8,
        index_path=tmp_path / "faiss_global.index",
        id_map_path=tmp_path / "faiss_global_ids.json",
    )
    await mgr.init()
    return mgr


def _fake_vec(content: str) -> np.ndarray:
    """Mesmo embedding determinístico do FakeOllama — texto igual ⇒ sim=1.0."""
    rng = np.random.default_rng(seed=abs(hash(content)) % (2**32))
    v = rng.standard_normal(8).astype(np.float32)
    return v / float(np.linalg.norm(v))


async def _seed_global(
    sqlite: SQLiteManager,
    gfaiss: FaissManager,
    *,
    conteudo: str,
    titulo: str = "Norma X",
    weight: float = 1.0,
) -> int:
    cid = await sqlite.global_chunks.insert(
        source="norma_abnt", doc_class="norma",
        titulo=titulo, conteudo=conteudo, weight=weight,
    )
    await gfaiss.add(cid, _fake_vec(conteudo))
    return cid


async def _seed_local(
    sqlite: SQLiteManager, faiss: FaissManager, *, content: str, score: int | None
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
    chunk_ids = await sqlite.chunks.insert_many(
        iid, [ChunkInsert(chunk_idx=0, content=content[:500], doc_class="note", weight=1.0)]
    )
    await faiss.add(chunk_ids[0], _fake_vec(content))
    return iid


def _rag(
    sqlite: SQLiteManager, faiss: FaissManager, gfaiss: FaissManager | None
) -> ContrastiveRAG:
    from tests.conftest import FakeOllama

    return ContrastiveRAG(
        ollama=FakeOllama(),  # type: ignore[arg-type]
        sqlite=sqlite, faiss=faiss,
        chunks=sqlite.chunks,
        top_k=10,
        global_faiss=gfaiss,
        global_chunks=sqlite.global_chunks if gfaiss is not None else None,
        max_global=3,
    )


async def test_global_refs_injected_when_local_empty(
    sqlite_mgr: SQLiteManager, faiss_mgr: FaissManager, global_faiss: FaissManager
) -> None:
    query = "norma de concreto armado"
    cid = await _seed_global(sqlite_mgr, global_faiss, conteudo=query)

    bundle = await _rag(sqlite_mgr, faiss_mgr, global_faiss).build(query)
    assert [g.id for g in bundle.global_refs] == [cid]
    assert "[Referências técnicas" in bundle.user_prompt
    assert "Norma X" in bundle.user_prompt


async def test_global_weight_zero_disables_global(
    sqlite_mgr: SQLiteManager, faiss_mgr: FaissManager, global_faiss: FaissManager
) -> None:
    query = "norma de impermeabilização"
    await _seed_global(sqlite_mgr, global_faiss, conteudo=query)

    bundle = await _rag(sqlite_mgr, faiss_mgr, global_faiss).build(
        query, global_weight=0.0
    )
    assert bundle.global_refs == []
    assert "[Referências técnicas" not in bundle.user_prompt


async def test_low_global_weight_cuts_weak_refs(
    sqlite_mgr: SQLiteManager, faiss_mgr: FaissManager, global_faiss: FaissManager
) -> None:
    # sim máxima = 1.0 → score = 1.0 * 1.0 * 0.1 = 0.1 < corte (0.25).
    query = "alvenaria estrutural"
    await _seed_global(sqlite_mgr, global_faiss, conteudo=query)

    bundle = await _rag(sqlite_mgr, faiss_mgr, global_faiss).build(
        query, global_weight=0.1
    )
    assert bundle.global_refs == []


async def test_inactive_global_chunk_excluded(
    sqlite_mgr: SQLiteManager, faiss_mgr: FaissManager, global_faiss: FaissManager
) -> None:
    query = "ensaio de compressão"
    cid = await _seed_global(sqlite_mgr, global_faiss, conteudo=query)
    await sqlite_mgr.global_chunks.set_ativo(cid, False)

    bundle = await _rag(sqlite_mgr, faiss_mgr, global_faiss).build(query)
    assert bundle.global_refs == []


async def test_dual_merge_keeps_contrastive_and_adds_global(
    sqlite_mgr: SQLiteManager, faiss_mgr: FaissManager, global_faiss: FaissManager
) -> None:
    query = "cronograma da fundação"
    pos_id = await _seed_local(sqlite_mgr, faiss_mgr, content=query, score=5)
    gid = await _seed_global(sqlite_mgr, global_faiss, conteudo=query)

    bundle = await _rag(sqlite_mgr, faiss_mgr, global_faiss).build(query)
    assert pos_id in bundle.positive_ids
    assert [g.id for g in bundle.global_refs] == [gid]
    assert "[O QUE FAZER" in bundle.user_prompt
    assert "[Referências técnicas" in bundle.user_prompt


async def test_without_global_index_behaves_like_before(
    sqlite_mgr: SQLiteManager, faiss_mgr: FaissManager
) -> None:
    query = "pergunta qualquer"
    await _seed_local(sqlite_mgr, faiss_mgr, content=query, score=5)

    bundle = await _rag(sqlite_mgr, faiss_mgr, None).build(query)
    assert bundle.global_refs == []
    assert "[Referências técnicas" not in bundle.user_prompt
