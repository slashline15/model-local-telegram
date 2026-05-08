# tests/test_token_usage.py

"""Testa TokenUsageRepo e ModelPricingRepo."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.sqlite_mgr import SQLiteManager  # noqa: E402


@pytest.mark.asyncio
async def test_insert_and_sum_by_user(sqlite_mgr: SQLiteManager):
    await sqlite_mgr.token_usage.insert(
        run_id="run1", user_id=42, model="gemma4:31b-cloud", backend="ollama",
        operation="chat", prompt_tokens=100, response_tokens=50, duration_ms=500,
    )
    await sqlite_mgr.token_usage.insert(
        run_id="run1", user_id=42, model="gemma4:31b-cloud", backend="ollama",
        operation="embedding", prompt_tokens=30, response_tokens=0,
    )

    summs = await sqlite_mgr.token_usage.sum_by_user(42)
    assert len(summs) == 1
    s = summs[0]
    assert s.model == "gemma4:31b-cloud"
    assert s.total_prompt == 130
    assert s.total_response == 50
    assert s.total_tokens == 180
    assert s.count == 2


@pytest.mark.asyncio
async def test_sum_by_model(sqlite_mgr: SQLiteManager):
    for op in ["chat", "embedding", "classify_intent"]:
        await sqlite_mgr.token_usage.insert(
            run_id="r2", user_id=1, model="llama3.2:3b", backend="ollama",
            operation=op, prompt_tokens=10, response_tokens=5,
        )

    summs = await sqlite_mgr.token_usage.sum_by_model()
    by_model = {s.model: s for s in summs}
    assert "llama3.2:3b" in by_model
    assert by_model["llama3.2:3b"].count == 3


@pytest.mark.asyncio
async def test_daily_breakdown(sqlite_mgr: SQLiteManager):
    # Insere registros nos últimos 2 dias.
    for _ in range(3):
        await sqlite_mgr.token_usage.insert(
            run_id="r3", user_id=1, model="gpt-4o-mini", backend="openai",
            operation="chat", prompt_tokens=200, response_tokens=100,
        )

    rows = await sqlite_mgr.token_usage.daily_breakdown(days=7)
    # Deve ter ao menos 1 dia.
    assert len(rows) >= 1
    total_tok = sum(r.total_tokens for r in rows)
    assert total_tok == 900  # 3 × 300


@pytest.mark.asyncio
async def test_model_pricing_calc_cost(sqlite_mgr: SQLiteManager):
    # gpt-4o-mini está no seed: $0.00015/1k input, $0.0006/1k output.
    cost = await sqlite_mgr.model_pricing.calc_cost("gpt-4o-mini", 1000, 500)
    expected = 1000 * 0.00015 / 1000 + 500 * 0.0006 / 1000
    assert abs(cost - expected) < 1e-9


@pytest.mark.asyncio
async def test_model_pricing_zero_for_unknown(sqlite_mgr: SQLiteManager):
    cost = await sqlite_mgr.model_pricing.calc_cost("unknown-model", 9999, 9999)
    assert cost == 0.0


@pytest.mark.asyncio
async def test_model_pricing_upsert(sqlite_mgr: SQLiteManager):
    await sqlite_mgr.model_pricing.upsert(
        "my-model", "custom", 0.01, 0.02, "USD"
    )
    p = await sqlite_mgr.model_pricing.get("my-model")
    assert p is not None
    assert p.cost_per_1k_input == 0.01
    # Upsert novamente com valor diferente.
    await sqlite_mgr.model_pricing.upsert("my-model", "custom", 0.05, 0.1, "USD")
    p2 = await sqlite_mgr.model_pricing.get("my-model")
    assert p2 is not None
    assert p2.cost_per_1k_input == 0.05


@pytest.mark.asyncio
async def test_top_users(sqlite_mgr: SQLiteManager):
    # Registra users com IDs diferentes para ter dados de ranking.
    for uid in [1, 2, 3]:
        for _ in range(uid):  # user 3 tem mais tokens
            await sqlite_mgr.token_usage.insert(
                run_id=f"r-{uid}", user_id=uid, model="llama3.2:3b",
                backend="ollama", operation="chat",
                prompt_tokens=100, response_tokens=50,
            )

    since = "2000-01-01T00:00:00+00:00"
    top = await sqlite_mgr.token_usage.top_users(since, limit=5)
    assert len(top) == 3
    # user 3 tem mais tokens (3×150=450).
    assert top[0][0] == 3
