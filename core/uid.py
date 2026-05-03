# core/uid.py

"""Geração de UIDs legíveis para exibição em mensagens (#AB3X9KF2)."""

from __future__ import annotations

import secrets

# Sem caracteres ambíguos: I, L, O, U, 0, 1.
# 30 chars ⇒ 30^8 ≈ 6,5×10¹¹ combinações, colisão desprezível.
_CHARS: str = "ABCDEFGHJKMNPQRSTVWXYZ23456789"


def gen_uid(length: int = 8) -> str:
    """Gera UID aleatório legível. Armazenar sem `#`; exibir com `#` em code block."""
    if length < 4:
        raise ValueError("length deve ser >= 4 para evitar colisões.")
    return "".join(secrets.choice(_CHARS) for _ in range(length))
