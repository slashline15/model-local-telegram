# database/repos/doc_classes.py

from __future__ import annotations

import aiosqlite

from database.models import DocClass
from database.repos.base import BaseRepo


class DocClassesRepo(BaseRepo):
    """Catálogo de classes de documento — peso e ACL editáveis sem código."""

    async def get(self, slug: str) -> DocClass | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM doc_classes WHERE slug = ?", (slug,)
            ) as cur:
                row = await cur.fetchone()
        return _row_to_doc_class(row) if row else None

    async def list_active(self) -> list[DocClass]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM doc_classes WHERE ativo = 1 ORDER BY slug"
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_doc_class(r) for r in rows]


def _row_to_doc_class(row: aiosqlite.Row) -> DocClass:
    return DocClass(
        slug=str(row["slug"]),
        label=str(row["label"]),
        peso=float(row["peso"]),
        nivel_min_classificar=int(row["nivel_min_classificar"]),
        nivel_min_ler=int(row["nivel_min_ler"]),
        ativo=bool(row["ativo"]),
        created_at=str(row["created_at"]),
    )
