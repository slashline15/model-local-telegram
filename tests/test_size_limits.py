from __future__ import annotations

from pathlib import Path

import pytest

from core.audio_transcriber import WhisperTranscriber
from core.exceptions import TranscriptionError
from tg.handlers import _file_too_big


def test_file_too_big_returns_false_when_size_unknown() -> None:
    big, mb = _file_too_big(None, 20)
    assert big is False
    assert mb == 0.0


def test_file_too_big_at_exact_limit_passes() -> None:
    twenty_mb = 20 * 1024 * 1024
    big, _ = _file_too_big(twenty_mb, 20)
    assert big is False  # estritamente >, então no limite passa


def test_file_too_big_above_limit_rejects() -> None:
    big, mb = _file_too_big(20 * 1024 * 1024 + 1, 20)
    assert big is True
    assert pytest.approx(mb, rel=1e-4) == 20.0


def test_file_too_big_reports_correct_mb_value() -> None:
    big, mb = _file_too_big(15 * 1024 * 1024, 20)
    assert big is False
    assert pytest.approx(mb, rel=1e-4) == 15.0


@pytest.mark.asyncio
async def test_whisper_rejects_oversized_file_without_network(tmp_path: Path) -> None:
    """Garante que o check de tamanho roda ANTES de tentar a chamada HTTP."""
    big_audio = tmp_path / "big.ogg"
    # 26 MB de zeros — Whisper limit é 25.
    big_audio.write_bytes(b"\x00" * (26 * 1024 * 1024))

    t = WhisperTranscriber(api_key="sk-fake", max_size_mb=25)
    with pytest.raises(TranscriptionError) as ei:
        await t.transcribe(big_audio)
    assert "excede o limite do Whisper" in str(ei.value)


@pytest.mark.asyncio
async def test_whisper_rejects_missing_file(tmp_path: Path) -> None:
    t = WhisperTranscriber(api_key="sk-fake")
    with pytest.raises(TranscriptionError):
        await t.transcribe(tmp_path / "nope.ogg")
