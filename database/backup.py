# database/backup.py

"""
Backup atômico do SQLite com rotação.

- Valida `PRAGMA integrity_check` antes de qualquer cópia: se o DB corrente
  estiver corrompido, NÃO sobrescreve nem rotaciona — preserva os bons.
- Usa `sqlite3.Connection.backup()` (API oficial), seguro mesmo com o DB
  em uso por outras conexões.
- Mantém os últimos `max_keep` arquivos `bot-YYYYMMDD-HHMMSS.db` em `backup_dir`.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from core.logger import get_logger

log = get_logger(__name__)


def create_backup(
    db_path: Path,
    backup_dir: Path,
    *,
    max_keep: int = 10,
) -> Path | None:
    """Faz backup do `db_path` em `backup_dir` e rotaciona.

    Retorna o `Path` do backup criado, ou `None` se nada foi feito
    (DB inexistente, vazio ou corrompido).
    """
    if not db_path.exists() or db_path.stat().st_size == 0:
        log.info("Backup: DB %s não existe ou está vazio — pulando.", db_path)
        return None

    if not _is_healthy(db_path):
        log.warning(
            "Backup: DB %s falhou integrity_check — preservando backups antigos, "
            "NÃO criando novo nem rotacionando.",
            db_path,
        )
        return None

    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"bot-{ts}.db"

    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    removed = _rotate(backup_dir, max_keep=max_keep)
    log.info(
        "Backup criado: %s (mantendo últimos %d, removidos %d).",
        target.name, max_keep, removed,
    )
    return target


def _is_healthy(db_path: Path) -> bool:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute("PRAGMA integrity_check;").fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        log.warning("Backup: DB %s não abre (%s).", db_path, exc)
        return False
    return bool(row) and row[0] == "ok"


def _rotate(backup_dir: Path, *, max_keep: int) -> int:
    """Remove backups mais antigos, mantendo apenas os `max_keep` mais recentes."""
    if max_keep < 1:
        raise ValueError("max_keep deve ser >= 1")
    backups = sorted(backup_dir.glob("bot-*.db"))
    excess = backups[:-max_keep] if len(backups) > max_keep else []
    for old in excess:
        old.unlink()
    return len(excess)
