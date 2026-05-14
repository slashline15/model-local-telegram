# database/schema.py

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from core.logger import get_logger

log = get_logger(__name__)


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


_INTERACTIONS_BASE: str = """
CREATE TABLE IF NOT EXISTS interactions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL,
    chat_id             INTEGER,
    user_message        TEXT    NOT NULL,
    bot_response        TEXT    NOT NULL,
    timestamp           TEXT    NOT NULL,
    media_path          TEXT,
    media_type          TEXT,
    score               INTEGER,
    tags                TEXT    NOT NULL DEFAULT '[]',
    intent              TEXT,
    model_used          TEXT,
    temperature         REAL,
    prompt_tokens       INTEGER,
    response_tokens     INTEGER,
    total_duration_ms   INTEGER,
    prompt_used         TEXT,
    positive_ids        TEXT    NOT NULL DEFAULT '[]',
    negative_ids        TEXT    NOT NULL DEFAULT '[]',
    retrieved_count     INTEGER,
    embedding_model     TEXT,
    embedding_dim       INTEGER,
    tool_calls          TEXT    NOT NULL DEFAULT '[]',
    error               TEXT,
    run_id              TEXT,
    project_id          INTEGER REFERENCES projects(id),
    visibilidade        TEXT    NOT NULL DEFAULT 'publica'  -- publica | privada
);
"""

_USER_SETTINGS_BASE: str = """
CREATE TABLE IF NOT EXISTS user_settings (
    user_id            INTEGER PRIMARY KEY,
    current_model      TEXT    NOT NULL DEFAULT 'gemma:2b',
    temperature        REAL    NOT NULL DEFAULT 0.7,
    current_project_id INTEGER REFERENCES projects(id),
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL
);
"""

_REMINDERS_BASE: str = """
CREATE TABLE IF NOT EXISTS reminders (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id               INTEGER NOT NULL,
    chat_id               INTEGER NOT NULL,
    text                  TEXT    NOT NULL,
    scheduled_for         TEXT    NOT NULL,
    status                TEXT    NOT NULL DEFAULT 'pending',
    source_interaction_id INTEGER,
    created_at            TEXT    NOT NULL,
    sent_at               TEXT
);
"""

_PIPELINE_STEPS_BASE: str = """
CREATE TABLE IF NOT EXISTS pipeline_steps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT    NOT NULL,
    interaction_id  INTEGER,
    user_id         INTEGER NOT NULL,
    chat_id         INTEGER,
    step_index      INTEGER NOT NULL,
    step_name       TEXT    NOT NULL,
    status          TEXT    NOT NULL,
    duration_ms     INTEGER NOT NULL DEFAULT 0,
    details         TEXT    NOT NULL DEFAULT '{}',
    error           TEXT,
    timestamp       TEXT    NOT NULL
);
"""

_USERS_BASE: str = """
CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id  INTEGER UNIQUE NOT NULL,
    name         TEXT    NOT NULL,
    email        TEXT,
    role         TEXT    NOT NULL DEFAULT 'worker',
    status       TEXT    NOT NULL DEFAULT 'active',
    invited_by   INTEGER REFERENCES users(id),
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);
"""

_PROJECTS_BASE: str = """
CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    uid         TEXT    UNIQUE NOT NULL,
    name        TEXT    NOT NULL,
    address     TEXT,
    type        TEXT,
    status      TEXT    NOT NULL DEFAULT 'active',
    start_date  TEXT,
    end_date    TEXT,
    created_by  INTEGER NOT NULL REFERENCES users(id),
    admin_id    INTEGER NOT NULL REFERENCES users(id),
    created_at  TEXT    NOT NULL
);
"""

_INVITES_BASE: str = """
CREATE TABLE IF NOT EXISTS invites (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    uid         TEXT    UNIQUE NOT NULL,
    token       TEXT    UNIQUE NOT NULL,
    project_id  INTEGER REFERENCES projects(id),
    role        TEXT    NOT NULL,
    created_by  INTEGER NOT NULL REFERENCES users(id),
    used_by     INTEGER REFERENCES users(id),
    expires_at  TEXT,
    used_at     TEXT,
    created_at  TEXT    NOT NULL
);
"""

_PROJECT_MEMBERS_BASE: str = """
CREATE TABLE IF NOT EXISTS project_members (
    project_id          INTEGER NOT NULL REFERENCES projects(id),
    user_id             INTEGER NOT NULL REFERENCES users(id),
    role                TEXT    NOT NULL,
    can_approve_rdo     INTEGER NOT NULL DEFAULT 0,
    can_view_financial  INTEGER NOT NULL DEFAULT 0,
    can_invite          INTEGER NOT NULL DEFAULT 0,
    joined_at           TEXT    NOT NULL,
    invite_id           INTEGER REFERENCES invites(id),
    PRIMARY KEY (project_id, user_id)
);
"""

# Catálogo global de funções (cargos). Não é por obra — mesma "Pedreiro" vale
# em qualquer canteiro. `ativo=0` esconde da UI mas preserva FKs históricas.
_FUNCOES_BASE: str = """
CREATE TABLE IF NOT EXISTS funcoes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nome        TEXT    UNIQUE NOT NULL,
    ativo       INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL
);
"""

# Empresas (próprias e terceirizadas) — sempre vinculadas a uma obra.
# Mesma empresa em N obras = N linhas (por design — facilita dados/permissões).
_EMPRESAS_BASE: str = """
CREATE TABLE IF NOT EXISTS empresas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    uid         TEXT    UNIQUE NOT NULL,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    nome        TEXT    NOT NULL,
    cnpj        TEXT,
    tipo        TEXT    NOT NULL DEFAULT 'third_party',  -- own | third_party
    ativo       INTEGER NOT NULL DEFAULT 1,
    created_by  INTEGER NOT NULL REFERENCES users(id),
    created_at  TEXT    NOT NULL
);
"""

# Colaboradores individuais — geralmente da empresa própria. Terceiros entram
# como contagem por empresa no efetivo (Fase 4); cadastro individual aqui é
# opcional (funcao_id nulo é aceito).
_COLABORADORES_BASE: str = """
CREATE TABLE IF NOT EXISTS colaboradores (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    uid         TEXT    UNIQUE NOT NULL,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    empresa_id  INTEGER NOT NULL REFERENCES empresas(id),
    funcao_id   INTEGER REFERENCES funcoes(id),
    nome        TEXT    NOT NULL,
    apelido     TEXT,
    ativo       INTEGER NOT NULL DEFAULT 1,
    created_by  INTEGER NOT NULL REFERENCES users(id),
    created_at  TEXT    NOT NULL
);
"""

_INTERACTION_CHUNKS_BASE: str = """
CREATE TABLE IF NOT EXISTS interaction_chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    interaction_id  INTEGER NOT NULL REFERENCES interactions(id),
    chunk_idx       INTEGER NOT NULL,
    content         TEXT    NOT NULL,
    doc_class       TEXT    NOT NULL DEFAULT 'note',
    weight          REAL    NOT NULL DEFAULT 1.0,
    created_at      TEXT    NOT NULL
);
"""

# =============================================================================
# Refundação 2026-05 — tabelas novas (esqueleto consolidado)
# =============================================================================
# Convenção de nível: INTEGER 1=N1 (admin), 2=N2 (co-responsável), 3=N3 (op).
# ACL: usuário com role numérico <= valor_min pode operar.
# Ver ROADMAP.md (Fase 4 reescrita) e vault Obsidian "Esqueleto consolidado".

# Catálogo de classes de documento. Substitui o enum hardcoded de doc_class.
# Peso e ACL editáveis sem código — adicionar/remover classe é INSERT/UPDATE.
_DOC_CLASSES_BASE: str = """
CREATE TABLE IF NOT EXISTS doc_classes (
    slug                    TEXT    PRIMARY KEY,
    label                   TEXT    NOT NULL,
    peso                    REAL    NOT NULL DEFAULT 1.0,
    nivel_min_classificar   INTEGER NOT NULL DEFAULT 3,
    nivel_min_ler           INTEGER NOT NULL DEFAULT 3,
    ativo                   INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT    NOT NULL
);
"""

# Regras de permissão por papel. Adicionar regra = INSERT, não código.
# Convenção: ausência de linha = lógica default da app decide.
_ROLE_PERMISSIONS_BASE: str = """
CREATE TABLE IF NOT EXISTS role_permissions (
    role        TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    resource    TEXT    NOT NULL,
    allowed     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (role, action, resource)
);
"""

# Upload classificado de documento (via /doc). Separado de interactions
# porque tem ACL forte e ciclo de vida próprio.
_DOCUMENTS_BASE: str = """
CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    uid             TEXT    UNIQUE NOT NULL,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    doc_class       TEXT    NOT NULL REFERENCES doc_classes(slug),
    titulo          TEXT    NOT NULL,
    arquivo_path    TEXT,
    arquivo_hash    TEXT,
    mime            TEXT,
    enviado_por     INTEGER NOT NULL REFERENCES users(id),
    interaction_id  INTEGER REFERENCES interactions(id),
    visibilidade    TEXT    NOT NULL DEFAULT 'publica',  -- publica | privada
    created_at      TEXT    NOT NULL
);
"""

# Cronograma macro. Organização leve com auto-relação (parent_id).
# Sem regras de impedimento — só pra orientar atividades.
_CRONOGRAMA_ETAPAS_BASE: str = """
CREATE TABLE IF NOT EXISTS cronograma_etapas (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    uid                     TEXT    UNIQUE NOT NULL,
    project_id              INTEGER NOT NULL REFERENCES projects(id),
    parent_id               INTEGER REFERENCES cronograma_etapas(id),
    etapa                   TEXT    NOT NULL,
    descricao               TEXT,
    data_prevista_inicio    TEXT,
    data_prevista_termino   TEXT,
    ordem                   INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT    NOT NULL
);
"""

# ENTIDADES DE OBRA — fonte da verdade. interaction_id é só rastreabilidade.

_ATIVIDADES_BASE: str = """
CREATE TABLE IF NOT EXISTS atividades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    dia             TEXT    NOT NULL,
    etapa_id        INTEGER REFERENCES cronograma_etapas(id),
    responsavel_id  INTEGER REFERENCES users(id),
    estado          TEXT    NOT NULL,
    descricao       TEXT    NOT NULL,
    interaction_id  INTEGER REFERENCES interactions(id),
    criado_por      INTEGER NOT NULL REFERENCES users(id),
    created_at      TEXT    NOT NULL
);
"""

_EFETIVO_DIARIO_BASE: str = """
CREATE TABLE IF NOT EXISTS efetivo_diario (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    dia             TEXT    NOT NULL,
    funcao_id       INTEGER NOT NULL REFERENCES funcoes(id),
    empresa_id      INTEGER REFERENCES empresas(id),
    qtd             INTEGER NOT NULL,
    interaction_id  INTEGER REFERENCES interactions(id),
    criado_por      INTEGER NOT NULL REFERENCES users(id),
    created_at      TEXT    NOT NULL
);
"""

_CLIMA_DIARIO_BASE: str = """
CREATE TABLE IF NOT EXISTS clima_diario (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    dia             TEXT    NOT NULL,
    condicao        TEXT    NOT NULL,
    hora_inicio     TEXT,
    hora_fim        TEXT,
    interaction_id  INTEGER REFERENCES interactions(id),
    criado_por      INTEGER NOT NULL REFERENCES users(id),
    created_at      TEXT    NOT NULL
);
"""

# Um expediente por (project, dia).
_EXPEDIENTE_DIARIO_BASE: str = """
CREATE TABLE IF NOT EXISTS expediente_diario (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    dia         TEXT    NOT NULL,
    inicio      TEXT    NOT NULL,
    fim         TEXT    NOT NULL,
    regime      TEXT,
    criado_por  INTEGER NOT NULL REFERENCES users(id),
    created_at  TEXT    NOT NULL,
    UNIQUE (project_id, dia)
);
"""

_MATERIAIS_MOVIMENTO_BASE: str = """
CREATE TABLE IF NOT EXISTS materiais_movimento (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    dia             TEXT    NOT NULL,
    item            TEXT    NOT NULL,
    qtd             REAL,
    unidade         TEXT,
    responsavel     TEXT,
    status          TEXT,
    interaction_id  INTEGER REFERENCES interactions(id),
    criado_por      INTEGER NOT NULL REFERENCES users(id),
    created_at      TEXT    NOT NULL
);
"""

# Campo crítico do RDO — temporalidade, vínculo, impacto, natureza.
_ANOTACOES_BASE: str = """
CREATE TABLE IF NOT EXISTS anotacoes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL REFERENCES projects(id),
    dia             TEXT    NOT NULL,
    inicio          TEXT,
    fim             TEXT,
    natureza        TEXT    NOT NULL,
    atividade_id    INTEGER REFERENCES atividades(id),
    recurso         TEXT,
    impacto         TEXT,
    texto           TEXT    NOT NULL,
    visibilidade    TEXT    NOT NULL DEFAULT 'publica',
    interaction_id  INTEGER REFERENCES interactions(id),
    criado_por      INTEGER NOT NULL REFERENCES users(id),
    created_at      TEXT    NOT NULL
);
"""

# Satélites de interactions (1:1). Populados em paralelo a interactions
# até a coexistência terminar; depois interactions perde essas colunas.
_INTERACTION_TELEMETRY_BASE: str = """
CREATE TABLE IF NOT EXISTS interaction_telemetry (
    interaction_id      INTEGER PRIMARY KEY REFERENCES interactions(id),
    model_used          TEXT,
    temperature         REAL,
    prompt_tokens       INTEGER,
    response_tokens     INTEGER,
    total_duration_ms   INTEGER,
    tool_calls          TEXT    NOT NULL DEFAULT '[]',
    error               TEXT,
    run_id              TEXT
);
"""

_INTERACTION_RAG_BASE: str = """
CREATE TABLE IF NOT EXISTS interaction_rag (
    interaction_id      INTEGER PRIMARY KEY REFERENCES interactions(id),
    positive_ids        TEXT    NOT NULL DEFAULT '[]',
    negative_ids        TEXT    NOT NULL DEFAULT '[]',
    retrieved_count     INTEGER,
    embedding_model     TEXT,
    embedding_dim       INTEGER,
    prompt_used         TEXT
);
"""

_TOKEN_USAGE_BASE: str = """
CREATE TABLE IF NOT EXISTS token_usage (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT    NOT NULL,
    interaction_id      INTEGER REFERENCES interactions(id),
    user_id             INTEGER NOT NULL,
    project_id          INTEGER,
    model               TEXT    NOT NULL,
    backend             TEXT    NOT NULL DEFAULT 'ollama',
    operation           TEXT    NOT NULL,
    prompt_tokens       INTEGER NOT NULL DEFAULT 0,
    response_tokens     INTEGER NOT NULL DEFAULT 0,
    total_tokens        INTEGER NOT NULL DEFAULT 0,
    duration_ms         INTEGER NOT NULL DEFAULT 0,
    quantity_secondary  REAL    DEFAULT 0,
    created_at          TEXT    NOT NULL
);
"""

_MODEL_PRICING_BASE: str = """
CREATE TABLE IF NOT EXISTS model_pricing (
    model              TEXT    PRIMARY KEY,
    backend            TEXT    NOT NULL,
    cost_per_1k_input  REAL    DEFAULT 0,
    cost_per_1k_output REAL    DEFAULT 0,
    currency           TEXT    DEFAULT 'USD',
    updated_at         TEXT    NOT NULL
);
"""

_MODEL_PRICING_SEED: tuple[tuple[str, str, float, float, str], ...] = (
    ("gemma4:31b-cloud",       "ollama", 0,       0,      "USD"),
    ("llama3.2:3b",            "ollama", 0,       0,      "USD"),
    ("nomic-embed-text:v1.5",  "ollama", 0,       0,      "USD"),
    ("gpt-4o-mini",            "openai", 0.00015, 0.0006, "USD"),
    ("whisper-1",              "openai", 0.006,   0,      "USD"),
)

# (slug, label, peso, nivel_min_classificar, nivel_min_ler)
# Nível: 1=N1 (admin), 2=N2 (co-resp), 3=N3 (op). Comparação <= libera.
# Ex.: nivel_min_ler=1 → só N1 lê (folha de pagamento).
_DOC_CLASSES_SEED: tuple[tuple[str, str, float, int, int], ...] = (
    ("contrato",   "Contrato",                 1.5, 2, 2),
    ("memorial",   "Memorial descritivo",      1.4, 2, 3),
    ("norma",      "Norma técnica",            1.3, 2, 3),
    ("proposta",   "Proposta comercial",       1.1, 2, 2),
    ("folha_pgto", "Folha de pagamento",       1.4, 1, 1),
    ("anotacao",   "Anotação livre",           1.0, 3, 3),
    ("reuniao",    "Transcrição de reunião",   0.7, 3, 3),
    ("outro",      "Outro",                    0.8, 3, 3),
)

_TABLES: tuple[str, ...] = (
    _INTERACTIONS_BASE,
    _USER_SETTINGS_BASE,
    _PIPELINE_STEPS_BASE,
    _REMINDERS_BASE,
    _USERS_BASE,
    _PROJECTS_BASE,
    _INVITES_BASE,
    _PROJECT_MEMBERS_BASE,
    _FUNCOES_BASE,
    _EMPRESAS_BASE,
    _COLABORADORES_BASE,
    _INTERACTION_CHUNKS_BASE,
    _TOKEN_USAGE_BASE,
    _MODEL_PRICING_BASE,
    # Refundação 2026-05
    _DOC_CLASSES_BASE,
    _ROLE_PERMISSIONS_BASE,
    _DOCUMENTS_BASE,
    _CRONOGRAMA_ETAPAS_BASE,
    _ATIVIDADES_BASE,
    _EFETIVO_DIARIO_BASE,
    _CLIMA_DIARIO_BASE,
    _EXPEDIENTE_DIARIO_BASE,
    _MATERIAIS_MOVIMENTO_BASE,
    _ANOTACOES_BASE,
    _INTERACTION_TELEMETRY_BASE,
    _INTERACTION_RAG_BASE,
)

# Funções fixas do desenho do excalidraw — seed inicial, idempotente.
_FUNCOES_SEED: tuple[str, ...] = (
    "Engenheiro",
    "Estagiário",
    "Auxiliar",
    "Apontador",
    "Mestre de obras",
    "Encarregado",
    "Gestor",
    "Técnico de Segurança",
    "Eletricista",
    "Almoxarife",
    "Pedreiro",
    "Carpinteiro",
    "Servente",
    "Betoneiro",
    "Motorista",
)

_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_interactions_user      ON interactions(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_interactions_score     ON interactions(score);",
    "CREATE INDEX IF NOT EXISTS idx_interactions_intent    ON interactions(intent);",
    "CREATE INDEX IF NOT EXISTS idx_interactions_run       ON interactions(run_id);",
    "CREATE INDEX IF NOT EXISTS idx_pipeline_run           ON pipeline_steps(run_id);",
    "CREATE INDEX IF NOT EXISTS idx_pipeline_interaction   ON pipeline_steps(interaction_id);",
    "CREATE INDEX IF NOT EXISTS idx_pipeline_user          ON pipeline_steps(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_reminders_status       ON reminders(status);",
    "CREATE INDEX IF NOT EXISTS idx_reminders_user         ON reminders(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_reminders_due          ON reminders(scheduled_for);",
    "CREATE INDEX IF NOT EXISTS idx_users_telegram         ON users(telegram_id);",
    "CREATE INDEX IF NOT EXISTS idx_users_role             ON users(role);",
    "CREATE INDEX IF NOT EXISTS idx_users_status           ON users(status);",
    "CREATE INDEX IF NOT EXISTS idx_projects_status        ON projects(status);",
    "CREATE INDEX IF NOT EXISTS idx_projects_created_by    ON projects(created_by);",
    "CREATE INDEX IF NOT EXISTS idx_invites_token          ON invites(token);",
    "CREATE INDEX IF NOT EXISTS idx_invites_project        ON invites(project_id);",
    "CREATE INDEX IF NOT EXISTS idx_invites_unused         ON invites(used_at) WHERE used_at IS NULL;",
    "CREATE INDEX IF NOT EXISTS idx_members_user           ON project_members(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_interactions_project   ON interactions(project_id);",
    "CREATE INDEX IF NOT EXISTS idx_projects_admin         ON projects(admin_id);",
    "CREATE INDEX IF NOT EXISTS idx_funcoes_ativo          ON funcoes(ativo);",
    "CREATE INDEX IF NOT EXISTS idx_empresas_project       ON empresas(project_id);",
    "CREATE INDEX IF NOT EXISTS idx_empresas_ativo         ON empresas(ativo);",
    "CREATE INDEX IF NOT EXISTS idx_colaboradores_project  ON colaboradores(project_id);",
    "CREATE INDEX IF NOT EXISTS idx_colaboradores_empresa  ON colaboradores(empresa_id);",
    "CREATE INDEX IF NOT EXISTS idx_colaboradores_funcao   ON colaboradores(funcao_id);",
    "CREATE INDEX IF NOT EXISTS idx_colaboradores_ativo    ON colaboradores(ativo);",
    # interaction_chunks
    "CREATE INDEX IF NOT EXISTS idx_chunks_interaction     ON interaction_chunks(interaction_id);",
    "CREATE INDEX IF NOT EXISTS idx_chunks_document        ON interaction_chunks(document_id);",
    # token_usage
    "CREATE INDEX IF NOT EXISTS idx_token_usage_user       ON token_usage(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_token_usage_project    ON token_usage(project_id);",
    "CREATE INDEX IF NOT EXISTS idx_token_usage_model      ON token_usage(model);",
    "CREATE INDEX IF NOT EXISTS idx_token_usage_created    ON token_usage(created_at);",
    # Refundação 2026-05
    "CREATE INDEX IF NOT EXISTS idx_doc_classes_ativo      ON doc_classes(ativo);",
    "CREATE INDEX IF NOT EXISTS idx_documents_project      ON documents(project_id);",
    "CREATE INDEX IF NOT EXISTS idx_documents_class        ON documents(doc_class);",
    "CREATE INDEX IF NOT EXISTS idx_documents_enviado_por  ON documents(enviado_por);",
    "CREATE INDEX IF NOT EXISTS idx_cronograma_project     ON cronograma_etapas(project_id);",
    "CREATE INDEX IF NOT EXISTS idx_cronograma_parent      ON cronograma_etapas(parent_id);",
    "CREATE INDEX IF NOT EXISTS idx_atividades_project_dia ON atividades(project_id, dia);",
    "CREATE INDEX IF NOT EXISTS idx_atividades_etapa       ON atividades(etapa_id);",
    "CREATE INDEX IF NOT EXISTS idx_atividades_resp        ON atividades(responsavel_id);",
    "CREATE INDEX IF NOT EXISTS idx_efetivo_project_dia    ON efetivo_diario(project_id, dia);",
    "CREATE INDEX IF NOT EXISTS idx_efetivo_funcao         ON efetivo_diario(funcao_id);",
    "CREATE INDEX IF NOT EXISTS idx_efetivo_empresa        ON efetivo_diario(empresa_id);",
    "CREATE INDEX IF NOT EXISTS idx_clima_project_dia      ON clima_diario(project_id, dia);",
    "CREATE INDEX IF NOT EXISTS idx_materiais_project_dia  ON materiais_movimento(project_id, dia);",
    "CREATE INDEX IF NOT EXISTS idx_anotacoes_project_dia  ON anotacoes(project_id, dia);",
    "CREATE INDEX IF NOT EXISTS idx_anotacoes_atividade    ON anotacoes(atividade_id);",
    "CREATE INDEX IF NOT EXISTS idx_anotacoes_natureza     ON anotacoes(natureza);",
)

# (tabela, coluna, declaração) — aplicado se faltar a coluna em DBs antigos.
_LEGACY_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("interactions", "chat_id",            "INTEGER"),
    ("interactions", "media_type",         "TEXT"),
    ("interactions", "intent",             "TEXT"),
    ("interactions", "model_used",         "TEXT"),
    ("interactions", "temperature",        "REAL"),
    ("interactions", "prompt_tokens",      "INTEGER"),
    ("interactions", "response_tokens",    "INTEGER"),
    ("interactions", "total_duration_ms",  "INTEGER"),
    ("interactions", "prompt_used",        "TEXT"),
    ("interactions", "positive_ids",       "TEXT NOT NULL DEFAULT '[]'"),
    ("interactions", "negative_ids",       "TEXT NOT NULL DEFAULT '[]'"),
    ("interactions", "retrieved_count",    "INTEGER"),
    ("interactions", "embedding_model",    "TEXT"),
    ("interactions", "embedding_dim",      "INTEGER"),
    ("interactions", "tool_calls",         "TEXT NOT NULL DEFAULT '[]'"),
    ("interactions", "error",              "TEXT"),
    ("interactions", "run_id",             "TEXT"),
    ("user_settings", "created_at",         "TEXT NOT NULL DEFAULT ''"),
    ("user_settings", "updated_at",         "TEXT NOT NULL DEFAULT ''"),
    ("user_settings", "current_project_id", "INTEGER REFERENCES projects(id)"),
    ("interactions",  "project_id",         "INTEGER REFERENCES projects(id)"),
    ("projects",      "admin_id",           "INTEGER REFERENCES users(id)"),
    # Refundação 2026-05
    ("interactions",       "correction",   "TEXT"),
    ("interaction_chunks", "document_id",  "INTEGER REFERENCES documents(id)"),
    ("interactions",       "visibilidade", "TEXT NOT NULL DEFAULT 'publica'"),
    ("documents",          "visibilidade", "TEXT NOT NULL DEFAULT 'publica'"),
)


async def init_schema(db_path: Path) -> None:
    """Cria tabelas, aplica migrations legadas, garante índices, popula seeds."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as conn:
        for ddl in _TABLES:
            await conn.execute(ddl)
        await _apply_legacy_migrations(conn)
        for stmt in _INDEXES:
            await conn.execute(stmt)
        await _seed_funcoes(conn)
        await _seed_model_pricing(conn)
        await _seed_doc_classes(conn)
        await conn.commit()
    log.info("Schema SQLite pronto em %s", db_path)


async def _seed_funcoes(conn: aiosqlite.Connection) -> None:
    """Insere as funções fixas se ainda não existirem (idempotente via UNIQUE)."""
    ts = now_iso()
    await conn.executemany(
        "INSERT OR IGNORE INTO funcoes (nome, ativo, created_at) VALUES (?, 1, ?)",
        [(nome, ts) for nome in _FUNCOES_SEED],
    )


async def _seed_model_pricing(conn: aiosqlite.Connection) -> None:
    """Insere pricing padrão se ainda não existir (idempotente via PRIMARY KEY)."""
    ts = now_iso()
    await conn.executemany(
        """
        INSERT OR IGNORE INTO model_pricing
            (model, backend, cost_per_1k_input, cost_per_1k_output, currency, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [(m, b, ci, co, cur, ts) for m, b, ci, co, cur in _MODEL_PRICING_SEED],
    )


async def _seed_doc_classes(conn: aiosqlite.Connection) -> None:
    """Catálogo inicial de classes de documento (idempotente via PK)."""
    ts = now_iso()
    await conn.executemany(
        """
        INSERT OR IGNORE INTO doc_classes
            (slug, label, peso, nivel_min_classificar, nivel_min_ler, ativo, created_at)
        VALUES (?, ?, ?, ?, ?, 1, ?)
        """,
        [(s, l, p, nc, nl, ts) for s, l, p, nc, nl in _DOC_CLASSES_SEED],
    )


async def _apply_legacy_migrations(conn: aiosqlite.Connection) -> None:
    for table, col, decl in _LEGACY_MIGRATIONS:
        existing = await _table_columns(conn, table)
        if col not in existing:
            log.info("Migration: ALTER %s ADD COLUMN %s", table, col)
            await conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


async def _table_columns(conn: aiosqlite.Connection, table: str) -> set[str]:
    async with conn.execute(f"PRAGMA table_info({table})") as cur:
        rows = await cur.fetchall()
    return {str(r[1]) for r in rows}
