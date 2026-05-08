# database/repos/token_usage.py

from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from database.models import DailyTokenRow, TokenUsageRow, TokenUsageSummary
from database.repos.base import BaseRepo


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


class TokenUsageRepo(BaseRepo):
    """Persistência e consultas de consumo de tokens por operação LLM."""

    async def insert(
        self,
        *,
        run_id: str,
        user_id: int,
        model: str,
        backend: str,
        operation: str,
        prompt_tokens: int = 0,
        response_tokens: int = 0,
        duration_ms: int = 0,
        quantity_secondary: float = 0.0,
        interaction_id: int | None = None,
        project_id: int | None = None,
    ) -> int:
        total = prompt_tokens + response_tokens
        ts = _now_iso()
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                """
                INSERT INTO token_usage
                    (run_id, interaction_id, user_id, project_id, model, backend,
                     operation, prompt_tokens, response_tokens, total_tokens,
                     duration_ms, quantity_secondary, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, interaction_id, user_id, project_id, model, backend,
                    operation, prompt_tokens, response_tokens, total,
                    duration_ms, quantity_secondary, ts,
                ),
            )
            await conn.commit()
            assert cur.lastrowid is not None
            return int(cur.lastrowid)

    async def sum_by_user(
        self, user_id: int, since: str | None = None
    ) -> list[TokenUsageSummary]:
        return await self._sum_grouped(where="user_id = ?", params=[user_id], since=since)

    async def sum_by_project(
        self, project_id: int, since: str | None = None
    ) -> list[TokenUsageSummary]:
        return await self._sum_grouped(
            where="project_id = ?", params=[project_id], since=since
        )

    async def sum_by_model(self, since: str | None = None) -> list[TokenUsageSummary]:
        return await self._sum_grouped(where=None, params=[], since=since)

    async def _sum_grouped(
        self,
        where: str | None,
        params: list,
        since: str | None,
    ) -> list[TokenUsageSummary]:
        """Agrupa por (model, backend) e junta custo via subquery."""
        conditions: list[str] = []
        all_params: list = list(params)
        if where:
            conditions.append(where)
        if since:
            conditions.append("t.created_at >= ?")
            all_params.append(since)
        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        sql = f"""
        SELECT
            t.model,
            t.backend,
            SUM(t.prompt_tokens)    AS total_prompt,
            SUM(t.response_tokens)  AS total_response,
            SUM(t.total_tokens)     AS total_tokens,
            SUM(t.duration_ms)      AS total_duration_ms,
            COALESCE(SUM(
                t.prompt_tokens  * COALESCE(p.cost_per_1k_input,  0) / 1000.0 +
                t.response_tokens* COALESCE(p.cost_per_1k_output, 0) / 1000.0
            ), 0)                   AS cost_usd,
            COUNT(*)                AS cnt
        FROM token_usage t
        LEFT JOIN model_pricing p ON p.model = t.model
        {where_clause}
        GROUP BY t.model, t.backend
        ORDER BY total_tokens DESC
        """
        async with aiosqlite.connect(self._db_path) as conn:
            async with conn.execute(sql, all_params) as cur:
                rows = await cur.fetchall()
        return [
            TokenUsageSummary(
                model=str(r[0]),
                backend=str(r[1]),
                total_prompt=int(r[2] or 0),
                total_response=int(r[3] or 0),
                total_tokens=int(r[4] or 0),
                total_duration_ms=int(r[5] or 0),
                cost_usd=float(r[6] or 0),
                count=int(r[7] or 0),
            )
            for r in rows
        ]

    async def daily_breakdown(
        self,
        days: int = 7,
        user_id: int | None = None,
        project_id: int | None = None,
    ) -> list[DailyTokenRow]:
        """Retorna totais diários dos últimos `days` dias (sparkline)."""
        conditions: list[str] = [
            f"created_at >= date('now', '-{days} days')"
        ]
        params: list = []
        if user_id is not None:
            conditions.append("t.user_id = ?")
            params.append(user_id)
        if project_id is not None:
            conditions.append("t.project_id = ?")
            params.append(project_id)
        where_clause = "WHERE " + " AND ".join(conditions)

        sql = f"""
        SELECT
            substr(t.created_at, 1, 10)         AS day,
            SUM(t.total_tokens)                 AS total_tokens,
            COALESCE(SUM(
                t.prompt_tokens  * COALESCE(p.cost_per_1k_input,  0) / 1000.0 +
                t.response_tokens* COALESCE(p.cost_per_1k_output, 0) / 1000.0
            ), 0)                               AS cost_usd
        FROM token_usage t
        LEFT JOIN model_pricing p ON p.model = t.model
        {where_clause}
        GROUP BY day
        ORDER BY day
        """
        async with aiosqlite.connect(self._db_path) as conn:
            async with conn.execute(sql, params) as cur:
                rows = await cur.fetchall()
        return [
            DailyTokenRow(
                date=str(r[0]),
                total_tokens=int(r[1] or 0),
                cost_usd=float(r[2] or 0),
            )
            for r in rows
        ]

    async def top_users(
        self, since: str, limit: int = 10
    ) -> list[tuple[int, str, int, float]]:
        """(user_id, name, total_tokens, cost_usd) — JOIN com users."""
        sql = """
        SELECT
            t.user_id,
            COALESCE(u.name, CAST(t.user_id AS TEXT)) AS name,
            SUM(t.total_tokens) AS total_tokens,
            COALESCE(SUM(
                t.prompt_tokens  * COALESCE(p.cost_per_1k_input,  0) / 1000.0 +
                t.response_tokens* COALESCE(p.cost_per_1k_output, 0) / 1000.0
            ), 0) AS cost_usd
        FROM token_usage t
        LEFT JOIN users u ON u.id = t.user_id
        LEFT JOIN model_pricing p ON p.model = t.model
        WHERE t.created_at >= ?
        GROUP BY t.user_id
        ORDER BY total_tokens DESC
        LIMIT ?
        """
        async with aiosqlite.connect(self._db_path) as conn:
            async with conn.execute(sql, [since, limit]) as cur:
                rows = await cur.fetchall()
        return [(int(r[0]), str(r[1]), int(r[2] or 0), float(r[3] or 0)) for r in rows]

    async def top_projects(
        self, since: str, limit: int = 10
    ) -> list[tuple[int, str, int, float]]:
        """(project_id, name, total_tokens, cost_usd) — JOIN com projects."""
        sql = """
        SELECT
            t.project_id,
            COALESCE(pr.name, CAST(t.project_id AS TEXT)) AS name,
            SUM(t.total_tokens) AS total_tokens,
            COALESCE(SUM(
                t.prompt_tokens  * COALESCE(p.cost_per_1k_input,  0) / 1000.0 +
                t.response_tokens* COALESCE(p.cost_per_1k_output, 0) / 1000.0
            ), 0) AS cost_usd
        FROM token_usage t
        LEFT JOIN projects pr ON pr.id = t.project_id
        LEFT JOIN model_pricing p ON p.model = t.model
        WHERE t.created_at >= ? AND t.project_id IS NOT NULL
        GROUP BY t.project_id
        ORDER BY total_tokens DESC
        LIMIT ?
        """
        async with aiosqlite.connect(self._db_path) as conn:
            async with conn.execute(sql, [since, limit]) as cur:
                rows = await cur.fetchall()
        return [(int(r[0]), str(r[1]), int(r[2] or 0), float(r[3] or 0)) for r in rows]

    async def total_for_period(
        self,
        since: str,
        until: str | None = None,
        user_id: int | None = None,
        project_id: int | None = None,
    ) -> tuple[int, float]:
        """(total_tokens, cost_usd) para um período arbitrário."""
        conditions = ["t.created_at >= ?"]
        params: list = [since]
        if until:
            conditions.append("t.created_at < ?")
            params.append(until)
        if user_id is not None:
            conditions.append("t.user_id = ?")
            params.append(user_id)
        if project_id is not None:
            conditions.append("t.project_id = ?")
            params.append(project_id)
        where_clause = "WHERE " + " AND ".join(conditions)

        sql = f"""
        SELECT
            SUM(t.total_tokens),
            COALESCE(SUM(
                t.prompt_tokens  * COALESCE(p.cost_per_1k_input,  0) / 1000.0 +
                t.response_tokens* COALESCE(p.cost_per_1k_output, 0) / 1000.0
            ), 0)
        FROM token_usage t
        LEFT JOIN model_pricing p ON p.model = t.model
        {where_clause}
        """
        async with aiosqlite.connect(self._db_path) as conn:
            async with conn.execute(sql, params) as cur:
                row = await cur.fetchone()
        if row is None:
            return 0, 0.0
        return int(row[0] or 0), float(row[1] or 0)
