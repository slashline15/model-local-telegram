# ROADMAP — Bot RDO / ollama_telegram

Contexto: bot Telegram com RAG contrastivo sobre Ollama local.
O núcleo de IA (RAG, embeddings, contrastive learning) está funcional e estável.
O objetivo é evoluir para um sistema multi-usuário de RDO (Relatório Diário de Obra).

---

## Estado atual (sessão 2026-04-28)

### Infraestrutura de IA ✅
- RAG contrastivo com FAISS (768 dims, nomic-embed-text)
- Fallback chain: Ollama primário → Ollama fallbacks → OpenAI
- Scoring 1-5 por interação, aprendizado contrastivo automático
- Intent classifier, tag generator, tool use (web_search, reminders)
- Pipeline auditável com `pipeline_steps` + `run_id`

### Banco de dados atual
Tabelas: `interactions`, `user_settings`, `reminders`, `pipeline_steps`, `users` (adicionada hoje)

### Correções feitas nessa sessão
1. `OLLAMA_HOST=http://[::1]:11434` no ambiente Windows → corrigido para `127.0.0.1`
2. `sys == 'win32'` (bug) → `sys.platform == 'win32'`
3. Event loop Windows com PTB 21.x → `loop.run_until_complete()` + `run_polling()`
4. Truncagem de embedding na query RAG (espelhando `_EMBED_INPUT_MAX_CHARS = 3000`)

---

## Fase 2 — Obras e Controle de Acesso (próxima sessão)

### Tabelas a criar

```sql
-- Convites de uso único (deep link /start?token=...)
CREATE TABLE invites (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    uid         TEXT UNIQUE NOT NULL,   -- ex: AB3X9KF2 (exibido como #AB3X9KF2)
    token       TEXT UNIQUE NOT NULL,   -- UUID interno para o deep link
    project_id  INTEGER REFERENCES projects(id),  -- NULL = convite de plataforma
    role        TEXT NOT NULL,
    created_by  INTEGER NOT NULL REFERENCES users(id),
    used_by     INTEGER REFERENCES users(id),
    expires_at  TEXT,
    used_at     TEXT,
    created_at  TEXT NOT NULL
);

-- Obras
CREATE TABLE projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    uid         TEXT UNIQUE NOT NULL,   -- ex: GH72MX91 (exibido como #GH72MX91)
    name        TEXT NOT NULL,
    address     TEXT,
    type        TEXT,   -- residencial | comercial | infraestrutura | reforma
    status      TEXT NOT NULL DEFAULT 'active',
    start_date  TEXT,
    end_date    TEXT,
    created_by  INTEGER NOT NULL REFERENCES users(id),
    created_at  TEXT NOT NULL
);

-- Membros por obra (acesso estritamente isolado)
CREATE TABLE project_members (
    project_id          INTEGER NOT NULL REFERENCES projects(id),
    user_id             INTEGER NOT NULL REFERENCES users(id),
    role                TEXT NOT NULL,
    can_approve_rdo     INTEGER NOT NULL DEFAULT 0,
    can_view_financial  INTEGER NOT NULL DEFAULT 0,
    can_invite          INTEGER NOT NULL DEFAULT 0,
    joined_at           TEXT NOT NULL,
    invite_id           INTEGER REFERENCES invites(id),
    PRIMARY KEY (project_id, user_id)
);
```

Migration em `interactions`:
```sql
ALTER TABLE interactions ADD COLUMN project_id INTEGER REFERENCES projects(id);
```

### Utilitário de UID (`core/uid.py`)
```python
import secrets

_CHARS = "ABCDEFGHJKMNPQRSTVWXYZ23456789"  # sem ambiguidade: I, L, O, U, 0, 1

def gen_uid(length: int = 8) -> str:
    """Gera UID legível para exibição em mensagens (#AB3X9KF2)."""
    return "".join(secrets.choice(_CHARS) for _ in range(length))
```

8 chars → 30^8 ≈ 656 bilhões de combinações.
Exibição: `#` + uid (sem armazenar o `#` no banco).
Usar em code blocks Telegram para evitar indexação como hashtag.

### Fluxo de convite
1. Admin executa `/invite @papel` → bot gera `token` (UUID) + `uid` (AB3X9KF2)
2. Bot envia link `t.me/SEU_BOT?start=<token>`
3. Usuário clica → `/start <token>` → bot valida token, pede nome, registra em `users`
4. Token marcado como `used_at = now`, não reutilizável

### Middleware de autorização
Todos os handlers verificam:
```python
user = await deps.sqlite.get_user_by_telegram_id(update.effective_user.id)
if user is None or user.status != 'active':
    await update.message.reply_text("⛔ Acesso não autorizado.")
    return
```

Atenção: hoje qualquer Telegram ID acessa o bot. Isso muda nessa fase.

---

## Fase 3 — Cronograma Macro (Gantt)

### Tabelas

```sql
CREATE TABLE schedule_phases (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    uid            TEXT UNIQUE NOT NULL,
    project_id     INTEGER NOT NULL REFERENCES projects(id),
    phase_number   INTEGER NOT NULL,
    name           TEXT NOT NULL,
    discipline     TEXT,   -- civil | eletrica | hidraulica | seguranca
    location       TEXT,
    planned_start  TEXT NOT NULL,
    planned_end    TEXT NOT NULL,
    actual_start   TEXT,
    actual_end     TEXT,
    progress_pct   REAL NOT NULL DEFAULT 0.0,
    status         TEXT NOT NULL DEFAULT 'pending',
    -- pending | active | done | delayed | suspended
    notes          TEXT,   -- campo mais importante
    order_index    INTEGER NOT NULL,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE phase_activities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    uid             TEXT UNIQUE NOT NULL,
    phase_id        INTEGER NOT NULL REFERENCES schedule_phases(id),
    name            TEXT NOT NULL,
    unit            TEXT,      -- m², m³, un, vb
    quantity_total  REAL,
    quantity_done   REAL NOT NULL DEFAULT 0.0,
    planned_start   TEXT,
    planned_end     TEXT,
    actual_start    TEXT,
    actual_end      TEXT,
    notes           TEXT,
    created_at      TEXT NOT NULL
);
```

### Gantt ASCII no Telegram
Renderizado em bloco de código (monospace garantido):

```
📅 CRONOGRAMA MACRO
Obra: Reforma da Igreja Messiânica
Hoje: 28/04/2026

FASE 1 ┤████████░░░         Mobilização         ✅ 100%
FASE 2 ┤    ████████▒▒▒▒░   Terraplanagem       🔄 42%
FASE 3 ┤             ░░░░░░ Estrutura            ⏳
FASE 4 ┤               ░░░░ Cobertura            ⏳
       ┼────┬────┬────┬────┬────
            Jan  Fev  Mar  Abr
```
`#GH72MX91`

Legenda: `█` executado · `▒` em andamento · `░` planejado

Entrada via formulário de botões (sem importar .mpp / .xlsx):
- `/fase add` → bot pergunta nome, datas, disciplina em sequência
- `/fase update FASE2 50` → atualiza progresso
- `/cronograma` → renderiza Gantt

---

## Fase 4 — RDO (Relatório Diário de Obra)

### Tabelas principais

```sql
CREATE TABLE daily_reports (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    uid                 TEXT UNIQUE NOT NULL,
    project_id          INTEGER NOT NULL REFERENCES projects(id),
    author_id           INTEGER NOT NULL REFERENCES users(id),
    report_date         TEXT NOT NULL,          -- data a que se refere o trabalho
    weather             TEXT,                   -- sol | nublado | chuva | chuva_forte
    temp_min_c          REAL,
    temp_max_c          REAL,
    precipitation_mm    REAL,
    shift_start         TEXT,                   -- HH:MM
    shift_end           TEXT,
    interruptions_min   INTEGER DEFAULT 0,      -- minutos de paralisação
    interruption_cause  TEXT,
    workers_count       INTEGER,
    activities          TEXT NOT NULL DEFAULT '[]',  -- JSON [{phase_id, activity_id, desc, pct_done}]
    issues              TEXT NOT NULL DEFAULT '[]',  -- JSON [{desc, severity, resolved, photos}]
    notes               TEXT,                   -- campo mais importante — anotações livres
    photos              TEXT NOT NULL DEFAULT '[]',  -- JSON [interaction_id, ...]
    status              TEXT NOT NULL DEFAULT 'draft',
    -- draft | submitted | approved | rejected
    approved_by         INTEGER REFERENCES users(id),
    approved_at         TEXT,
    rejection_reason    TEXT,
    interaction_id      INTEGER REFERENCES interactions(id),  -- conversa que gerou o RDO
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

-- Efetivos (trabalhadores em campo)
CREATE TABLE workforce (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id       INTEGER NOT NULL REFERENCES daily_reports(id),
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    user_id         INTEGER REFERENCES users(id),  -- NULL se não registrado
    name            TEXT NOT NULL,
    role            TEXT NOT NULL,   -- pedreiro | eletricista | encarregado | etc.
    hours_worked    REAL NOT NULL,
    phase_id        INTEGER REFERENCES schedule_phases(id),
    created_at      TEXT NOT NULL
);
```

### Embedding de RDOs
```python
embed_input = (
    f"OBRA: {project.name}\n"
    f"DATA: {report.report_date}\n"
    f"ATIVIDADES: {activities_text}\n"
    f"OCORRENCIAS: {issues_text}\n"
    f"NOTAS: {report.notes}"
)[:3000]
```

Isso permite consultas como: "o que aconteceu de problema nessa fase?" com recuperação semântica.

---

## Fase 5 — Financeiro

- `budget_items` (itens do orçamento por fase/atividade)
- `expenses` (gastos reais com data, valor, fornecedor, NF)
- `measurements` (medições de avanço físico-financeiro — boletim de medição)
- Relatório de desvio: planejado vs. executado por fase

Mais simples que as fases anteriores — majoritariamente cálculos e formatação.

---

## Decisões de design para lembrar

1. **`telegram_id` ≠ `users.id`** — sempre buscar por `telegram_id`, armazenar FK com `users.id`
2. **UIDs são só para exibição** — banco armazena sem `#`, mensagens exibem com `#` em code block
3. **Isolamento por obra é absoluto** — middleware deve checar `project_members` antes de qualquer leitura de dados de obra
4. **RAG injeta contexto de obra** — quando usuário estiver em contexto de obra ativa, o embedding prefix inclui nome da obra e categoria
5. **Notas são o campo mais importante** — nunca truncar, nunca limitar. É o que salva construtoras em disputas
6. **Aprovação de RDO tem trilha imutável** — `approved_by`, `approved_at`, nunca deixar UPDATE apagar
7. **Projeto principal** tem multiagentes + cálculos complexos que ficaram travados na IA — a validação aqui é justamente provar que uma única IA com RAG contrastivo resolve sem orquestrador
