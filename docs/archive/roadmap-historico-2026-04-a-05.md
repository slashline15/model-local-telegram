# ROADMAP — Bot RDO / ollama_telegram

Contexto: bot Telegram com RAG contrastivo sobre Ollama local.
O núcleo de IA (RAG, embeddings, contrastive learning) está funcional e estável.
O objetivo é evoluir para um sistema multi-usuário de RDO (Relatório Diário de Obra).

---

## Changelog

- **2026-05-14** — Isolamento básico de chats por usuário. Coluna `visibilidade` (`publica` | `privada`) em `interactions` e `documents`, default `publica`. `fetch_by_ids` agora exige `requester_user_id` (kwonly): inteiro filtra "pública OR dono", `None` é bypass explícito (admin/teste). Filtro aplicado em 3 pontos: RAG retrieval (`llm/contrastive_rag.py`), `/recall #iXX` e snippets do `/recall <texto>`. Fecha o vetor de "telepatia" onde qualquer usuário citava `#iXX` e recebia conteúdo alheio. Brecha de leitura por N1/N2 fica como TODO no repo até a hierarquia de papéis estar mapeada.
- **2026-05-12** — Refundação do schema. Decisão de aproveitar a base atual em vez de recomeçar; `interactions` é "tabela-mochila" e será esvaziada de dados de obra. Dados passam a viver em tabelas próprias por entidade (atividades, efetivo, clima, expediente, materiais, anotações). RDO deixa de ser entidade e vira VIEW agregada por `(project_id, dia)`. Comando `/doc` exclusivo para upload classificado (N1/N2) com ACL por classe. Ver [Fase 4 (reescrita)](#fase-4-reescrita-2026-05--refundação-do-schema).
- **2026-04-28** — Estado inicial documentado. Fase 2 (multi-obra) parcialmente implementada; Fase 3 (Gantt) e Fase 4 (RDO original) desenhadas mas não construídas.

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

## Fase 4 (reescrita 2026-05) — Refundação do schema

> **Esta seção substitui o desenho original da Fase 4 (mantido abaixo como referência histórica).**
>
> A versão antiga modelava o RDO como uma tabela única `daily_reports` com JSON em `activities`, `issues`, `photos`. Em uso real isso reproduziu o problema atual de `interactions`: tabela-mochila, difícil de evoluir, sem rastreabilidade por item.

### Decisões que disparam essa fase

1. **`interactions` virou mochila.** 23 colunas misturando conversa, telemetria, estado de RAG e avaliação. Adicionar campo novo = `ALTER TABLE` em tabela viva. Inviável a médio prazo.
2. **RDO não é entidade.** É um *relatório* — VIEW SQL que agrega tabelas operacionais por `(project_id, dia)`. As entidades de verdade são atividade, efetivo, clima, expediente, material, anotação.
3. **Documento importante exige entrada explícita.** Folha de pagamento, aditivo contratual, memorial: enviados a frio por N1/N2, classificados na hora, com ACL por classe. Não podem se misturar com tráfego operacional.
4. **Aproveitar > recomeçar.** 10 das 14 tabelas atuais já estão corretas (users, projects, project_members, invites, funcoes, empresas, colaboradores, model_pricing, token_usage, pipeline_steps). Recomeçar = 2 meses recriando o que já existe.

### Esqueleto consolidado

**Mantidas como estão:** `users`, `projects`, `project_members`, `invites`, `funcoes`, `empresas`, `colaboradores`, `reminders`, `pipeline_steps`, `token_usage`, `model_pricing`, `user_settings`.

**Mantidas, mas esvaziadas:** `interactions` (vira só log de mensagem), `interaction_chunks` → renomear para `chunks` e generalizar.

**Novas — fonte da verdade dos dados de obra:**

```sql
-- Catálogo de classes de documento (peso editável + ACL)
CREATE TABLE doc_classes (
    slug                TEXT PRIMARY KEY,        -- contrato, memorial, norma, ...
    label               TEXT NOT NULL,
    peso                REAL NOT NULL DEFAULT 1.0,
    nivel_min_classificar INTEGER NOT NULL DEFAULT 3,  -- só N1/N2 podem marcar como N1
    nivel_min_ler       INTEGER NOT NULL DEFAULT 3,    -- quem pode receber chunks no RAG
    ativo               INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL
);

-- Permissões em tabela própria (sem ALTER quando regra muda)
CREATE TABLE role_permissions (
    role        TEXT NOT NULL,       -- N1 | N2 | N3
    action      TEXT NOT NULL,       -- criar, ler, editar, classificar
    resource    TEXT NOT NULL,       -- anotacao, documento, doc_classes:contrato, ...
    allowed     INTEGER NOT NULL,
    PRIMARY KEY (role, action, resource)
);

-- Upload classificado de documento (entrada via /doc, N1/N2)
CREATE TABLE documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    uid             TEXT UNIQUE NOT NULL,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    doc_class       TEXT NOT NULL REFERENCES doc_classes(slug),
    titulo          TEXT NOT NULL,
    arquivo_path    TEXT,
    arquivo_hash    TEXT,
    mime            TEXT,
    enviado_por     INTEGER NOT NULL REFERENCES users(id),
    interaction_id  INTEGER REFERENCES interactions(id),  -- origem (mensagem do /doc)
    created_at      TEXT NOT NULL
);

-- Cronograma macro (organização, sem regras de impedimento)
CREATE TABLE cronograma_etapas (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    uid                     TEXT UNIQUE NOT NULL,
    project_id              INTEGER NOT NULL REFERENCES projects(id),
    parent_id               INTEGER REFERENCES cronograma_etapas(id),  -- hierarquia leve
    etapa                   TEXT NOT NULL,
    descricao               TEXT,
    data_prevista_inicio    TEXT,
    data_prevista_termino   TEXT,
    ordem                   INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT NOT NULL
);

-- ENTIDADES DE OBRA (cada uma com FK pra interaction_id de origem)

CREATE TABLE atividades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    dia             TEXT NOT NULL,            -- YYYY-MM-DD
    etapa_id        INTEGER REFERENCES cronograma_etapas(id),
    responsavel_id  INTEGER REFERENCES users(id),
    estado          TEXT NOT NULL,            -- concluida | em_andamento | atrasada | impedida
    descricao       TEXT NOT NULL,
    interaction_id  INTEGER REFERENCES interactions(id),
    criado_por      INTEGER NOT NULL REFERENCES users(id),
    created_at      TEXT NOT NULL
);

CREATE TABLE efetivo_diario (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    dia             TEXT NOT NULL,
    funcao_id       INTEGER NOT NULL REFERENCES funcoes(id),
    empresa_id      INTEGER REFERENCES empresas(id),
    qtd             INTEGER NOT NULL,
    interaction_id  INTEGER REFERENCES interactions(id),
    criado_por      INTEGER NOT NULL REFERENCES users(id),
    created_at      TEXT NOT NULL
);

CREATE TABLE clima_diario (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    dia             TEXT NOT NULL,
    condicao        TEXT NOT NULL,            -- sol | nublado | chuva
    hora_inicio     TEXT,
    hora_fim        TEXT,
    interaction_id  INTEGER REFERENCES interactions(id),
    criado_por      INTEGER NOT NULL REFERENCES users(id),
    created_at      TEXT NOT NULL
);

CREATE TABLE expediente_diario (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    dia         TEXT NOT NULL,
    inicio      TEXT NOT NULL,
    fim         TEXT NOT NULL,
    regime      TEXT,                          -- normal | hora_extra | turno_especial
    criado_por  INTEGER NOT NULL REFERENCES users(id),
    created_at  TEXT NOT NULL,
    UNIQUE (project_id, dia)
);

CREATE TABLE materiais_movimento (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    dia             TEXT NOT NULL,
    item            TEXT NOT NULL,
    qtd             REAL,
    unidade         TEXT,
    responsavel     TEXT,                      -- "cliente" | "obra" | nome livre
    status          TEXT,                      -- entregue | atrasado | parcial | em_uso | parado
    interaction_id  INTEGER REFERENCES interactions(id),
    criado_por      INTEGER NOT NULL REFERENCES users(id),
    created_at      TEXT NOT NULL
);

CREATE TABLE anotacoes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    dia             TEXT NOT NULL,
    inicio          TEXT,                      -- HH:MM ou ISO
    fim             TEXT,
    natureza        TEXT NOT NULL,             -- evento | ocorrencia
    atividade_id    INTEGER REFERENCES atividades(id),
    recurso         TEXT,                      -- ref. solto pra material/efetivo/etc
    impacto         TEXT,                      -- atraso | aditivo | retrabalho | nenhum | ...
    texto           TEXT NOT NULL,
    visibilidade    TEXT NOT NULL DEFAULT 'publica',  -- publica | privada
    interaction_id  INTEGER REFERENCES interactions(id),
    criado_por      INTEGER NOT NULL REFERENCES users(id),
    created_at      TEXT NOT NULL
);
```

**Satélites de `interactions` (passo final, quando handlers já não gravam dado de obra ali):**

```sql
CREATE TABLE interaction_telemetry (
    interaction_id      INTEGER PRIMARY KEY REFERENCES interactions(id),
    model_used          TEXT,
    temperature         REAL,
    prompt_tokens       INTEGER,
    response_tokens     INTEGER,
    total_duration_ms   INTEGER,
    tool_calls          TEXT NOT NULL DEFAULT '[]',
    error               TEXT,
    run_id              TEXT
);

CREATE TABLE interaction_rag (
    interaction_id      INTEGER PRIMARY KEY REFERENCES interactions(id),
    positive_ids        TEXT NOT NULL DEFAULT '[]',
    negative_ids        TEXT NOT NULL DEFAULT '[]',
    retrieved_count     INTEGER,
    embedding_model     TEXT,
    embedding_dim       INTEGER,
    prompt_used         TEXT
);
```

Depois disso `interactions` fica com: `id, user_id, chat_id, project_id, user_message, bot_response, timestamp, media_path, media_type, score, intent, correction, tags`.

### RDO como VIEW

```sql
CREATE VIEW vw_rdo_dia AS
SELECT
    p.id                AS project_id,
    p.name              AS obra,
    d.dia               AS dia,
    e.inicio            AS expediente_inicio,
    e.fim               AS expediente_fim,
    c.condicao          AS clima,
    -- agregações via subqueries ou GROUP BY conforme o cliente final
    ...
FROM projects p
CROSS JOIN (SELECT DISTINCT dia FROM atividades UNION ... ) d
LEFT JOIN expediente_diario e ON e.project_id=p.id AND e.dia=d.dia
LEFT JOIN clima_diario       c ON c.project_id=p.id AND c.dia=d.dia
WHERE ...;
```

Forma final da VIEW se desenha quando os primeiros dias reais existirem no banco — antes disso é especulação.

### Comando `/doc` (entrada explícita, N1/N2)

Fluxo:
1. Usuário N1/N2 manda `/doc` (ou anexa arquivo já com legenda especial)
2. Bot pergunta classe via botões inline (lê `doc_classes` filtrado por `nivel_min_classificar <= user.role`)
3. Bot pergunta título
4. Salva em `documents`, chunkifica (chunking semântico, ver passo 7 do plano), indexa em `chunks` com `doc_class` + peso + boost por remetente
5. Retrieval respeita ACL: chunk só entra no contexto se `user.role <= doc_classes.nivel_min_ler`

### Plano de transição (estrangulamento)

Cada passo isolado, validável sozinho. Pode parar em qualquer um sem quebrar o bot.

1. Aprovar este esqueleto ✅ (sessão 2026-05-12)
2. Criar tabelas novas vazias — não quebra nada existente
3. Implementar comando `/doc` + `doc_classes` + `role_permissions` + ACL no retrieval
4. Handlers passam a gravar em `atividades`/`efetivo`/`clima`/`materiais`/`anotacoes` **em paralelo** a `interactions` (fase de coexistência)
5. Quando handlers cobrirem todos os tipos, `interactions` para de receber dado de obra (vira log puro)
6. Criar `vw_rdo_dia` a partir dos primeiros dias reais
7. Migrar chunking de "tamanho fixo" para semântico (parágrafo/seção)
8. Criar `interaction_telemetry` + `interaction_rag`, backfill, depois `DROP COLUMN` em `interactions`

### Avaliação de frameworks (referência rápida)

| Framework | Veredito |
|-----------|----------|
| CrewAI / LangGraph | Orquestração multi-agente. Você tem pipeline linear. Não entra agora. |
| mem0 | Memória de longo prazo com extração de fatos. Pode entrar **acima** do SQLite, depois. |
| graphiti | Grafo temporal. Combina com "linha do tempo da obra". Avaliar após esqueleto. |
| Botpress | Builder visual de bot. Não serve — você quer controle de schema. |

Nenhum substitui a fundação. SQLite continua sendo a fonte da verdade jurídica.

---

## Fase 4 (original 2026-04 — superada, mantida como referência)

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
