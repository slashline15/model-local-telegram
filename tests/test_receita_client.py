from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.receita_client import _is_fresh, _parse_rf_payload, lookup_cnpj


def test_parse_rf_payload_nested() -> None:
    data = {
        "razao_social": "Construtora Alfa LTDA",
        "estabelecimento": {
            "nome_fantasia": "Alfa",
            "situacao_cadastral": "Ativa",
            "atividade_principal": {"descricao": "Construção de edifícios"},
        },
    }
    campos = _parse_rf_payload(data)
    assert campos["razao_social"] == "Construtora Alfa LTDA"
    assert campos["nome_fantasia"] == "Alfa"
    assert campos["situacao_rf"] == "Ativa"
    assert campos["atividade"] == "Construção de edifícios"


def test_parse_rf_payload_flat_fallback() -> None:
    data = {
        "razao_social": "Beta ME",
        "nome_fantasia": "Beta",
        "situacao_cadastral": "Baixada",
        "cnae_fiscal_descricao": "Comércio de materiais",
    }
    campos = _parse_rf_payload(data)
    assert campos["nome_fantasia"] == "Beta"
    assert campos["situacao_rf"] == "Baixada"
    assert campos["atividade"] == "Comércio de materiais"


def test_is_fresh() -> None:
    assert _is_fresh(None) is False
    assert _is_fresh("data-invalida") is False

    agora = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
    assert _is_fresh(agora) is True

    velho = (
        datetime.now(tz=timezone.utc) - timedelta(days=40)
    ).isoformat(timespec="seconds")
    assert _is_fresh(velho) is False


@pytest.mark.asyncio
async def test_lookup_cnpj_invalid_returns_none_without_network() -> None:
    # CNPJ com tamanho errado é rejeitado antes de qualquer requisição.
    assert await lookup_cnpj("123") is None
    assert await lookup_cnpj("") is None
    assert await lookup_cnpj("00.000.000/0000") is None
