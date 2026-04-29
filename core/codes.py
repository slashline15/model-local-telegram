from __future__ import annotations

import re

_CODE_PREFIX: str = "i"
_CODE_RE = re.compile(r"#?i(\d+)", re.IGNORECASE)


def format_code(interaction_id: int) -> str:
    """Formata o id de uma interação como código curto: 42 → 'i42'."""
    return f"{_CODE_PREFIX}{interaction_id}"


def format_hashtag(interaction_id: int) -> str:
    """Versão clicável no Telegram: 42 → '#i42'."""
    return f"#{format_code(interaction_id)}"


def parse_code(text: str) -> int | None:
    """Extrai o id de uma string como '#i42', 'i42' ou apenas '42'.

    Retorna None se não conseguir interpretar.
    """
    s = text.strip()
    if not s:
        return None
    m = _CODE_RE.fullmatch(s)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    if s.isdigit():
        try:
            return int(s)
        except ValueError:
            return None
    return None
