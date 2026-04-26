from __future__ import annotations

import asyncio
import json
from pathlib import Path

import faiss  # type: ignore[import-untyped]
import numpy as np

from core.exceptions import StorageError
from core.logger import get_logger

log = get_logger(__name__)


class FaissManager:
    """Índice FAISS persistido em disco com mapeamento posição→sqlite_id.

    Usa IndexIDMap2 sobre IndexFlatIP (similaridade por produto interno em
    vetores normalizados ⇒ cosseno). Toda I/O pesada roda em executor.
    """

    def __init__(
        self,
        dim: int,
        index_path: Path,
        id_map_path: Path,
    ) -> None:
        self._dim: int = dim
        self._index_path: Path = index_path
        self._id_map_path: Path = id_map_path
        self._index: faiss.IndexIDMap2 | None = None
        self._known_ids: set[int] = set()
        self._lock: asyncio.Lock = asyncio.Lock()

    async def init(self) -> None:
        await asyncio.to_thread(self._load_or_create)

    def _load_or_create(self) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        if self._index_path.exists():
            try:
                self._index = faiss.read_index(str(self._index_path))
                log.info(
                    "FAISS carregado de %s (ntotal=%d)",
                    self._index_path,
                    self._index.ntotal,
                )
            except Exception as exc:  # noqa: BLE001
                raise StorageError(f"Falha ao ler índice FAISS: {exc}") from exc
        else:
            base = faiss.IndexFlatIP(self._dim)
            self._index = faiss.IndexIDMap2(base)
            log.info("Novo índice FAISS criado (dim=%d)", self._dim)

        if self._id_map_path.exists():
            try:
                self._known_ids = set(json.loads(self._id_map_path.read_text("utf-8")))
            except (OSError, json.JSONDecodeError) as exc:
                raise StorageError(f"Falha ao ler id-map FAISS: {exc}") from exc
        else:
            self._known_ids = set()

    async def add(self, sqlite_id: int, vector: np.ndarray) -> None:
        async with self._lock:
            await asyncio.to_thread(self._add_sync, sqlite_id, vector)

    def _add_sync(self, sqlite_id: int, vector: np.ndarray) -> None:
        if self._index is None:
            raise StorageError("FAISS não inicializado.")
        v = self._prepare(vector)
        ids = np.array([sqlite_id], dtype=np.int64)
        self._index.add_with_ids(v, ids)
        self._known_ids.add(int(sqlite_id))
        self._persist()

    async def search(self, vector: np.ndarray, top_k: int) -> list[tuple[int, float]]:
        async with self._lock:
            return await asyncio.to_thread(self._search_sync, vector, top_k)

    def _search_sync(self, vector: np.ndarray, top_k: int) -> list[tuple[int, float]]:
        if self._index is None:
            raise StorageError("FAISS não inicializado.")
        if self._index.ntotal == 0:
            return []
        v = self._prepare(vector)
        k = min(top_k, self._index.ntotal)
        scores, ids = self._index.search(v, k)
        out: list[tuple[int, float]] = []
        for sid, score in zip(ids[0].tolist(), scores[0].tolist(), strict=True):
            if sid == -1:
                continue
            out.append((int(sid), float(score)))
        return out

    def _persist(self) -> None:
        if self._index is None:
            return
        faiss.write_index(self._index, str(self._index_path))
        self._id_map_path.write_text(
            json.dumps(sorted(self._known_ids)), encoding="utf-8"
        )

    def _prepare(self, vector: np.ndarray) -> np.ndarray:
        v = np.asarray(vector, dtype=np.float32)
        if v.ndim == 1:
            v = v.reshape(1, -1)
        if v.shape[1] != self._dim:
            raise StorageError(
                f"Dimensão de vetor incompatível: esperado {self._dim}, recebido {v.shape[1]}."
            )
        faiss.normalize_L2(v)
        return v

    @property
    def ntotal(self) -> int:
        return 0 if self._index is None else int(self._index.ntotal)
