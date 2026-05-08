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
    project_id          INTEGER REFERENCES projects(id)
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
    # token_usage
    "CREATE INDEX IF NOT EXISTS idx_token_usage_user       ON token_usage(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_token_usage_project    ON token_usage(project_id);",
    "CREATE INDEX IF NOT EXISTS idx_token_usage_model      ON token_usage(model);",
    "CREATE INDEX IF NOT EXISTS idx_token_usage_created    ON token_usage(created_at);",
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
