# core/chunking.py

"""
Chunking semântico compartilhado — pipeline do bot e scripts offline.

Respeita quebras de parágrafo; parágrafo maior que chunk_size cai no
sliding-window com overlap.
"""

from __future__ import annotations

import re


def chunk_text_fixed(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Sliding-window fallback para parágrafos maiores que chunk_size."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start += chunk_size - overlap
    return chunks


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Chunking semântico: respeita parágrafos. Fallback para sliding-window."""
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    paragraphs = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]
    if not paragraphs:
        return chunk_text_fixed(text, chunk_size, overlap)
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= chunk_size:
            current = (current + "\n\n" + para).strip()
        else:
            if current:
                chunks.append(current)
            if len(para) > chunk_size:
                chunks.extend(chunk_text_fixed(para, chunk_size, overlap))
                current = ""
            else:
                current = para
    if current:
        chunks.append(current)
    return chunks or [text]
