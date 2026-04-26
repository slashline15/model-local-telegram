from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.faiss_mgr import FaissManager  # noqa: E402
from database.sqlite_mgr import SQLiteManager  # noqa: E402


@pytest_asyncio.fixture
async def sqlite_mgr(tmp_path: Path) -> SQLiteManager:
    mgr = SQLiteManager(db_path=tmp_path / "test.db", default_model="test:1b")
    await mgr.init_schema()
    return mgr


@pytest_asyncio.fixture
async def faiss_mgr(tmp_path: Path) -> FaissManager:
    mgr = FaissManager(
        dim=8,
        index_path=tmp_path / "faiss.index",
        id_map_path=tmp_path / "faiss.json",
    )
    await mgr.init()
    return mgr


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(seed=42)


class FakeOllama:
    """Stand-in para OllamaClient — controla saídas de chat e embed."""

    def __init__(
        self,
        chat_responses: list[str] | None = None,
        embed_dim: int = 8,
    ) -> None:
        self._chat_queue: list[str] = list(chat_responses or [])
        self._embed_dim: int = embed_dim
        self.chat_calls: list[dict[str, Any]] = []
        self.embed_calls: list[str] = []
        self.embedding_model: str = "fake-embed"

    async def chat(
        self,
        messages: list[Any],
        model: str | None = None,
        temperature: float = 0.7,
        tools: list[Any] | None = None,
        format_json: bool = False,
    ) -> Any:
        from llm.ollama_client import ChatResult
        self.chat_calls.append(
            {"model": model, "temperature": temperature, "format_json": format_json}
        )
        content = self._chat_queue.pop(0) if self._chat_queue else ""
        return ChatResult(
            content=content,
            tool_calls=[],
            raw={},
            prompt_tokens=10,
            response_tokens=20,
            total_duration_ms=42,
            model=model or "fake",
        )

    async def embed(self, text: str, model: str | None = None) -> np.ndarray:
        self.embed_calls.append(text)
        # determinístico em função do hash, mas estável (sem rede).
        rng = np.random.default_rng(seed=abs(hash(text)) % (2**32))
        v = rng.standard_normal(self._embed_dim).astype(np.float32)
        n = float(np.linalg.norm(v))
        return v / n if n else v

    async def list_models(self) -> list[str]:
        return ["test:1b", "fake-embed"]
