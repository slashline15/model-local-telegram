from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from database.faiss_mgr import FaissManager


pytestmark = pytest.mark.asyncio


async def test_add_and_search(faiss_mgr: FaissManager, rng: np.random.Generator) -> None:
    vectors = {sid: rng.standard_normal(8).astype(np.float32) for sid in (101, 202, 303)}
    for sid, v in vectors.items():
        await faiss_mgr.add(sid, v)

    assert faiss_mgr.ntotal == 3

    target_id = 202
    hits = await faiss_mgr.search(vectors[target_id], top_k=3)
    assert hits, "deveria retornar hits"
    assert hits[0][0] == target_id, "o vetor mais similar é ele mesmo"


async def test_search_empty_returns_nothing(faiss_mgr: FaissManager) -> None:
    out = await faiss_mgr.search(np.zeros(8, dtype=np.float32), top_k=5)
    assert out == []


async def test_dim_mismatch_raises(faiss_mgr: FaissManager) -> None:
    with pytest.raises(Exception):
        await faiss_mgr.add(1, np.zeros(7, dtype=np.float32))


async def test_persistence_across_instances(tmp_path: Path, rng: np.random.Generator) -> None:
    idx_path = tmp_path / "f.index"
    map_path = tmp_path / "f.json"
    a = FaissManager(dim=8, index_path=idx_path, id_map_path=map_path)
    await a.init()
    await a.add(7, rng.standard_normal(8).astype(np.float32))
    assert idx_path.exists()

    b = FaissManager(dim=8, index_path=idx_path, id_map_path=map_path)
    await b.init()
    assert b.ntotal == 1
