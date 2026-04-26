from __future__ import annotations

import pytest

from core.pipeline import PipelineRecorder


@pytest.mark.asyncio
async def test_records_step_ok() -> None:
    rec = PipelineRecorder(user_id=1, chat_id=2)
    async with rec.step("etapa", k=1) as s:
        s.set(value=42)
    assert len(rec.run.steps) == 1
    step = rec.run.steps[0]
    assert step.status == "ok"
    assert step.details == {"k": 1, "value": 42}
    assert step.duration_ms >= 0


@pytest.mark.asyncio
async def test_records_step_error_and_reraises() -> None:
    rec = PipelineRecorder(user_id=1, chat_id=2)
    with pytest.raises(RuntimeError):
        async with rec.step("falha"):
            raise RuntimeError("boom")
    step = rec.run.steps[0]
    assert step.status == "error"
    assert "boom" in (step.error or "")
    assert rec.run.has_errors
    assert "falha" in (rec.run.first_error or "")


def test_skipped_step() -> None:
    rec = PipelineRecorder(user_id=1, chat_id=None)
    rec.skipped("transcribe", reason="sem audio")
    assert rec.run.steps[0].status == "skipped"
    assert rec.run.steps[0].details["reason"] == "sem audio"


def test_to_rows_shape() -> None:
    rec = PipelineRecorder(user_id=1, chat_id=1)
    rec.skipped("x", reason="r")
    rows = rec.to_rows()
    assert len(rows) == 1
    assert rows[0].step_index == 1
    assert rows[0].status == "skipped"


def test_summary_renders_lines() -> None:
    rec = PipelineRecorder(user_id=1, chat_id=1)
    rec.skipped("x", reason="r")
    s = rec.summary()
    assert "pipeline" in s
    assert "x" in s
