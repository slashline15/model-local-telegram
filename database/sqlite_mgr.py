from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

from core.exceptions import StorageError
from core.logger import get_logger

log = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class Interaction:
    id: int
    user_id: int
    chat_id: int | None
    user_message: str
    bot_response: str
    timestamp: str
    media_path: str | None
    media_type: str | None
    score: int | None
    tags: list[str]
    intent: str | None
    model_used: str | None
    temperature: float | None
    prompt_tokens: int | None
    response_tokens: int | None
    total_duration_ms: int | None
    prompt_used: str | None
    positive_ids: list[int]
    negative_ids: list[int]
    retrieved_count: int | None
    embedding_model: str | None
    embedding_dim: int | None
    tool_calls: list[dict[str, Any]]
    error: str | None
    run_id: str | None


@dataclass(slots=True, frozen=True)
class UserSettings:
    user_id: int
    current_model: str
    temperature: float
    created_at: str
    updated_at: str


@dataclass(slots=True, frozen=True)
class PipelineStepRow:
    run_id: str
    step_index: int
    step_name: str
    status: str
    duration_ms: int
    details: dict[str, Any]
    error: str | None


@dataclass(slots=True, frozen=True)
class Reminder:
    id: int
    user_id: int
    chat_id: int
    text: str
    scheduled_for: str  # ISO local
    status: str  # pending | sent | cancelled
    source_interaction_id: int | None
    created_at: str
    sent_at: str | None


@dataclass(slots=True, frozen=True)
class User:
    id: int
    telegram_id: int
    name: str
    email: str | None
    role: str          # superadmin | admin | engineer | supervisor | worker | client
    status: str        # active | inactive | banned
    invited_by: int | None
    created_at: str
    updated_at: str


@dataclass(slots=True, frozen=True)
class StatsSnapshot:
    total_interactions: int
    rated: int
    positives: int
    negatives: int
    distinct_users: int
    distinct_intents: int
    avg_latency_ms: float | None
    last_run_id: str | None
    faiss_indexed: int


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
    run_id              TEXT
);
"""

_USER_SETTINGS_BASE: str = """
CREATE TABLE IF NOT EXISTS user_settings (
    user_id       INTEGER PRIMARY KEY,
    current_model TEXT    NOT NULL DEFAULT 'gemma:2b',
    temperature   REAL    NOT NULL DEFAULT 0.7,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
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
)

# (tabela, coluna, declaração) — aplicado se faltar a coluna em DBs antigos.
_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
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
    ("user_settings", "created_at",        "TEXT NOT NULL DEFAULT ''"),
    ("user_settings", "updated_at",        "TEXT NOT NULL DEFAULT ''"),
)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


class SQLiteManager:
    """Wrapper assíncrono em torno do SQLite — metadados, settings e pipeline."""

    def __init__(
        self,
        db_path: Path,
        default_model: str = "gemma:2b",
        default_temperature: float = 0.7,
    ) -> None:
        self._db_path: Path = db_path
        self._default_model: str = default_model
        self._default_temperature: float = default_temperature

    async def init_schema(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as conn:
            # 1) tabelas base (CREATE TABLE IF NOT EXISTS — não recria existentes)
            await conn.execute(_INTERACTIONS_BASE)
            await conn.execute(_USER_SETTINGS_BASE)
            await conn.execute(_PIPELINE_STEPS_BASE)
            await conn.execute(_REMINDERS_BASE)
            await conn.execute(_USERS_BASE)
            # 2) migrations ANTES dos índices: alguns índices referenciam
            #    colunas adicionadas em versões posteriores do schema.
            await self._apply_migrations(conn)
            # 3) índices (já com todas as colunas garantidas)
            for stmt in _INDEXES:
                await conn.execute(stmt)
            await conn.commit()
        log.info("Schema SQLite pronto em %s", self._db_path)

    async def _apply_migrations(self, conn: aiosqlite.Connection) -> None:
        for table, col, decl in _MIGRATIONS:
            existing = await self._table_columns(conn, table)
            if col not in existing:
                log.info("Migration: ALTER %s ADD COLUMN %s", table, col)
                await conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    @staticmethod
    async def _table_columns(conn: aiosqlite.Connection, table: str) -> set[str]:
        async with conn.execute(f"PRAGMA table_info({table})") as cur:
            rows = await cur.fetchall()
        return {str(r[1]) for r in rows}

    # ---------- interactions ----------

    async def insert_interaction(
        self,
        *,
        user_id: int,
        chat_id: int | None,
        user_message: str,
        bot_response: str,
        tags: list[str],
        intent: str | None,
        model_used: str | None,
        temperature: float | None,
        prompt_tokens: int | None,
        response_tokens: int | None,
        total_duration_ms: int | None,
        prompt_used: str | None,
        positive_ids: list[int],
        negative_ids: list[int],
        retrieved_count: int | None,
        embedding_model: str | None,
        embedding_dim: int | None,
        tool_calls: list[dict[str, Any]],
        media_path: str | None,
        media_type: str | None,
        error: str | None,
        run_id: str | None,
    ) -> int:
        try:
            async with aiosqlite.connect(self._db_path) as conn:
                cur = await conn.execute(
                    """
                    INSERT INTO interactions (
                        user_id, chat_id, user_message, bot_response, timestamp,
                        media_path, media_type, score, tags, intent, model_used,
                        temperature, prompt_tokens, response_tokens,
                        total_duration_ms, prompt_used, positive_ids, negative_ids,
                        retrieved_count, embedding_model, embedding_dim, tool_calls,
                        error, run_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id, chat_id, user_message, bot_response, _now_iso(),
                        media_path, media_type,
                        json.dumps(tags, ensure_ascii=False),
                        intent, model_used, temperature,
                        prompt_tokens, response_tokens, total_duration_ms,
                        prompt_used,
                        json.dumps(positive_ids),
                        json.dumps(negative_ids),
                        retrieved_count, embedding_model, embedding_dim,
                        json.dumps(tool_calls, ensure_ascii=False, default=str),
                        error, run_id,
                    ),
                )
                await conn.commit()
                if cur.lastrowid is None:
                    raise StorageError("INSERT não retornou lastrowid.")
                return int(cur.lastrowid)
        except aiosqlite.Error as exc:
            raise StorageError(f"Falha ao inserir interação: {exc}") from exc

    async def update_score(self, interaction_id: int, score: int) -> None:
        if not 1 <= score <= 5:
            raise StorageError(f"Score inválido (esperado 1..5): {score}")
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "UPDATE interactions SET score = ? WHERE id = ?",
                (score, interaction_id),
            )
            await conn.commit()

    async def fetch_by_ids(self, ids: Iterable[int]) -> list[Interaction]:
        ids_list = list(ids)
        if not ids_list:
            return []
        placeholders = ",".join("?" for _ in ids_list)
        query = f"SELECT * FROM interactions WHERE id IN ({placeholders})"
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(query, ids_list) as cur:
                rows = await cur.fetchall()
        return [self._row_to_interaction(row) for row in rows]

    async def list_user_history(
        self, user_id: int, limit: int = 10
    ) -> list[Interaction]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM interactions WHERE user_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (user_id, int(limit)),
            ) as cur:
                rows = await cur.fetchall()
        return [self._row_to_interaction(r) for r in rows]

    async def stats(self, faiss_indexed: int) -> StatsSnapshot:
        async with aiosqlite.connect(self._db_path) as conn:
            async with conn.execute(
                """
                SELECT
                    COUNT(*),
                    SUM(CASE WHEN score IS NOT NULL THEN 1 ELSE 0 END),
                    SUM(CASE WHEN score >= 4 THEN 1 ELSE 0 END),
                    SUM(CASE WHEN score <= 2 AND score IS NOT NULL THEN 1 ELSE 0 END),
                    COUNT(DISTINCT user_id),
                    COUNT(DISTINCT intent),
                    AVG(total_duration_ms)
                FROM interactions
                """
            ) as cur:
                row = await cur.fetchone()
            async with conn.execute(
                "SELECT run_id FROM interactions WHERE run_id IS NOT NULL "
                "ORDER BY id DESC LIMIT 1"
            ) as cur:
                last = await cur.fetchone()

        total, rated, pos, neg, users, intents, avg_lat = row or (0, 0, 0, 0, 0, 0, None)
        return StatsSnapshot(
            total_interactions=int(total or 0),
            rated=int(rated or 0),
            positives=int(pos or 0),
            negatives=int(neg or 0),
            distinct_users=int(users or 0),
            distinct_intents=int(intents or 0),
            avg_latency_ms=float(avg_lat) if avg_lat is not None else None,
            last_run_id=str(last[0]) if last else None,
            faiss_indexed=int(faiss_indexed),
        )

    # ---------- user_settings ----------

    async def get_user_settings(self, user_id: int) -> UserSettings:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT user_id, current_model, temperature, created_at, updated_at "
                "FROM user_settings WHERE user_id = ?",
                (user_id,),
            ) as cur:
                row = await cur.fetchone()

        if row is None:
            now = _now_iso()
            settings = UserSettings(
                user_id=user_id,
                current_model=self._default_model,
                temperature=self._default_temperature,
                created_at=now,
                updated_at=now,
            )
            await self._upsert_settings(settings)
            return settings

        return UserSettings(
            user_id=int(row["user_id"]),
            current_model=str(row["current_model"]),
            temperature=float(row["temperature"]),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )

    async def set_user_model(self, user_id: int, model: str) -> None:
        cur = await self.get_user_settings(user_id)
        await self._upsert_settings(
            UserSettings(
                user_id=user_id,
                current_model=model,
                temperature=cur.temperature,
                created_at=cur.created_at or _now_iso(),
                updated_at=_now_iso(),
            )
        )

    async def set_user_temperature(self, user_id: int, temperature: float) -> None:
        cur = await self.get_user_settings(user_id)
        await self._upsert_settings(
            UserSettings(
                user_id=user_id,
                current_model=cur.current_model,
                temperature=float(temperature),
                created_at=cur.created_at or _now_iso(),
                updated_at=_now_iso(),
            )
        )

    async def reset_user_settings(self, user_id: int) -> UserSettings:
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("DELETE FROM user_settings WHERE user_id = ?", (user_id,))
            await conn.commit()
        return await self.get_user_settings(user_id)

    async def _upsert_settings(self, s: UserSettings) -> None:
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                """
                INSERT INTO user_settings (user_id, current_model, temperature, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    current_model = excluded.current_model,
                    temperature   = excluded.temperature,
                    updated_at    = excluded.updated_at
                """,
                (s.user_id, s.current_model, s.temperature, s.created_at, s.updated_at),
            )
            await conn.commit()

    # ---------- reminders ----------

    async def insert_reminder(
        self,
        *,
        user_id: int,
        chat_id: int,
        text: str,
        scheduled_for: str,
        source_interaction_id: int | None = None,
    ) -> int:
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                """
                INSERT INTO reminders
                    (user_id, chat_id, text, scheduled_for, status,
                     source_interaction_id, created_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (user_id, chat_id, text, scheduled_for,
                 source_interaction_id, _now_iso()),
            )
            await conn.commit()
            if cur.lastrowid is None:
                raise StorageError("INSERT em reminders não retornou lastrowid.")
            return int(cur.lastrowid)

    async def mark_reminder_sent(self, reminder_id: int) -> None:
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "UPDATE reminders SET status='sent', sent_at=? WHERE id=?",
                (_now_iso(), reminder_id),
            )
            await conn.commit()

    async def cancel_reminder(self, reminder_id: int, *, user_id: int) -> bool:
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                "UPDATE reminders SET status='cancelled' "
                "WHERE id=? AND user_id=? AND status='pending'",
                (reminder_id, user_id),
            )
            await conn.commit()
            return (cur.rowcount or 0) > 0

    async def list_pending_reminders(self) -> list[Reminder]:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM reminders WHERE status='pending' "
                "ORDER BY scheduled_for ASC"
            ) as cur:
                rows = await cur.fetchall()
        return [self._row_to_reminder(r) for r in rows]

    async def list_user_reminders(
        self, user_id: int, *, only_pending: bool = True, limit: int = 20
    ) -> list[Reminder]:
        clause = "WHERE user_id=?" + (" AND status='pending'" if only_pending else "")
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                f"SELECT * FROM reminders {clause} "
                f"ORDER BY scheduled_for ASC LIMIT ?",
                (user_id, int(limit)),
            ) as cur:
                rows = await cur.fetchall()
        return [self._row_to_reminder(r) for r in rows]

    @staticmethod
    def _row_to_reminder(row: aiosqlite.Row) -> Reminder:
        return Reminder(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            chat_id=int(row["chat_id"]),
            text=str(row["text"]),
            scheduled_for=str(row["scheduled_for"]),
            status=str(row["status"]),
            source_interaction_id=row["source_interaction_id"],
            created_at=str(row["created_at"]),
            sent_at=row["sent_at"],
        )

    # ---------- pipeline_steps ----------

    async def save_pipeline_steps(
        self,
        *,
        run_id: str,
        user_id: int,
        chat_id: int | None,
        interaction_id: int | None,
        steps: list[PipelineStepRow],
    ) -> None:
        if not steps:
            return
        ts = _now_iso()
        rows = [
            (
                run_id, interaction_id, user_id, chat_id,
                s.step_index, s.step_name, s.status, int(s.duration_ms),
                json.dumps(s.details, ensure_ascii=False, default=str),
                s.error, ts,
            )
            for s in steps
        ]
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.executemany(
                """
                INSERT INTO pipeline_steps
                    (run_id, interaction_id, user_id, chat_id, step_index,
                     step_name, status, duration_ms, details, error, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            await conn.commit()

    # ---------- users ----------

    async def register_user(
        self,
        *,
        telegram_id: int,
        name: str,
        email: str | None = None,
        role: str = "worker",
        invited_by: int | None = None,
    ) -> User:
        now = _now_iso()
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute(
                """
                INSERT INTO users (telegram_id, name, email, role, status, invited_by,
                                   created_at, updated_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    name       = excluded.name,
                    updated_at = excluded.updated_at
                """,
                (telegram_id, name, email, role, invited_by, now, now),
            )
            await conn.commit()
            row_id = cur.lastrowid
        user = await self.get_user_by_telegram_id(telegram_id)
        if user is None:
            raise StorageError(f"Falha ao registrar usuário telegram_id={telegram_id}")
        return user

    async def get_user_by_telegram_id(self, telegram_id: int) -> User | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
            ) as cur:
                row = await cur.fetchone()
        return self._row_to_user(row) if row else None

    async def get_user_by_id(self, user_id: int) -> User | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ) as cur:
                row = await cur.fetchone()
        return self._row_to_user(row) if row else None

    async def update_user_role(self, user_id: int, role: str) -> None:
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "UPDATE users SET role = ?, updated_at = ? WHERE id = ?",
                (role, _now_iso(), user_id),
            )
            await conn.commit()

    async def update_user_status(self, user_id: int, status: str) -> None:
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "UPDATE users SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now_iso(), user_id),
            )
            await conn.commit()

    async def list_users(
        self, *, role: str | None = None, status: str = "active", limit: int = 100
    ) -> list[User]:
        if role:
            query = "SELECT * FROM users WHERE status = ? AND role = ? LIMIT ?"
            params: tuple = (status, role, limit)
        else:
            query = "SELECT * FROM users WHERE status = ? LIMIT ?"
            params = (status, limit)
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(query, params) as cur:
                rows = await cur.fetchall()
        return [self._row_to_user(r) for r in rows]

    @staticmethod
    def _row_to_user(row: aiosqlite.Row) -> User:
        return User(
            id=int(row["id"]),
            telegram_id=int(row["telegram_id"]),
            name=str(row["name"]),
            email=row["email"],
            role=str(row["role"]),
            status=str(row["status"]),
            invited_by=row["invited_by"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    # ---------- helpers ----------

    @staticmethod
    def _row_to_interaction(row: aiosqlite.Row) -> Interaction:
        def _json_list(key: str) -> list[Any]:
            raw = row[key] if key in row.keys() else None
            if not raw:
                return []
            try:
                val = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                return []
            return val if isinstance(val, list) else []

        def _opt(key: str) -> Any:
            return row[key] if key in row.keys() else None

        return Interaction(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            chat_id=_opt("chat_id"),
            user_message=str(row["user_message"]),
            bot_response=str(row["bot_response"]),
            timestamp=str(row["timestamp"]),
            media_path=_opt("media_path"),
            media_type=_opt("media_type"),
            score=_opt("score"),
            tags=_json_list("tags"),
            intent=_opt("intent"),
            model_used=_opt("model_used"),
            temperature=_opt("temperature"),
            prompt_tokens=_opt("prompt_tokens"),
            response_tokens=_opt("response_tokens"),
            total_duration_ms=_opt("total_duration_ms"),
            prompt_used=_opt("prompt_used"),
            positive_ids=[int(x) for x in _json_list("positive_ids")],
            negative_ids=[int(x) for x in _json_list("negative_ids")],
            retrieved_count=_opt("retrieved_count"),
            embedding_model=_opt("embedding_model"),
            embedding_dim=_opt("embedding_dim"),
            tool_calls=[x for x in _json_list("tool_calls") if isinstance(x, dict)],
            error=_opt("error"),
            run_id=_opt("run_id"),
        )
