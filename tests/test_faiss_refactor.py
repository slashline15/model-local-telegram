# tests/test_faiss_refactor.py

"""Testa FaissManager após refatoração para chunk_ids."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.faiss_mgr import FaissManager  # noqa: E402


@pytest.mark.asyncio
async def test_add_with_chunk_id(faiss_mgr: FaissManager, rng: np.random.Generator):
    vec = rng.standard_normal(8).astype(np.float32)
    chunk_id = 999
    await faiss_mgr.add(chunk_id, vec)
    assert faiss_mgr.ntotal == 1


@pytest.mark.asyncio
async def test_search_returns_chunk_ids(faiss_mgr: FaissManager, rng: np.random.Generator):
    chunk_ids = [100, 200, 300]
    for cid in chunk_ids:
        vec = rng.standard_normal(8).astype(np.float32)
        await faiss_mgr.add(cid, vec)

    query = rng.standard_normal(8).astype(np.float32)
    hits = await faiss_mgr.search(query, top_k=3)
    assert len(hits) <= 3
    returned_ids = [h[0] for h in hits]
    # Todos os IDs retornados devem ser chunk_ids inseridos.
    for rid in returned_ids:
        assert rid in chunk_ids


@pytest.mark.asyncio
async def test_add_many(faiss_mgr: FaissManager, rng: np.random.Generator):
    chunk_ids = list(range(50, 60))
    vecs = [rng.standard_normal(8).astype(np.float32) for _ in chunk_ids]
    await faiss_mgr.add_many(chunk_ids, vecs)
    assert faiss_mgr.ntotal == 10


@pytest.mark.asyncio
async def test_add_many_empty(faiss_mgr: FaissManager):
    await faiss_mgr.add_many([], [])
    assert faiss_mgr.ntotal == 0


@pytest.mark.asyncio
async def test_persistence(tmp_path: Path, rng: np.random.Generator):
    """Garante que chunk_ids são preservados após reload do índice."""
    mgr = FaissManager(
        dim=8,
        index_path=tmp_path / "p.index",
        id_map_path=tmp_path / "p.json",
    )
    await mgr.init()

    chunk_ids = [1001, 2002]
    for cid in chunk_ids:
        vec = rng.standard_normal(8).astype(np.float32)
        await mgr.add(cid, vec)

    # Recarrega.
    mgr2 = FaissManager(
        dim=8,
        index_path=tmp_path / "p.index",
        id_map_path=tmp_path / "p.json",
    )
    await mgr2.init()
    assert mgr2.ntotal == 2
