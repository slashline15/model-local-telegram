from __future__ import annotations

import pytest

from core.uid import gen_uid


def test_gen_uid_default_length() -> None:
    assert len(gen_uid()) == 8


def test_gen_uid_custom_length() -> None:
    assert len(gen_uid(12)) == 12


def test_gen_uid_alphabet_excludes_ambiguous() -> None:
    forbidden = set("ILOU01")
    sample = "".join(gen_uid(8) for _ in range(200))
    assert not (set(sample) & forbidden)


def test_gen_uid_uses_only_allowed_chars() -> None:
    allowed = set("ABCDEFGHJKMNPQRSTVWXYZ23456789")
    sample = "".join(gen_uid(16) for _ in range(50))
    assert set(sample).issubset(allowed)


def test_gen_uid_rejects_short_length() -> None:
    with pytest.raises(ValueError):
        gen_uid(3)


def test_gen_uid_is_random_enough() -> None:
    """200 chamadas — esperaria 0 colisões (espaço ~6e11)."""
    seen = {gen_uid() for _ in range(200)}
    assert len(seen) == 200
