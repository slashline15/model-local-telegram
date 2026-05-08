# database/repos/model_pricing.py

from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from database.models import ModelPricing
from database.repos.base import BaseRepo


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


class ModelPricingRepo(BaseRepo):
    """Leitura e upsert de preços de modelos LLM."""

    async def get(self, model: str) -> ModelPricing | None:
        async with aiosqlite.connect(self._db_path) as conn:
            async with conn.execute(
                """
                SELECT model, backend, cost_per_1k_input, cost_per_1k_output,
                       currency, updated_at
                FROM model_pricing WHERE model = ?
                """,
                (model,),
            ) as cur:
                row = await cur.fetchone()
        return _row_to_pricing(row) if row else None

    async def get_all(self) -> list[ModelPricing]:
        async with aiosqlite.connect(self._db_path) as conn:
            async with conn.execute(
                """
                SELECT model, backend, cost_per_1k_input, cost_per_1k_output,
                       currency, updated_at
                FROM model_pricing ORDER BY model
                """
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_pricing(r) for r in rows]

    async def upsert(
        self,
        model: str,
        backend: str,
        cost_per_1k_input: float,
        cost_per_1k_output: float,
        currency: str = "USD",
    ) -> None:
        ts = _now_iso()
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                """
                INSERT INTO model_pricing
                    (model, backend, cost_per_1k_input, cost_per_1k_output,
                     currency, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(model) DO UPDATE SET
                    backend            = excluded.backend,
                    cost_per_1k_input  = excluded.cost_per_1k_input,
                    cost_per_1k_output = excluded.cost_per_1k_output,
                    currency           = excluded.currency,
                    updated_at         = excluded.updated_at
                """,
                (model, backend, cost_per_1k_input, cost_per_1k_output, currency, ts),
            )
            await conn.commit()

    async def calc_cost(
        self,
        model: str,
        prompt_tokens: int,
        response_tokens: int,
    ) -> float:
        """Calcula custo em USD. Retorna 0.0 se modelo não tiver pricing cadastrado."""
        pricing = await self.get(model)
        if pricing is None:
            return 0.0
        return (
            prompt_tokens   * pricing.cost_per_1k_input  / 1000.0
            + response_tokens * pricing.cost_per_1k_output / 1000.0
        )


def _row_to_pricing(r: tuple) -> ModelPricing:
    return ModelPricing(
        model=str(r[0]),
        backend=str(r[1]),
        cost_per_1k_input=float(r[2] or 0),
        cost_per_1k_output=float(r[3] or 0),
        currency=str(r[4]),
        updated_at=str(r[5]),
    )
