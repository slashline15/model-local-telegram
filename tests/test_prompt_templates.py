from __future__ import annotations

from llm.prompt_templates import (
    FewShotExample,
    render_contrastive_prompt,
    render_neutral_context,
)


def test_contrastive_prompt_contains_anchors() -> None:
    out = render_contrastive_prompt(
        user_message="qual a capital do Brasil?",
        positives=[FewShotExample("ola", "ola!")],
        negatives=[FewShotExample("xpto", "errado")],
    )
    assert "[O QUE FAZER" in out
    assert "[O QUE NÃO FAZER" in out
    assert "[Pergunta Atual]" in out
    assert "qual a capital do Brasil?" in out
    assert "ola" in out
    assert "xpto" in out


def test_contrastive_prompt_handles_empty_lists() -> None:
    out = render_contrastive_prompt("oi", [], [])
    assert "(nenhum exemplo disponível)" in out


def test_neutral_context_renders() -> None:
    out = render_neutral_context(
        "como vai?",
        [FewShotExample("hi", "olá")],
    )
    assert "[Contexto recente" in out
    assert "[Pergunta Atual]" in out
    assert "hi" in out
