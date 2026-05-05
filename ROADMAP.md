# ROADMAP — Bot RDO / ollama_telegram

Bot Telegram com RAG contrastivo sobre Ollama local, evoluindo para um
sistema multi-usuário de **gestão de obras** com geração de RDO (Relatório
Diário de Obra) como projeção sobre dados relacionais.

> **Princípio fundamental:** O RDO **não é uma tabela**. É uma projeção
> sobre eventos do dia, formatada (PDF/HTML/template) a partir de tabelas
> de domínio (atividades, efetivo, clima, materiais, ocorrências, visitas).
> A IA atua **na entrada** — entendendo linguagem informal de obra,
> categorizando, deduplicando, sugerindo correções. **A renderização do
> RDO é determinística** (consulta + formatação), nunca chama LLM.

---

## Estado atual (sessão 2026-05-05)

### Núcleo de IA ✅
- RAG contrastivo com FAISS (768 dims, `nomic-embed-text:v1.5`)
- Fallback chain: Ollama primário (`gemma4:31b-cloud`) → fallbacks Ollama
  (`llama3.2:3b`) → OpenAI (`gpt-4o-mini`)
- Scoring 1-5 por interação, aprendizado contrastivo automático
- Intent classifier, tag generator, tool registry
- Tools: `web_search`, `reminders` (com rehidratação no boot via
  `reminders.reload_pending()` em `tg/bot.py:139`)
- Pipeline auditável com `pipeline_steps` + `run_id`
- Transcrição de voz (Whisper) opcional
- Backup SQLite automático (até 10 cópias por padrão)

### Identidade e acesso ✅
- `users` com papéis globais (superadmin / admin / engineer / supervisor /
  worker / client / etc.) e status (active / inactive / banned)
- `projects` com `admin_id` (único admin por obra; criação atômica
  já cadastra o admin como membro com permissões totais)
- `invites` uso único (deep link `t.me/BOT?start=<token>`)
- `project_members` com flags `can_approve_rdo`, `can_view_financial`,
  `can_invite`
- Middleware bloqueia não-cadastrado de cara, com mensagem pra pedir
  convite
- `user_settings.current_project_id` — obra ativa do usuário, persistida

### Cadastros base ✅
- `funcoes` — catálogo **global** (mesma "Pedreiro" vale em qualquer
  canteiro), 15 funções seedadas idempotentemente do desenho original
  (Engenheiro, Estagiário, Auxiliar, Apontador, Mestre de obras,
  Encarregado, Gestor, Técnico de Segurança, Eletricista, Almoxarife,
  Pedreiro, Carpinteiro, Servente, Betoneiro, Motorista)
- `empresas` — vinculadas a obra, tipo `own` (própria) ou `third_party`
  (terceira). Mesma empresa em N obras = N linhas (por design)
- `colaboradores` — vinculados a `empresa_id` (obrigatório) e `funcao_id`
  (opcional). Cadastro individual normalmente é da empresa própria;
  terceiros entram como contagem por empresa no efetivo do dia

### Tabelas no DB hoje
`interactions`, `user_settings`, `pipeline_steps`, `reminders`, `users`,
`projects`, `invites`, `project_members`, `funcoes`, `empresas`,
`colaboradores`

### Testes
**91/91 verdes.** Cobertura:
- Permissões (RBAC) — 12 testes (`test_permissions.py`)
- Convites uso único + transferência de admin (`test_invite_flow.py`)
- Membership e isolamento por obra (`test_projects_invites.py`)
- Cadastros (funções, empresas, colaboradores) com isolamento
  (`test_rdo_repos.py`)
- RAG contrastivo, intent classifier, tag generator, tool registry
- SQLite, FAISS, backup, prompt templates, UID

---

## Próxima fase — Cronograma Macro 🎯

**Objetivo:** dar visão ampla, previsibilidade, e ancorar atividades reais
(do dia-a-dia, registradas via chat) em etapas/atividades planejadas.

Sem cronograma, registros do dia ficam soltos. Com cronograma, cada
atividade real cita uma "atividade macro", e o sistema mede previsto vs.
realizado por etapa.

### Tabelas a criar

```sql
-- ETAPA MACRO da obra (Mobilização, Terraplanagem, Estrutura, ...)
CREATE TABLE schedule_phases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    uid             TEXT    UNIQUE NOT NULL,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    phase_number    INTEGER NOT NULL,        -- ordem de exibição (1, 2, 3...)
    name            TEXT    NOT NULL,
    discipline      TEXT,                    -- civil | eletrica | hidraulica | seguranca
    location        TEXT,                    -- bloco/andar/setor (texto livre)
    planned_start   TEXT    NOT NULL,        -- ISO date
    planned_end     TEXT    NOT NULL,
    actual_start    TEXT,                    -- preenchido na 1ª atividade real ligada
    actual_end      TEXT,                    -- preenchido quando progress_pct = 100
    progress_pct    REAL    NOT NULL DEFAULT 0.0,  -- agregado das atividades macro
    status          TEXT    NOT NULL DEFAULT 'pending',
    -- pending | active | done | delayed | suspended
    notes           TEXT,                    -- campo crítico — nunca truncar
    created_by      INTEGER NOT NULL REFERENCES users(id),
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

-- ATIVIDADE MACRO de uma etapa (ex: "Concretagem do pavimento 2",
-- "Montagem de fôrmas pilares P1-P12"). Item de planejamento — quantidades
-- esperadas, datas previstas. Atividades reais do dia (próxima fase) vão
-- referenciar este id.
CREATE TABLE phase_activities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    uid             TEXT    UNIQUE NOT NULL,
    phase_id        INTEGER NOT NULL REFERENCES schedule_phases(id),
    name            TEXT    NOT NULL,
    unit            TEXT,                    -- m², m³, un, vb, kg
    quantity_total  REAL,                    -- meta planejada
    quantity_done   REAL    NOT NULL DEFAULT 0.0,  -- somatório do realizado
    planned_start   TEXT,
    planned_end     TEXT,
    actual_start    TEXT,
    actual_end      TEXT,
    notes           TEXT,
    created_by      INTEGER NOT NULL REFERENCES users(id),
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);
```

### Comandos do bot (formulário guiado)

- `/cronograma` — renderiza Gantt ASCII da obra ativa
- `/etapa add` — bot pergunta nome, datas, disciplina, local em sequência
- `/etapa update <UID> <campo> <valor>` — atualização pontual
- `/atividade add <ETAPA_UID>` — formulário pra atividade macro
- `/atividade progresso <UID> <quantidade>` — adiciona ao realizado;
  recalcula `progress_pct` da etapa pai

### Renderização (ASCII em monospace, fora de prompt LLM)

```
📅 CRONOGRAMA MACRO
Obra: Reforma da Igreja Messiânica  #GH72MX91
Hoje: 05/05/2026

E1 ┤████████░░░         Mobilização         ✅ 100%
E2 ┤    ████████▒▒▒▒░   Terraplanagem       🔄  42%
E3 ┤             ░░░░░░ Estrutura            ⏳   0%
E4 ┤               ░░░░ Cobertura            ⏳   0%
   ┼────┬────┬────┬────┬────
        Jan  Fev  Mar  Abr  Mai
```

Legenda: `█` executado · `▒` em andamento · `░` planejado

### Regras de agregação
- `phase.progress_pct` = média ponderada de `phase_activities.progress`
  (cada atividade pondera por `quantity_total` se preenchido, senão peso igual)
- `phase.actual_start` = mínimo de `phase_activities.actual_start` (não NULL)
- `phase.actual_end` = preenchido quando todas as atividades têm `actual_end`
- `phase.status` = `delayed` quando `today > planned_end` e `progress_pct < 100`

### Testes mínimos (nova suite)
- Criar etapa, criar atividades macro, vincular
- Atualizar progresso de atividade → propaga pra etapa
- Marcar status `delayed` quando passar do `planned_end`
- Isolamento: etapa de obra A não aparece em queries de obra B
- Render Gantt ASCII (snapshot teste de string)

---

## Fases seguintes (alta-órbita, sem detalhe ainda)

### Atividades reais do dia
Tabelas de eventos por domínio, todos com `project_id` e timestamp:
- `activity_logs` — trabalho do dia, vinculado a `phase_activities.id`
- `weather_periods` — clima por período (manhã/tarde/noite), categorizado
  como `seco_produtivo / seco_improdutivo / chuva_produtiva / chuva_improdutiva / sem_expediente`
- `workforce_entries` — efetivo do dia: cabeças por colaborador (próprio)
  ou contagem por empresa (terceiros)
- `material_movements` — entrada/saída de materiais
- `incidents` — paralisações, ocorrências, acidentes
- `visits` — visitas de cliente/terceiros
- `media_attachments` — fotos/áudios vinculados a qualquer evento acima

### Auditoria
- `audit_log` universal com `table_name`, `record_id`, `actor`,
  `before_json`, `after_json`, `timestamp`
- Apenas nível 1 visualiza; níveis 1 e 2 podem alterar registros existentes

### Fechamento de dia
- `day_signatures` — `project_id`, `report_date`, `signed_by`, `signed_at`,
  `pdf_path`, `snapshot_hash`
- Snapshot congela o estado no momento da assinatura. Alterações
  posteriores aparecem só no audit log; PDF assinado é imutável.

### Geração do RDO
- Renderizadores por template (HTML, PDF, formato online)
- Determinístico: query + format. **Sem chamada LLM nessa etapa.**
- Soma efetivo local + soma terceirizados = efetivo real
- Gráfico pluviométrico mensal a partir de `weather_periods`
- Lista de atividades agrupadas por etapa, com previsto vs. realizado

### Financeiro (último)
- `budget_items` (orçamento por etapa/atividade)
- `expenses` (gastos reais)
- `measurements` (medições físico-financeiras)
- Relatório de desvio planejado vs. executado

---

## Backlog técnico

- **Indexação de arquivos grandes** — hoje o bot avisa pedindo pra
  reduzir; falta chunking/embedding incremental. Prioridade baixa
  (não bloqueia nada). [final da fila]
- **Teste automatizado de rehidratação de reminders** — funcionalidade
  existe (`reload_pending` no boot) mas sem teste; adicionar.
- **Migrar dados de teste** quando começar a entrar dado real, decidir
  entre `ALTER TABLE` ou recriação do DB.

---

## Decisões de design para lembrar

1. **`telegram_id` ≠ `users.id`** — sempre buscar por `telegram_id`,
   armazenar FK com `users.id`
2. **UIDs são só pra exibição** — banco armazena sem `#`, mensagens
   exibem com `#` em code block (evita virar hashtag indexada)
3. **Isolamento por obra é absoluto** — middleware checa
   `project_members` antes de qualquer leitura de dados de obra
4. **RAG injeta contexto de obra** — quando o usuário tem
   `current_project_id`, o embedding prefix inclui nome da obra e
   categoria
5. **Notas são o campo mais importante** — nunca truncar, nunca
   limitar. É o que salva construtoras em disputas
6. **RDO não é tabela** — é projeção sobre eventos. Renderização é
   determinística e imutável após assinatura
7. **Tabelas específicas por domínio** — não tabela `events` genérica.
   Integridade relacional importa pra documento legal
8. **Audit trail universal** — toda alteração rastreada. Nível 1 vê;
   1 e 2 podem alterar
9. **Catálogo global de funções** — "Pedreiro" não é por obra
10. **Empresas vinculadas à obra** — mesma empresa em N obras = N
    linhas (facilita permissões e cadastros independentes)
11. **AI atua na entrada, não na saída** — interpreta linguagem informal,
    categoriza, sugere; renderização do RDO nunca chama LLM
12. **Projeto principal valida tese central** — uma única IA com RAG
    contrastivo resolve o que multiagentes + cálculos complexos travavam
