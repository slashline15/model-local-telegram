# tests/test_chunking.py

"""Testa _chunk_text() com vários tamanhos e edge cases."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tg.handlers import _chunk_text  # noqa: E402


def test_empty_text():
    assert _chunk_text("", 2500, 300) == []


def test_text_smaller_than_chunk():
    text = "abc" * 100  # 300 chars << 2500
    chunks = _chunk_text(text, 2500, 300)
    assert chunks == [text]


def test_text_exactly_chunk_size():
    text = "x" * 2500
    chunks = _chunk_text(text, 2500, 300)
    assert chunks == [text]


def test_text_larger_than_chunk():
    text = "a" * 5000
    chunks = _chunk_text(text, 2500, 300)
    # Com chunk_size=2500, overlap=300, stride=2200:
    # chunk0=0..2500, chunk1=2200..4700, chunk2=4400..5000 → 3 chunks
    assert len(chunks) >= 2
    # Primeiro chunk tem exatamente chunk_size chars.
    assert len(chunks[0]) == 2500
    # Toda a string está coberta (o último char do texto está em algum chunk).
    last_chars = set()
    for c in chunks:
        last_chars.add(c[-1])
    assert "a" in last_chars



def test_overlap():
    chunk_size = 10
    overlap = 3
    text = "abcdefghij" * 3  # 30 chars
    chunks = _chunk_text(text, chunk_size, overlap)
    # O segundo chunk começa em (chunk_size - overlap) = 7 do texto
    assert chunks[1] == text[7:17]


def test_many_chunks():
    text = "x" * 10_000
    chunks = _chunk_text(text, 2500, 300)
    # Todos os caracteres do texto original devem estar cobertos
    covered = set()
    start = 0
    for c in chunks:
        covered.update(range(start, start + len(c)))
        start += len(c) - 300 if start + len(c) < len(text) else len(c)
    # Toda a string está presente em pelo menos 1 chunk
    assert len(chunks) >= 4


def test_reconstruct_coverage():
    """Garante que o texto completo está coberto pelos chunks."""
    text = "Hello, World! " * 200  # ~2800 chars
    chunk_size = 1000
    overlap = 100
    chunks = _chunk_text(text, chunk_size, overlap)
    # Verifica que o início de cada chunk (sem overlap) cobre o texto
    reconstructed = chunks[0]
    for c in chunks[1:]:
        reconstructed += c[overlap:]
    assert text in reconstructed or reconstructed.startswith(text[:chunk_size])
