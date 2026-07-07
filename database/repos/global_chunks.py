# database/repos/global_chunks.py

from __future__ import annotations

import aiosqlite

from core.exceptions import StorageError
from database.models import GlobalChunk
from database.repos.base import BaseRepo
from database.schema import now_iso


class GlobalChunksRepo(BaseRepo):
    """Chunks da base global de nicho — sem project_id, sem ACL.

    Populada offline (scripts/populate_global_base.py) e lida pelo
    ContrastiveRAG no merge dual. `ativo=0` tira do RAG sem apagar.
    """

    async def insert(
        self,
        *,
        source: str,
        conteudo: str,
        doc_class: str = "norma",
        titulo: str | None = None,
        weight: float = 1.0,
    ) -> int:
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                """
                INSERT INTO global_chunks
                    (source, doc_class, titulo, conteudo, weight, ativo, created_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                """,
                (source, doc_class, titulo, conteudo, weight, now_iso()),
            )
            await conn.commit()
            if cur.lastrowid is None:
                raise StorageError("INSERT em global_chunks não retornou lastrowid.")
            return int(cur.lastrowid)

    async def bulk_insert(
        self, items: list[dict]
    ) -> list[int]:
        """Insere vários chunks; cada item = kwargs de `insert`. Um commit só."""
        if not items:
            return []
        ts = now_iso()
        ids: list[int] = []
        async with aiosqlite.connect(self._db_path) as conn:
            for it in items:
                cur = await conn.execute(
                    """
                    INSERT INTO global_chunks
                        (source, doc_class, titulo, conteudo, weight, ativo, created_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        it["source"],
                        it.get("doc_class", "norma"),
                        it.get("titulo"),
                        it["conteudo"],
                        it.get("weight", 1.0),
                        ts,
                    ),
                )
                if cur.lastrowid is None:
                    raise StorageError("INSERT em global_chunks não retornou lastrowid.")
                ids.append(int(cur.lastrowid))
            await conn.commit()
        return ids

    async def get_by_ids(self, chunk_ids: list[int]) -> list[GlobalChunk]:
        if not chunk_ids:
            return []
        placeholders = ",".join("?" for _ in chunk_ids)
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM global_chunks WHERE id IN ({placeholders})",
                chunk_ids,
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_global_chunk(r) for r in rows]

    async def list_active(self, *, limit: int = 10_000) -> list[GlobalChunk]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM global_chunks WHERE ativo = 1 ORDER BY id LIMIT ?",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_global_chunk(r) for r in rows]

    async def set_ativo(self, chunk_id: int, ativo: bool) -> None:
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "UPDATE global_chunks SET ativo = ? WHERE id = ?",
                (int(ativo), chunk_id),
            )
            await conn.commit()

    async def count(self) -> int:
        async with aiosqlite.connect(self._db_path) as conn:
            async with conn.execute("SELECT COUNT(*) FROM global_chunks") as cur:
                row = await cur.fetchone()
        return int(row[0]) if row else 0


def _row_to_global_chunk(row: aiosqlite.Row) -> GlobalChunk:
    return GlobalChunk(
        id=int(row["id"]),
        source=str(row["source"]),
        doc_class=str(row["doc_class"]),
        titulo=row["titulo"],
        conteudo=str(row["conteudo"]),
        weight=float(row["weight"]),
        ativo=bool(row["ativo"]),
        created_at=str(row["created_at"]),
    )
