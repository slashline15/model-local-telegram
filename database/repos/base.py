# database/repos/base.py

from __future__ import annotations

from pathlib import Path


class BaseRepo:
    """Base mínima — guarda o caminho do banco. Cada método abre/fecha conexão."""

    __slots__ = ("_db_path",)

    def __init__(self, db_path: Path) -> None:
        self._db_path: Path = db_path
