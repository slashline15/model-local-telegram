from __future__ import annotations

import sqlite3
from pathlib import Path

from database.backup import create_backup


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES ('hello')")
    conn.commit()
    conn.close()


def test_skips_when_db_missing(tmp_path: Path) -> None:
    out = create_backup(tmp_path / "nope.db", tmp_path / "backups")
    assert out is None
    assert not (tmp_path / "backups").exists()


def test_skips_when_db_empty(tmp_path: Path) -> None:
    db = tmp_path / "empty.db"
    db.touch()
    out = create_backup(db, tmp_path / "backups")
    assert out is None


def test_creates_backup_for_healthy_db(tmp_path: Path) -> None:
    db = tmp_path / "bot.db"
    backup_dir = tmp_path / "backups"
    _make_db(db)

    out = create_backup(db, backup_dir)
    assert out is not None and out.exists()
    assert out.name.startswith("bot-") and out.suffix == ".db"

    conn = sqlite3.connect(out)
    rows = list(conn.execute("SELECT v FROM t"))
    conn.close()
    assert rows == [("hello",)]


def test_skips_corrupted_db_without_touching_existing_backups(tmp_path: Path) -> None:
    db = tmp_path / "bot.db"
    backup_dir = tmp_path / "backups"

    _make_db(db)
    good = create_backup(db, backup_dir)
    assert good is not None

    # Corrompe o DB de origem (sobrescreve com lixo).
    db.write_bytes(b"not a sqlite database at all")

    out = create_backup(db, backup_dir)
    assert out is None  # backup foi recusado
    # Backup bom anterior continua intacto.
    assert good.exists()


def test_rotates_keeping_only_max_keep(tmp_path: Path) -> None:
    db = tmp_path / "bot.db"
    backup_dir = tmp_path / "backups"
    _make_db(db)

    # Cria 5 backups com sufixos forçadamente distintos (timestamps colidiriam).
    backup_dir.mkdir()
    fakes = []
    for i in range(5):
        f = backup_dir / f"bot-2026010{i}-000000.db"
        f.write_bytes(b"")
        fakes.append(f)

    out = create_backup(db, backup_dir, max_keep=3)
    assert out is not None

    remaining = sorted(backup_dir.glob("bot-*.db"))
    assert len(remaining) == 3
    # O mais recente sempre sobrevive.
    assert out in remaining
    # Os mais antigos foram removidos.
    assert not fakes[0].exists()
    assert not fakes[1].exists()
