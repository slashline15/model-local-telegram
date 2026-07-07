"""Popula a base global de nicho (global_chunks + faiss_global).

Lê arquivos Markdown/texto de um diretório de conhecimento, chunka, embeda
e insere. Roda offline — não depende do bot estar no ar (só do Ollama).

Como rodar:
    python -m scripts.populate_global_base <diretorio> [opções]
    python -m scripts.populate_global_base ./conhecimento --source norma_abnt
    python -m scripts.populate_global_base ./glossario --doc-class glossario --weight 0.9
    python -m scripts.populate_global_base ./conhecimento --dry-run

Só ADICIONA. Para reconstruir o índice do zero a partir da tabela:
    python -m scripts.reindex --scope global
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Permite rodar como `python scripts/populate_global_base.py` direto.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.chunking import chunk_text  # noqa: E402
from core.config import get_settings  # noqa: E402
from core.logger import get_logger, setup_logging  # noqa: E402
from database.faiss_mgr import FaissManager  # noqa: E402
from database.sqlite_mgr import SQLiteManager  # noqa: E402
from llm.ollama_client import OllamaClient  # noqa: E402

_SUFFIXES: frozenset[str] = frozenset({".md", ".txt"})
_EMBED_INPUT_MAX_CHARS: int = 3000


def _titulo_de(path: Path, conteudo: str) -> str:
    """Primeira linha `# Título` do Markdown, senão o nome do arquivo."""
    for line in conteudo.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
        if line:
            break
    return path.stem.replace("_", " ").replace("-", " ")


async def populate(
    directory: Path,
    *,
    source: str,
    doc_class: str,
    weight: float,
    dry_run: bool,
) -> int:
    settings = get_settings()
    setup_logging(level=settings.log_level)
    log = get_logger("populate_global_base")

    files = sorted(
        p for p in directory.rglob("*") if p.suffix.lower() in _SUFFIXES
    )
    if not files:
        log.error("Nenhum .md/.txt encontrado em %s", directory)
        return 1
    log.info("Encontrados %d arquivo(s) em %s", len(files), directory)

    sqlite = SQLiteManager(
        db_path=settings.sqlite_path,
        default_model=settings.ollama_default_model,
    )
    await sqlite.init_schema()

    plan: list[tuple[str, str]] = []  # (titulo, chunk)
    for path in files:
        conteudo = path.read_text(encoding="utf-8", errors="replace").strip()
        if not conteudo:
            log.warning("Vazio, pulando: %s", path.name)
            continue
        titulo = _titulo_de(path, conteudo)
        chunks = chunk_text(conteudo, settings.chunk_size, settings.chunk_overlap)
        log.info("  • %s → %d chunk(s) ('%s')", path.name, len(chunks), titulo)
        plan.extend((titulo, c) for c in chunks)

    if dry_run:
        log.info("Dry-run: %d chunk(s) NÃO gravados.", len(plan))
        return 0

    faiss_global = FaissManager(
        dim=settings.embedding_dim,
        index_path=settings.faiss_global_index_path,
        id_map_path=settings.faiss_global_id_map_path,
    )
    await faiss_global.init()

    ollama = OllamaClient(
        host=settings.ollama_host,
        default_model=settings.ollama_default_model,
        embedding_model=settings.ollama_embedding_model,
        request_timeout_s=settings.ollama_request_timeout_s,
    )

    ok = 0
    fail = 0
    try:
        for titulo, chunk in plan:
            try:
                vec = await ollama.embed(chunk[:_EMBED_INPUT_MAX_CHARS])
                chunk_id = await sqlite.global_chunks.insert(
                    source=source,
                    doc_class=doc_class,
                    titulo=titulo,
                    conteudo=chunk,
                    weight=weight,
                )
                await faiss_global.add(chunk_id, vec)
                ok += 1
                if ok % 10 == 0:
                    log.info("Indexados %d/%d...", ok, len(plan))
            except Exception as exc:  # noqa: BLE001
                fail += 1
                log.warning("Falha ao indexar chunk de '%s': %s", titulo, exc)
    finally:
        await ollama.close()

    log.info(
        "População concluída: %d OK, %d falhas. faiss_global ntotal=%d",
        ok, fail, faiss_global.ntotal,
    )
    return 0 if fail == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Popula a base global de nicho (global_chunks + faiss_global)."
    )
    parser.add_argument("directory", type=Path, help="Diretório com .md/.txt")
    parser.add_argument("--source", default="manual",
                        help="Origem dos chunks (ex.: norma_abnt, glossario).")
    parser.add_argument("--doc-class", default="norma", dest="doc_class",
                        help="Classe dos chunks (default: norma).")
    parser.add_argument("--weight", type=float, default=1.0,
                        help="Peso dos chunks na busca (default: 1.0).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Só lista o plano de chunking; não grava nada.")
    args = parser.parse_args()

    if not args.directory.is_dir():
        print(f"Diretório não existe: {args.directory}", file=sys.stderr)
        sys.exit(2)

    rc = asyncio.run(populate(
        args.directory,
        source=args.source,
        doc_class=args.doc_class,
        weight=args.weight,
        dry_run=args.dry_run,
    ))
    sys.exit(rc)


if __name__ == "__main__":
    main()
