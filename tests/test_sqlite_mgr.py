from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from database.sqlite_mgr import PipelineStepRow, SQLiteManager


pytestmark = pytest.mark.asyncio


_LEGACY_INTERACTIONS_SQL = """
CREATE TABLE IF NOT EXISTS interactions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    user_message  TEXT    NOT NULL,
    bot_response  TEXT    NOT NULL,
    timestamp     TEXT    NOT NULL,
    media_path    TEXT,
    score         INTEGER,
    tags          TEXT    NOT NULL DEFAULT '[]'
);
"""

_LEGACY_USER_SETTINGS_SQL = """
CREATE TABLE IF NOT EXISTS user_settings (
    user_id       INTEGER PRIMARY KEY,
    current_model TEXT    NOT NULL DEFAULT 'gemma:2b',
    temperature   REAL    NOT NULL DEFAULT 0.7
);
"""


async def test_insert_and_fetch_interaction(sqlite_mgr: SQLiteManager) -> None:
    iid = await sqlite_mgr.insert_interaction(
        user_id=1, chat_id=10, user_message="oi", bot_response="olá",
        tags=["chat"], intent="chitchat",
        model_used="test:1b", temperature=0.7,
        prompt_tokens=5, response_tokens=8, total_duration_ms=100,
        prompt_used="prompt aqui", positive_ids=[2, 3], negative_ids=[],
        retrieved_count=0, embedding_model="fake-embed", embedding_dim=8,
        tool_calls=[{"name": "x"}], media_path=None, media_type="text",
        error=None, run_id="abc",
    )
    assert iid > 0

    rows = await sqlite_mgr.fetch_by_ids([iid])
    assert len(rows) == 1
    r = rows[0]
    assert r.intent == "chitchat"
    assert r.tags == ["chat"]
    assert r.positive_ids == [2, 3]
    assert r.tool_calls == [{"name": "x"}]
    assert r.embedding_dim == 8
    assert r.score is None


async def test_update_score_validates_range(sqlite_mgr: SQLiteManager) -> None:
    iid = await sqlite_mgr.insert_interaction(
        user_id=1, chat_id=None, user_message="x", bot_response="y",
        tags=[], intent=None, model_used=None, temperature=None,
        prompt_tokens=None, response_tokens=None, total_duration_ms=None,
        prompt_used=None, positive_ids=[], negative_ids=[],
        retrieved_count=None, embedding_model=None, embedding_dim=None,
        tool_calls=[], media_path=None, media_type=None,
        error=None, run_id=None,
    )
    await sqlite_mgr.update_score(iid, 5)
    rows = await sqlite_mgr.fetch_by_ids([iid])
    assert rows[0].score == 5

    with pytest.raises(Exception):
        await sqlite_mgr.update_score(iid, 9)


async def test_user_settings_lifecycle(sqlite_mgr: SQLiteManager) -> None:
    s = await sqlite_mgr.get_user_settings(42)
    assert s.user_id == 42
    assert s.current_model == "test:1b"
    assert s.temperature == pytest.approx(0.7)

    await sqlite_mgr.set_user_model(42, "llama3:8b")
    await sqlite_mgr.set_user_temperature(42, 0.3)
    s2 = await sqlite_mgr.get_user_settings(42)
    assert s2.current_model == "llama3:8b"
    assert s2.temperature == pytest.approx(0.3)

    s3 = await sqlite_mgr.reset_user_settings(42)
    assert s3.current_model == "test:1b"


async def test_stats_and_history(sqlite_mgr: SQLiteManager) -> None:
    for n in range(3):
        await sqlite_mgr.insert_interaction(
            user_id=1, chat_id=1, user_message=f"q{n}", bot_response=f"a{n}",
            tags=["x"], intent="question",
            model_used="m", temperature=0.5,
            prompt_tokens=1, response_tokens=2, total_duration_ms=10 * (n + 1),
            prompt_used="p", positive_ids=[], negative_ids=[],
            retrieved_count=0, embedding_model="e", embedding_dim=8,
            tool_calls=[], media_path=None, media_type="text",
            error=None, run_id="r1",
        )
    snap = await sqlite_mgr.stats(faiss_indexed=99)
    assert snap.total_interactions == 3
    assert snap.faiss_indexed == 99
    assert snap.distinct_users == 1
    assert snap.last_run_id == "r1"

    hist = await sqlite_mgr.list_user_history(1, limit=2)
    assert len(hist) == 2


async def test_migrates_legacy_db(tmp_path: Path) -> None:
    """DB criado pela versão antiga (sem `intent`, `run_id`, etc.) deve migrar."""
    db_path = tmp_path / "legacy.db"

    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(_LEGACY_INTERACTIONS_SQL)
        await conn.execute(_LEGACY_USER_SETTINGS_SQL)
        await conn.execute(
            "INSERT INTO interactions (user_id, user_message, bot_response, timestamp) "
            "VALUES (1, 'oi', 'olá', '2025-01-01T00:00:00')"
        )
        await conn.commit()

    mgr = SQLiteManager(db_path=db_path, default_model="test:1b")
    await mgr.init_schema()

    new_id = await mgr.insert_interaction(
        user_id=1, chat_id=99, user_message="x", bot_response="y",
        tags=["a"], intent="question",
        model_used="m", temperature=0.5,
        prompt_tokens=1, response_tokens=2, total_duration_ms=3,
        prompt_used="p", positive_ids=[1], negative_ids=[],
        retrieved_count=0, embedding_model="e", embedding_dim=8,
        tool_calls=[], media_path=None, media_type="text",
        error=None, run_id="abc",
    )
    rows = await mgr.fetch_by_ids([new_id])
    assert rows and rows[0].intent == "question"
    assert rows[0].run_id == "abc"
    snap = await mgr.stats(faiss_indexed=0)
    assert snap.total_interactions == 2


async def test_save_pipeline_steps(sqlite_mgr: SQLiteManager) -> None:
    steps = [
        PipelineStepRow(
            run_id="r", step_index=1, step_name="x",
            status="ok", duration_ms=12, details={"k": 1}, error=None,
        ),
        PipelineStepRow(
            run_id="r", step_index=2, step_name="y",
            status="error", duration_ms=3, details={}, error="boom",
        ),
    ]
    await sqlite_mgr.save_pipeline_steps(
        run_id="r", user_id=1, chat_id=2, interaction_id=None, steps=steps
    )
