# ROADMAP — Bot RDO / ollama_telegram

Bot Telegram com RAG contrastivo sobre Ollama local, evoluindo para sistema
multi-usuário de Relatório Diário de Obra.

> Histórico completo (decisões antigas, DDL inteiro, Fase 4 original):
> `docs/archive/roadmap-historico-2026-04-a-05.md`.

---

## Changelog

- **2026-05-14** — Isolamento de chats por usuário. `interactions.visibilidade`
  + `documents.visibilidade` (default `publica`). `fetch_by_ids` exige
  `requester_user_id` (kwonly). Aplicado no RAG, `/recall #iXX` e snippets.
  Fecha telepatia. Brecha N1/N2 fica como TODO até hierarquia de papéis.
- **2026-05-12** — Refundação do schema. `interactions` vira só log; dados
  de obra em tabelas próprias (atividades, efetivo, clima, expediente,
  materiais, anotacoes). RDO é VIEW. `/doc` classificado com ACL.
- **2026-04-28** — Fase 2 (multi-obra) parcial; Fase 3/4 desenhadas.

---

## Estado atual

### ✅ Funcionando
- RAG contrastivo (FAISS, nomic-embed-text 768d)
- Fallback chain Ollama → OpenAI
- Scoring 1-5 + aprendizado contrastivo
- Intent/tags, tool use, web_search, reminders
- Pipeline auditável (`pipeline_steps` + `run_id`)
- Multi-usuário com `users`/`projects`/`project_members`/`invites`
- Schema novo (Refundação 2026-05) criado, vazio
- Isolamento básico de chats (visibilidade publica/privada)

### 🔄 Em construção
- Comando `/doc` real com ACL (Passo 3 da refundação)
- Handlers gravando em `atividades`/`efetivo`/`clima`/... (Passo 4)
- Hierarquia de papéis N1/N2/N3 mapeada em `users.role`

### ⏳ Não começado
- Cronograma macro (UI/handlers — tabela `cronograma_etapas` existe)
- VW `vw_rdo_dia` (depende de dados reais primeiro)
- Export JSON → HTML/PDF (modelo: `docs/rdo_preview_exemplo.html`)
- Financeiro (Fase 5)

---

## Plano de transição da refundação (8 passos)

| # | Passo | Status |
|---|-------|--------|
| 1 | Aprovar esqueleto | ✅ 2026-05-12 |
| 2 | Criar tabelas novas vazias | ✅ 2026-05-12 |
| 2.5 | Isolamento básico de chats (visibilidade) | ✅ 2026-05-14 |
| 3 | `/doc` + `role_permissions` + ACL no retrieval | ⏭ próximo |
| 4 | Handlers gravam em paralelo nas tabelas novas | |
| 5 | `interactions` para de receber dado de obra | |
| 6 | `vw_rdo_dia` a partir de dias reais | |
| 7 | Chunking semântico (parágrafo/seção) | |
| 8 | Backfill `interaction_telemetry`/`_rag`, `DROP COLUMN` | |

Detalhes de cada passo no histórico.

---

## Decisões de design para lembrar

1. `telegram_id` ≠ `users.id` — buscar por `telegram_id`, FK com `users.id`.
2. UIDs só pra exibição — banco sem `#`, mensagens com `#` em code block.
3. Isolamento por obra é absoluto — middleware checa `project_members`.
4. RAG injeta contexto de obra ativa no embedding prefix.
5. Notas são o campo mais importante — nunca truncar, nunca limitar.
6. Aprovação de RDO tem trilha imutável — `approved_by`, `approved_at`.
7. Visibilidade default = `publica`. Privacidade é opt-in pelo dono.
8. Documento crítico (folha, aditivo, memorial) entra só por `/doc` com ACL.
