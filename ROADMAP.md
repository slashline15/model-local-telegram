# ROADMAP — Bot RDO / ollama_telegram

Bot Telegram com RAG contrastivo sobre Ollama local, evoluindo para sistema
multi-usuário de Relatório Diário de Obra.

> Histórico completo (decisões antigas, DDL inteiro, Fase 4 original):
> `docs/archive/roadmap-historico-2026-04-a-05.md`.

---

## Changelog

- **2026-07-07** — Dual RAG + ACL simplificado + fornecedores globais + `/doc`.
  Implementa o plano `docs/plano-dual-rag-acl-fornecedores.md`:
  - **Dual RAG**: `faiss_global.index` (base de nicho) buscado em paralelo ao
    índice local; peso por obra em `projects.global_rag_weight` (default 0.5,
    0 desliga). Referências entram no prompt como bloco
    `[Referências técnicas]`; visíveis no `/recall`. População offline via
    `python -m scripts.populate_global_base <dir>`.
  - **ACL simplificado**: `fetch_by_ids` não filtra mais `visibilidade` —
    membro da obra lê tudo que foi indexado nela. Isolamento por
    `project_id` intacto. Segurança = decisão consciente de indexar.
  - **`/doc <classe> [título]`** (reply a arquivo ou texto): classificação com
    ACL por nível (`nivel_min_classificar` × papel na obra N1/N2/N3), classes
    sensíveis (folha_pgto, planilha_orcamento, contrato, proposta) pedem
    confirmação inline antes de indexar. Grava em `documents` + chunks com
    peso da classe + interação de log.
  - **Fornecedores globais**: tabela `fornecedores` (CNPJ único) + lookup
    Receita Federal (publica.cnpj.ws, cache 30d) no `/empresa add` com CNPJ;
    `empresas.fornecedor_id` faz o vínculo. Falha de rede não bloqueia cadastro.
  - **Fix**: `scripts/reindex.py` indexava `interaction_id` num índice que o
    RAG lê como `chunk_id` (quebrado desde o refactor de chunking). Agora
    reconstrói de `interaction_chunks` + re-chunka interações órfãs; ganhou
    `--scope project|global|all`.
  - **Desvio consciente do plano**: índices FAISS separados por obra
    (`faiss_{project_id}.index`) ficaram adiados — o filtro por `project_id`
    no SQLite já garante o isolamento e a separação física não muda
    comportamento visível. Fica pra quando houver volume real.
- **2026-05-14 (2)** — Bug-fix RAG por obra + handlers de cadastro do diário.
  RAG agora propaga `project_id` ativo: `fetch_by_ids` e `list_user_history`
  filtram pela obra (fecha confusão de contexto entre obras do mesmo dono).
  Novos comandos em `tg/handlers_obra.py`: `/clima`, `/efetivo`, `/atividade`,
  `/anotacao` (+ listagens e `/rdo [data]`). 4 repos novos
  (clima/efetivo/atividades/anotacoes) e modelos correspondentes. Etapa do
  cronograma fica opcional no MVP (`etapa_id` NULL). Cadastro inicial será
  via Google Forms; revisão de atividades órfãs vem depois.
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
- Dual RAG: base global de nicho + base da obra (peso por projeto)
- Fallback chain Ollama → OpenAI
- Scoring 1-5 + aprendizado contrastivo
- Intent/tags, tool use, web_search, reminders
- Pipeline auditável (`pipeline_steps` + `run_id`)
- Multi-usuário com `users`/`projects`/`project_members`/`invites`
- Schema novo (Refundação 2026-05) criado, vazio
- `/doc` com ACL de classificação + confirmação pra classes sensíveis
- ACL simplificado: membro da obra lê tudo indexado nela (2026-06)
- Fornecedores globais com lookup Receita Federal no `/empresa add`

### 🔄 Em construção
- Popular a base global (rodar `populate_global_base` com conteúdo real)
- Classificação de intent de obra (texto livre → registro estruturado)

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
| 3 | `/doc` + ACL simplificado no retrieval | ✅ 2026-07-07 |
| 4 | Handlers de cadastro (`/clima /efetivo /atividade /anotacao /rdo`) | ✅ 2026-05-14 |
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
