# core/pipeline.py

from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from core.log_context import set_run_id
from core.logger import get_logger

log = get_logger("pipeline")

_MAX_DETAIL_CHARS: int = 240


@dataclass(slots=True)
class PipelineStep:
    name: str
    started_at: float
    ended_at: float | None = None
    status: str = "started"
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def duration_ms(self) -> int:
        end = self.ended_at if self.ended_at is not None else time.monotonic()
        return int((end - self.started_at) * 1000)

    def set(self, **kwargs: Any) -> None:
        """Anota detalhes que serão logados ao fechar a etapa e persistidos no DB."""
        self.details.update(kwargs)


@dataclass(slots=True)
class PipelineRun:
    run_id: str
    user_id: int
    chat_id: int | None
    started_at: float
    steps: list[PipelineStep] = field(default_factory=list)

    @property
    def total_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)

    @property
    def has_errors(self) -> bool:
        return any(s.status == "error" for s in self.steps)

    @property
    def first_error(self) -> str | None:
        for s in self.steps:
            if s.status == "error" and s.error:
                return f"{s.name}: {s.error}"
        return None


class PipelineRecorder:
    """Mede e loga cada etapa de processamento de uma mensagem.

    Uso:
        rec = PipelineRecorder(user_id=123, chat_id=456)
        async with rec.step("intent", text_len=42) as s:
            intent = await classify(...)
            s.set(intent=intent)

        # ao fim:
        log.info(rec.summary())
        await sqlite.save_pipeline_steps(...rec.to_rows())
    """

    def __init__(self, user_id: int, chat_id: int | None) -> None:
        self.run: PipelineRun = PipelineRun(
            run_id=uuid.uuid4().hex[:12],
            user_id=user_id,
            chat_id=chat_id,
            started_at=time.monotonic(),
        )
        # Propaga run_id para o logger via ContextVar — todos os logs desta
        # Task asyncio passam a carregar o id automaticamente.
        set_run_id(self.run.run_id)
        self._tag: str = f"[u={user_id}]"

    @asynccontextmanager
    async def step(self, name: str, **details: Any) -> AsyncIterator[PipelineStep]:
        step = PipelineStep(
            name=name,
            started_at=time.monotonic(),
            details=dict(details),
        )
        self.run.steps.append(step)
        idx = len(self.run.steps)
        log.info(
            "%s ▶ [%02d] %-26s start  %s",
            self._tag, idx, name, _short_json(step.details),
        )
        try:
            yield step
        except Exception as exc:  # noqa: BLE001
            step.status = "error"
            step.error = f"{type(exc).__name__}: {exc}"
            step.ended_at = time.monotonic()
            log.error(
                "%s ✖ [%02d] %-26s ERROR  %5dms  %s",
                self._tag, idx, name, step.duration_ms, step.error,
            )
            raise
        else:
            step.status = "ok"
            step.ended_at = time.monotonic()
            log.info(
                "%s ✓ [%02d] %-26s ok     %5dms  %s",
                self._tag, idx, name, step.duration_ms,
                _short_json(step.details),
            )

    def skipped(self, name: str, reason: str, **details: Any) -> None:
        """Registra uma etapa pulada (não executou)."""
        step = PipelineStep(
            name=name,
            started_at=time.monotonic(),
            ended_at=time.monotonic(),
            status="skipped",
            details={"reason": reason, **details},
        )
        self.run.steps.append(step)
        idx = len(self.run.steps)
        log.info(
            "%s • [%02d] %-26s skip   %5dms  %s",
            self._tag, idx, name, 0, _short_json(step.details),
        )

    def summary(self) -> str:
        lines = [
            f"───── pipeline {self.run.run_id} (user={self.run.user_id}, "
            f"chat={self.run.chat_id}) total≈{self.run.total_ms}ms ─────"
        ]
        for i, s in enumerate(self.run.steps, 1):
            symbol = {"ok": "✓", "error": "✖", "skipped": "•"}.get(s.status, "?")
            lines.append(
                f"  {symbol} [{i:02d}] {s.name:30s} "
                f"{s.status:7s} {s.duration_ms:5d}ms  "
                f"{_short_json(s.details)}"
            )
        if self.run.has_errors:
            lines.append(f"  !! first_error = {self.run.first_error}")
        lines.append("─" * 70)
        return "\n".join(lines)

    def to_rows(self) -> list["PipelineStepRow"]:
        from database.sqlite_mgr import PipelineStepRow
        return [
            PipelineStepRow(
                run_id=self.run.run_id,
                step_index=i,
                step_name=s.name,
                status=s.status,
                duration_ms=s.duration_ms,
                details=s.details,
                error=s.error,
            )
            for i, s in enumerate(self.run.steps, 1)
        ]


def _short_json(obj: dict[str, Any], max_len: int = _MAX_DETAIL_CHARS) -> str:
    if not obj:
        return ""
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str, separators=(",", "="))
    except (TypeError, ValueError):
        s = str(obj)
    if len(s) > max_len:
        s = s[: max_len - 1] + "…"
    return s
