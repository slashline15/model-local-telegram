# CLAUDE.md — guia rápido pro próximo agente

Bot Telegram com RAG contrastivo (Ollama local, fallback OpenAI) que está
virando sistema multi-usuário de Relatório Diário de Obra. Schema em
refundação ativa (Fase 4, plano em 8 passos).

## Onde olhar primeiro

- `ROADMAP.md` — estado atual, próximos passos, decisões de design.
- `docs/SESSION_NOTES.md` — armadilhas resolvidas (event loop, migrations,
  RAG, tool calling). Lê antes de mexer em pipeline ou DB.
- `database/schema.py` — esqueleto consolidado pós-refundação 2026-05.
- `docs/archive/` — histórico (transcripts, planos superados, DDL antigo).
  Não leia sem necessidade.

## Pastas

- `tg/` — Telegram (handlers, bot, callbacks). `handlers._process_user_input`
  é o pipeline principal.
- `llm/` — Ollama client, `contrastive_rag.py` (montagem do prompt),
  `prompt_templates.py`.
- `database/` — schema, `sqlite_mgr.py` (fachada), `repos/` (CRUD por tabela),
  `faiss_mgr.py`.
- `core/` — utilidades (logger, pipeline recorder, codes, exceptions).
- `scripts/` — `reindex.py`, `bootstrap_check.py`.
- `tests/` — pytest, 115+ testes. `FakeOllama` em `conftest.py` evita rede.

## Comandos úteis

```bash
pytest -x --tb=short              # roda toda a suite
python -m scripts.reindex --dry-run  # auditoria do FAISS
python -m scripts.reindex         # apaga FAISS, regenera
python main.py                    # bot real (precisa Ollama + token)
```

## Convenções

- Comentários só pro "why" não-óbvio.
- `from __future__ import annotations` em todo módulo.
- `tg/` (não `telegram/`).
- PT-BR no produto e nos commits; código em inglês.
- Testes não tocam rede ou LLM real.

## Plano em curso

Fase 4 (refundação 2026-05). Próximo passo: comando `/doc` real com ACL.
Status detalhado em `ROADMAP.md` na tabela "Plano de transição".
