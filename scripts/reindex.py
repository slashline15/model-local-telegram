"""Reconstroi o índice FAISS a partir do SQLite.

Quando usar:
- Você fez ajustes em embedding_model, embedding_dim, ou no formato do texto
  embedado, e precisa que tudo no FAISS bata com o estado atual.
- Tem entradas órfãs no SQLite que falharam ao indexar (ex.: input estourou
  o context do modelo de embedding) e quer recuperá-las.

Como rodar:
    python -m scripts.reindex
    python -m scripts.reindex --dry-run    # só lista, não escreve

O índice antigo é apagado do disco antes da reconstrução. As interações
no SQLite NÃO são tocadas.
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

from core.config import get_settings  # noqa: E402
from core.logger import get_logger, setup_logging  # noqa: E402
from database.faiss_mgr import FaissManager  # noqa: E402
from database.sqlite_mgr import SQLiteManager  # noqa: E402
from llm.ollama_client import OllamaClient  # noqa: E402

# Mesmo limite usado no pipeline (tg/handlers.py::_EMBED_INPUT_MAX_CHARS).
_EMBED_INPUT_MAX_CHARS: int = 3000


async def _fetch_all_pairs(db_path: Path) -> list[tuple[int, str, str]]:
    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute(
            "SELECT id, user_message, bot_response FROM interactions ORDER BY id"
        ) as cur:
            rows = await cur.fetchall()
    return [(int(r[0]), str(r[1] or ""), str(r[2] or "")) for r in rows]


async def reindex(dry_run: bool) -> int:
    settings = get_settings()
    setup_logging(level=settings.log_level)
    log = get_logger("reindex")

    sqlite = SQLiteManager(
        db_path=settings.sqlite_path,
        default_model=settings.ollama_default_model,
    )
    await sqlite.init_schema()

    pairs = await _fetch_all_pairs(settings.sqlite_path)
    log.info("Encontradas %d interações no SQLite.", len(pairs))
    if not pairs:
        log.info("Nada a fazer.")
        return 0

    if dry_run:
        for iid, u, _ in pairs:
            preview = (u or "").replace("\n", " ")[:80]
            log.info("  • id=%d  user=%r", iid, preview)
        log.info("Dry-run: índice FAISS NÃO foi tocado.")
        return 0

    # Reset físico do índice antigo.
    for p in (settings.faiss_index_path, settings.faiss_id_map_path):
        if p.exists():
            p.unlink()
            log.info("Removido %s", p)

    faiss = FaissManager(
        dim=settings.embedding_dim,
        index_path=settings.faiss_index_path,
        id_map_path=settings.faiss_id_map_path,
    )
    await faiss.init()

    ollama = OllamaClient(
        host=settings.ollama_host,
        default_model=settings.ollama_default_model,
        embedding_model=settings.ollama_embedding_model,
        request_timeout_s=settings.ollama_request_timeout_s,
    )

    ok = 0
    fail = 0
    try:
        for iid, user_msg, bot_resp in pairs:
            embed_text = f"USER: {user_msg}\nBOT: {bot_resp}"[:_EMBED_INPUT_MAX_CHARS]
            try:
                vec = await ollama.embed(embed_text)
                await faiss.add(iid, vec)
                ok += 1
                if ok % 10 == 0:
                    log.info("Indexadas %d/%d...", ok, len(pairs))
            except Exception as exc:  # noqa: BLE001
                fail += 1
                log.warning("Falha ao indexar id=%d: %s", iid, exc)
    finally:
        await ollama.close()

    log.info("Reindex concluído: %d OK, %d falhas. FAISS ntotal=%d",
             ok, fail, faiss.ntotal)
    return 0 if fail == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconstroi o índice FAISS.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Só lista as interações; não apaga nem regenera o índice.",
    )
    args = parser.parse_args()

    rc = asyncio.run(reindex(dry_run=args.dry_run))
    sys.exit(rc)


if __name__ == "__main__":
    main()
