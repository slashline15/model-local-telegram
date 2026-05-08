# tests/test_chunks_repo.py

"""Testa ChunksRepo: insert, insert_many, get_by_ids, get_interaction_ids_for_chunks."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.repos.chunks import ChunkInsert, ChunksRepo  # noqa: E402
from database.sqlite_mgr import SQLiteManager  # noqa: E402


@pytest.mark.asyncio
async def test_insert_single(sqlite_mgr: SQLiteManager, tmp_path: Path):
    # Precisa de um interaction_id válido — criar um via sqlite_mgr.
    iid = await sqlite_mgr.interactions.insert(
        user_id=1, chat_id=1,
        user_message="hello", bot_response="hi",
        tags=[], intent=None, model_used=None, temperature=None,
        prompt_tokens=None, response_tokens=None, total_duration_ms=None,
        prompt_used=None, positive_ids=[], negative_ids=[],
        retrieved_count=None, embedding_model=None, embedding_dim=None,
        tool_calls=[], media_path=None, media_type="text",
        error=None, run_id=None, project_id=None,
    )

    chunk_id = await sqlite_mgr.chunks.insert(
        interaction_id=iid,
        chunk_idx=0,
        content="chunk zero content",
        doc_class="note",
        weight=1.0,
    )
    assert isinstance(chunk_id, int) and chunk_id > 0

    chunks = await sqlite_mgr.chunks.get_by_interaction(iid)
    assert len(chunks) == 1
    assert chunks[0].id == chunk_id
    assert chunks[0].content == "chunk zero content"
    assert chunks[0].doc_class == "note"


@pytest.mark.asyncio
async def test_insert_many(sqlite_mgr: SQLiteManager):
    iid = await sqlite_mgr.interactions.insert(
        user_id=1, chat_id=1,
        user_message="doc", bot_response="ok",
        tags=[], intent=None, model_used=None, temperature=None,
        prompt_tokens=None, response_tokens=None, total_duration_ms=None,
        prompt_used=None, positive_ids=[], negative_ids=[],
        retrieved_count=None, embedding_model=None, embedding_dim=None,
        tool_calls=[], media_path=None, media_type="document",
        error=None, run_id=None, project_id=None,
    )

    inserts = [
        ChunkInsert(chunk_idx=0, content="chunk A", doc_class="contract", weight=1.5),
        ChunkInsert(chunk_idx=1, content="chunk B", doc_class="contract", weight=1.5),
        ChunkInsert(chunk_idx=2, content="chunk C", doc_class="contract", weight=1.5),
    ]
    chunk_ids = await sqlite_mgr.chunks.insert_many(iid, inserts)
    assert len(chunk_ids) == 3
    assert len(set(chunk_ids)) == 3  # IDs únicos


@pytest.mark.asyncio
async def test_get_by_ids(sqlite_mgr: SQLiteManager):
    iid = await sqlite_mgr.interactions.insert(
        user_id=2, chat_id=1,
        user_message="x", bot_response="y",
        tags=[], intent=None, model_used=None, temperature=None,
        prompt_tokens=None, response_tokens=None, total_duration_ms=None,
        prompt_used=None, positive_ids=[], negative_ids=[],
        retrieved_count=None, embedding_model=None, embedding_dim=None,
        tool_calls=[], media_path=None, media_type="text",
        error=None, run_id=None, project_id=None,
    )
    inserts = [ChunkInsert(i, f"c{i}", "note", 1.0) for i in range(5)]
    ids = await sqlite_mgr.chunks.insert_many(iid, inserts)

    fetched = await sqlite_mgr.chunks.get_by_ids(ids[:3])
    assert len(fetched) == 3
    fetched_ids = {c.id for c in fetched}
    assert fetched_ids == set(ids[:3])


@pytest.mark.asyncio
async def test_get_interaction_ids_for_chunks(sqlite_mgr: SQLiteManager):
    iid = await sqlite_mgr.interactions.insert(
        user_id=3, chat_id=1,
        user_message="m", bot_response="r",
        tags=[], intent=None, model_used=None, temperature=None,
        prompt_tokens=None, response_tokens=None, total_duration_ms=None,
        prompt_used=None, positive_ids=[], negative_ids=[],
        retrieved_count=None, embedding_model=None, embedding_dim=None,
        tool_calls=[], media_path=None, media_type="text",
        error=None, run_id=None, project_id=None,
    )
    ids = await sqlite_mgr.chunks.insert_many(
        iid,
        [ChunkInsert(i, f"c{i}", "note", 1.0) for i in range(3)]
    )
    mapping = await sqlite_mgr.chunks.get_interaction_ids_for_chunks(ids)
    assert all(v == iid for v in mapping.values())
    assert set(mapping.keys()) == set(ids)


@pytest.mark.asyncio
async def test_empty_insert_many(sqlite_mgr: SQLiteManager):
    result = await sqlite_mgr.chunks.insert_many(999, [])
    assert result == []
