# database/repos/documents.py

from __future__ import annotations

import aiosqlite

from core.exceptions import StorageError
from database.models import Document
from database.repos.base import BaseRepo
from database.schema import now_iso


class DocumentsRepo(BaseRepo):
    """Uploads classificados via /doc — ciclo de vida separado de interactions."""

    async def insert(
        self,
        *,
        uid: str,
        project_id: int,
        doc_class: str,
        titulo: str,
        enviado_por: int,
        arquivo_path: str | None = None,
        arquivo_hash: str | None = None,
        mime: str | None = None,
        interaction_id: int | None = None,
        visibilidade: str = "publica",
    ) -> int:
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                """
                INSERT INTO documents
                    (uid, project_id, doc_class, titulo, arquivo_path,
                     arquivo_hash, mime, enviado_por, interaction_id,
                     visibilidade, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uid, project_id, doc_class, titulo, arquivo_path,
                    arquivo_hash, mime, enviado_por, interaction_id,
                    visibilidade, now_iso(),
                ),
            )
            await conn.commit()
            if cur.lastrowid is None:
                raise StorageError("INSERT em documents não retornou lastrowid.")
            return int(cur.lastrowid)

    async def get_by_uid(self, uid: str) -> Document | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM documents WHERE uid = ?", (uid,)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_document(row) if row else None

    async def list_for_project(
        self, project_id: int, *, limit: int = 50
    ) -> list[Document]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM documents WHERE project_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (project_id, limit),
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_document(r) for r in rows]


def _row_to_document(row: aiosqlite.Row) -> Document:
    return Document(
        id=int(row["id"]),
        uid=str(row["uid"]),
        project_id=int(row["project_id"]),
        doc_class=str(row["doc_class"]),
        titulo=str(row["titulo"]),
        arquivo_path=row["arquivo_path"],
        arquivo_hash=row["arquivo_hash"],
        mime=row["mime"],
        enviado_por=int(row["enviado_por"]),
        interaction_id=(
            int(row["interaction_id"]) if row["interaction_id"] is not None else None
        ),
        visibilidade=str(row["visibilidade"]) if "visibilidade" in row.keys() else "publica",
        created_at=str(row["created_at"]),
    )
