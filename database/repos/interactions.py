# database/repos/interactions.py

from __future__ import annotations

import json
from typing import Any, Iterable

import aiosqlite

from core.exceptions import StorageError
from database.models import Interaction, StatsSnapshot
from database.repos.base import BaseRepo
from database.schema import now_iso


class InteractionsRepo(BaseRepo):
    """CRUD e métricas sobre a tabela `interactions`."""

    async def insert(
        self,
        *,
        user_id: int,
        chat_id: int | None,
        user_message: str,
        bot_response: str,
        tags: list[str],
        intent: str | None,
        model_used: str | None,
        temperature: float | None,
        prompt_tokens: int | None,
        response_tokens: int | None,
        total_duration_ms: int | None,
        prompt_used: str | None,
        positive_ids: list[int],
        negative_ids: list[int],
        retrieved_count: int | None,
        embedding_model: str | None,
        embedding_dim: int | None,
        tool_calls: list[dict[str, Any]],
        media_path: str | None,
        media_type: str | None,
        error: str | None,
        run_id: str | None,
        project_id: int | None = None,
    ) -> int:
        try:
            async with aiosqlite.connect(self._db_path) as conn:
                cur = await conn.execute(
                    """
                    INSERT INTO interactions (
                        user_id, chat_id, user_message, bot_response, timestamp,
                        media_path, media_type, score, tags, intent, model_used,
                        temperature, prompt_tokens, response_tokens,
                        total_duration_ms, prompt_used, positive_ids, negative_ids,
                        retrieved_count, embedding_model, embedding_dim, tool_calls,
                        error, run_id, project_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id, chat_id, user_message, bot_response, now_iso(),
                        media_path, media_type,
                        json.dumps(tags, ensure_ascii=False),
                        intent, model_used, temperature,
                        prompt_tokens, response_tokens, total_duration_ms,
                        prompt_used,
                        json.dumps(positive_ids),
                        json.dumps(negative_ids),
                        retrieved_count, embedding_model, embedding_dim,
                        json.dumps(tool_calls, ensure_ascii=False, default=str),
                        error, run_id, project_id,
                    ),
                )
                await conn.commit()
                if cur.lastrowid is None:
                    raise StorageError("INSERT não retornou lastrowid.")
                return int(cur.lastrowid)
        except aiosqlite.Error as exc:
            raise StorageError(f"Falha ao inserir interação: {exc}") from exc

    async def update_score(self, interaction_id: int, score: int) -> None:
        if score not in (1, 5):
            raise StorageError(f"Score inválido (esperado 1=ruim ou 5=bom): {score}")
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "UPDATE interactions SET score = ? WHERE id = ?",
                (score, interaction_id),
            )
            await conn.commit()

    async def set_correction(self, interaction_id: int, text: str) -> None:
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "UPDATE interactions SET correction = ? WHERE id = ?",
                (text.strip(), interaction_id),
            )
            await conn.commit()

    async def fetch_by_ids(
        self,
        ids: Iterable[int],
        *,
        requester_user_id: int | None,
        project_id: int | None = None,
    ) -> list[Interaction]:
        """Busca interações por id — isolamento por obra, sem filtro de leitura.

        ACL simplificado (decisão 2026-06): tudo indexado fica disponível pra
        qualquer membro da obra. A segurança está na decisão consciente de
        indexar (confirmação no /doc), não num filtro automático de
        visibilidade. `requester_user_id` mantido na assinatura por
        compatibilidade; hoje não filtra nada.

        - `project_id=<int>`: restringe à obra ativa (evita confusão de
          contexto entre obras do mesmo usuário). `None` = não filtra.
        """
        ids_list = list(ids)
        if not ids_list:
            return []
        placeholders = ",".join("?" for _ in ids_list)
        params: list[Any] = list(ids_list)
        where = f"id IN ({placeholders})"
        if project_id is not None:
            where += " AND project_id = ?"
            params.append(project_id)
        query = f"SELECT * FROM interactions WHERE {where}"
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(query, params) as cur:
                rows = await cur.fetchall()
        return [_row_to_interaction(row) for row in rows]

    async def list_user_history(
        self,
        user_id: int,
        limit: int = 10,
        *,
        project_id: int | None = None,
    ) -> list[Interaction]:
        """Histórico cronológico do usuário, opcionalmente filtrado por obra.

        `project_id=<int>` impede que conversa de outra obra entre no
        contexto do prompt (confusão entre obras do mesmo dono).
        """
        params: list[Any] = [user_id]
        where = "user_id = ?"
        if project_id is not None:
            where += " AND project_id = ?"
            params.append(project_id)
        params.append(int(limit))
        query = (
            f"SELECT * FROM interactions WHERE {where} "
            f"ORDER BY id DESC LIMIT ?"
        )
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(query, params) as cur:
                rows = await cur.fetchall()
        return [_row_to_interaction(r) for r in rows]

    async def stats(self, faiss_indexed: int) -> StatsSnapshot:
        async with aiosqlite.connect(self._db_path) as conn:
            async with conn.execute(
                """
                SELECT
                    COUNT(*),
                    SUM(CASE WHEN score IS NOT NULL THEN 1 ELSE 0 END),
                    SUM(CASE WHEN score >= 4 THEN 1 ELSE 0 END),
                    SUM(CASE WHEN score <= 2 AND score IS NOT NULL THEN 1 ELSE 0 END),
                    COUNT(DISTINCT user_id),
                    COUNT(DISTINCT intent),
                    AVG(total_duration_ms)
                FROM interactions
                """
            ) as cur:
                row = await cur.fetchone()
            async with conn.execute(
                "SELECT run_id FROM interactions WHERE run_id IS NOT NULL "
                "ORDER BY id DESC LIMIT 1"
            ) as cur:
                last = await cur.fetchone()

        total, rated, pos, neg, users, intents, avg_lat = row or (0, 0, 0, 0, 0, 0, None)
        return StatsSnapshot(
            total_interactions=int(total or 0),
            rated=int(rated or 0),
            positives=int(pos or 0),
            negatives=int(neg or 0),
            distinct_users=int(users or 0),
            distinct_intents=int(intents or 0),
            avg_latency_ms=float(avg_lat) if avg_lat is not None else None,
            last_run_id=str(last[0]) if last else None,
            faiss_indexed=int(faiss_indexed),
        )


def _row_to_interaction(row: aiosqlite.Row) -> Interaction:
    def _json_list(key: str) -> list[Any]:
        raw = row[key] if key in row.keys() else None
        if not raw:
            return []
        try:
            val = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return []
        return val if isinstance(val, list) else []

    def _opt(key: str) -> Any:
        return row[key] if key in row.keys() else None

    return Interaction(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        chat_id=_opt("chat_id"),
        user_message=str(row["user_message"]),
        bot_response=str(row["bot_response"]),
        timestamp=str(row["timestamp"]),
        media_path=_opt("media_path"),
        media_type=_opt("media_type"),
        score=_opt("score"),
        tags=_json_list("tags"),
        intent=_opt("intent"),
        model_used=_opt("model_used"),
        temperature=_opt("temperature"),
        prompt_tokens=_opt("prompt_tokens"),
        response_tokens=_opt("response_tokens"),
        total_duration_ms=_opt("total_duration_ms"),
        prompt_used=_opt("prompt_used"),
        positive_ids=[int(x) for x in _json_list("positive_ids")],
        negative_ids=[int(x) for x in _json_list("negative_ids")],
        retrieved_count=_opt("retrieved_count"),
        embedding_model=_opt("embedding_model"),
        embedding_dim=_opt("embedding_dim"),
        tool_calls=[x for x in _json_list("tool_calls") if isinstance(x, dict)],
        error=_opt("error"),
        run_id=_opt("run_id"),
        correction=_opt("correction"),
        visibilidade=str(_opt("visibilidade") or "publica"),
    )
