# database/repos/chunks.py

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

from database.models import InteractionChunk
from database.repos.base import BaseRepo


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


@dataclass
class ChunkInsert:
    chunk_idx: int
    content: str
    doc_class: str
    weight: float
    document_id: int | None = None  # preenchido quando o chunk vem de /doc


class ChunksRepo(BaseRepo):
    """CRUD para interaction_chunks — mapeamento chunk_id ↔ interaction_id."""

    async def insert(
        self,
        interaction_id: int,
        chunk_idx: int,
        content: str,
        doc_class: str,
        weight: float,
    ) -> int:
        """Insere um chunk e retorna o chunk_id gerado."""
        ts = _now_iso()
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                """
                INSERT INTO interaction_chunks
                    (interaction_id, chunk_idx, content, doc_class, weight, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (interaction_id, chunk_idx, content, doc_class, weight, ts),
            )
            await conn.commit()
            assert cur.lastrowid is not None
            return int(cur.lastrowid)

    async def insert_many(
        self,
        interaction_id: int,
        chunks: list[ChunkInsert],
    ) -> list[int]:
        """Bulk insert — retorna lista de chunk_ids na mesma ordem."""
        if not chunks:
            return []
        ts = _now_iso()
        chunk_ids: list[int] = []
        async with aiosqlite.connect(self._db_path) as conn:
            for c in chunks:
                cur = await conn.execute(
                    """
                    INSERT INTO interaction_chunks
                        (interaction_id, chunk_idx, content, doc_class, weight,
                         document_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        interaction_id, c.chunk_idx, c.content, c.doc_class,
                        c.weight, c.document_id, ts,
                    ),
                )
                assert cur.lastrowid is not None
                chunk_ids.append(int(cur.lastrowid))
            await conn.commit()
        return chunk_ids

    async def get_by_interaction(self, interaction_id: int) -> list[InteractionChunk]:
        async with aiosqlite.connect(self._db_path) as conn:
            async with conn.execute(
                """
                SELECT id, interaction_id, chunk_idx, content, doc_class, weight,
                       created_at, document_id
                FROM interaction_chunks WHERE interaction_id = ? ORDER BY chunk_idx
                """,
                (interaction_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_chunk(r) for r in rows]

    async def get_by_ids(self, chunk_ids: list[int]) -> list[InteractionChunk]:
        if not chunk_ids:
            return []
        placeholders = ",".join("?" * len(chunk_ids))
        async with aiosqlite.connect(self._db_path) as conn:
            async with conn.execute(
                f"""
                SELECT id, interaction_id, chunk_idx, content, doc_class, weight,
                       created_at, document_id
                FROM interaction_chunks WHERE id IN ({placeholders})
                """,
                chunk_ids,
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_chunk(r) for r in rows]

    async def get_interaction_ids_for_chunks(
        self, chunk_ids: list[int]
    ) -> dict[int, int]:
        """Retorna {chunk_id: interaction_id}."""
        if not chunk_ids:
            return {}
        placeholders = ",".join("?" * len(chunk_ids))
        async with aiosqlite.connect(self._db_path) as conn:
            async with conn.execute(
                f"""
                SELECT id, interaction_id FROM interaction_chunks
                WHERE id IN ({placeholders})
                """,
                chunk_ids,
            ) as cur:
                rows = await cur.fetchall()
        return {int(r[0]): int(r[1]) for r in rows}


def _row_to_chunk(r: tuple) -> InteractionChunk:
    return InteractionChunk(
        id=int(r[0]),
        interaction_id=int(r[1]),
        chunk_idx=int(r[2]),
        content=str(r[3]),
        doc_class=str(r[4]),
        weight=float(r[5]),
        created_at=str(r[6]),
        document_id=int(r[7]) if len(r) > 7 and r[7] is not None else None,
    )
