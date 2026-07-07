"""Reconstroi os índices FAISS a partir do SQLite.

Quando usar:
- Ajustes em embedding_model, embedding_dim ou formato do texto embedado.
- Entradas órfãs no SQLite que falharam ao indexar.

Como rodar:
    python -m scripts.reindex                      # índice das obras (default)
    python -m scripts.reindex --scope global       # base global de nicho
    python -m scripts.reindex --scope all          # os dois
    python -m scripts.reindex --dry-run            # só lista, não escreve

O índice em disco é apagado antes da reconstrução. O SQLite NÃO é tocado.

Nota (pós-refactor de chunking): o índice local guarda chunk_ids de
interaction_chunks — o rebuild embeda o `content` armazenado de cada chunk.
Interações antigas sem chunks são re-chunkadas a partir de USER/BOT.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import aiosqlite

# Permite rodar como `python scripts/reindex.py` direto.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.chunking import chunk_text  # noqa: E402
from core.config import get_settings  # noqa: E402
from core.logger import get_logger, setup_logging  # noqa: E402
from database.faiss_mgr import FaissManager  # noqa: E402
from database.repos.chunks import ChunkInsert  # noqa: E402
from database.sqlite_mgr import SQLiteManager  # noqa: E402
from llm.ollama_client import OllamaClient  # noqa: E402

# Mesmo limite usado no pipeline (tg/handlers/pipeline.py::_EMBED_INPUT_MAX_CHARS).
_EMBED_INPUT_MAX_CHARS: int = 3000


async def _interactions_sem_chunks(db_path: Path) -> list[tuple[int, str, str]]:
    """Interações antigas (pré-chunking) que não têm linha em interaction_chunks."""
    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute(
            """
            SELECT i.id, i.user_message, i.bot_response
            FROM interactions i
            LEFT JOIN interaction_chunks c ON c.interaction_id = i.id
            WHERE c.id IS NULL
            ORDER BY i.id
            """
        ) as cur:
            rows = await cur.fetchall()
    return [(int(r[0]), str(r[1] or ""), str(r[2] or "")) for r in rows]


async def _all_chunks(db_path: Path) -> list[tuple[int, str]]:
    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute(
            "SELECT id, content FROM interaction_chunks ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
    return [(int(r[0]), str(r[1] or "")) for r in rows]


def _fresh_faiss(settings, index_path: Path, id_map_path: Path, log) -> FaissManager:
    for p in (index_path, id_map_path):
        if p.exists():
            p.unlink()
            log.info("Removido %s", p)
    return FaissManager(
        dim=settings.embedding_dim,
        index_path=index_path,
        id_map_path=id_map_path,
    )


async def _reindex_project_scope(
    settings, sqlite: SQLiteManager, ollama: OllamaClient, dry_run: bool, log
) -> tuple[int, int]:
    """Reconstrói o índice das obras a partir de interaction_chunks."""
    # Backfill: interações antigas sem chunks ganham chunks agora.
    orphans = await _interactions_sem_chunks(settings.sqlite_path)
    chunks = await _all_chunks(settings.sqlite_path)
    log.info(
        "Escopo project: %d chunk(s) existentes, %d interação(ões) sem chunk.",
        len(chunks), len(orphans),
    )
    if dry_run:
        for cid, content in chunks[:20]:
            log.info("  • chunk=%d  %r", cid, content.replace("\n", " ")[:70])
        if len(chunks) > 20:
            log.info("  … +%d chunk(s)", len(chunks) - 20)
        for iid, u, _ in orphans:
            log.info("  • interação órfã id=%d  user=%r", iid, u.replace("\n", " ")[:60])
        return 0, 0

    faiss = _fresh_faiss(
        settings, settings.faiss_index_path, settings.faiss_id_map_path, log
    )
    await faiss.init()

    ok = 0
    fail = 0
    for iid, user_msg, bot_resp in orphans:
        base_text = f"USER: {user_msg}\nBOT: {bot_resp}"
        raw = chunk_text(base_text, settings.chunk_size, settings.chunk_overlap)
        inserts = [
            ChunkInsert(chunk_idx=i, content=c[:500], doc_class="note", weight=1.0)
            for i, c in enumerate(raw)
        ]
        new_ids = await sqlite.chunks.insert_many(iid, inserts)
        chunks.extend(zip(new_ids, (c[:500] for c in raw)))

    for chunk_id, content in chunks:
        if not content.strip():
            continue
        try:
            vec = await ollama.embed(content[:_EMBED_INPUT_MAX_CHARS])
            await faiss.add(chunk_id, vec)
            ok += 1
            if ok % 25 == 0:
                log.info("Indexados %d/%d...", ok, len(chunks))
        except Exception as exc:  # noqa: BLE001
            fail += 1
            log.warning("Falha ao indexar chunk=%d: %s", chunk_id, exc)

    log.info("Escopo project: %d OK, %d falhas. ntotal=%d", ok, fail, faiss.ntotal)
    return ok, fail


async def _reindex_global_scope(
    settings, sqlite: SQLiteManager, ollama: OllamaClient, dry_run: bool, log
) -> tuple[int, int]:
    """Reconstrói o índice da base global a partir de global_chunks ativos."""
    rows = await sqlite.global_chunks.list_active()
    log.info("Escopo global: %d chunk(s) ativos.", len(rows))
    if dry_run:
        for c in rows[:20]:
            log.info("  • g%d  %r", c.id, (c.titulo or c.conteudo)[:70])
        if len(rows) > 20:
            log.info("  … +%d chunk(s)", len(rows) - 20)
        return 0, 0

    faiss_global = _fresh_faiss(
        settings, settings.faiss_global_index_path,
        settings.faiss_global_id_map_path, log,
    )
    await faiss_global.init()

    ok = 0
    fail = 0
    for c in rows:
        try:
            vec = await ollama.embed(c.conteudo[:_EMBED_INPUT_MAX_CHARS])
            await faiss_global.add(c.id, vec)
            ok += 1
            if ok % 25 == 0:
                log.info("Indexados %d/%d...", ok, len(rows))
        except Exception as exc:  # noqa: BLE001
            fail += 1
            log.warning("Falha ao indexar global_chunk=%d: %s", c.id, exc)

    log.info(
        "Escopo global: %d OK, %d falhas. ntotal=%d", ok, fail, faiss_global.ntotal
    )
    return ok, fail


async def reindex(dry_run: bool, scope: str) -> int:
    settings = get_settings()
    setup_logging(level=settings.log_level)
    log = get_logger("reindex")

    sqlite = SQLiteManager(
        db_path=settings.sqlite_path,
        default_model=settings.ollama_default_model,
    )
    await sqlite.init_schema()

    ollama = OllamaClient(
        host=settings.ollama_host,
        default_model=settings.ollama_default_model,
        embedding_model=settings.ollama_embedding_model,
        request_timeout_s=settings.ollama_request_timeout_s,
    )

    total_fail = 0
    try:
        if scope in ("project", "all"):
            _, fail = await _reindex_project_scope(
                settings, sqlite, ollama, dry_run, log
            )
            total_fail += fail
        if scope in ("global", "all"):
            _, fail = await _reindex_global_scope(
                settings, sqlite, ollama, dry_run, log
            )
            total_fail += fail
    finally:
        await ollama.close()

    if dry_run:
        log.info("Dry-run: nenhum índice foi tocado.")
    return 0 if total_fail == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconstroi índices FAISS.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Só lista o que seria indexado; não apaga nem regenera nada.",
    )
    parser.add_argument(
        "--scope",
        choices=("project", "global", "all"),
        default="project",
        help="Qual índice reconstruir (default: project).",
    )
    args = parser.parse_args()

    rc = asyncio.run(reindex(dry_run=args.dry_run, scope=args.scope))
    sys.exit(rc)


if __name__ == "__main__":
    main()
