# database/sqlite_mgr.py

"""
Façade do SQLite — instancia os repos por domínio e delega a API pública.

Os métodos públicos preservam a assinatura original para não quebrar
chamadores em `core/`, `tg/`, `llm/`, `scripts/` e `tests/`. Para código novo,
prefira acessar diretamente `mgr.interactions`, `mgr.settings`, etc.

Tipos (dataclasses) e SQLs vivem em `database.models` / `database.schema`.
Reexportados aqui apenas para retrocompat de imports antigos.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

# Reexports para retrocompat (imports do tipo
# `from database.sqlite_mgr import PipelineStepRow, Interaction, ...`).
from database.models import (  # noqa: F401
    Colaborador,
    DailyTokenRow,
    Empresa,
    Funcao,
    Interaction,
    InteractionChunk,
    Invite,
    ModelPricing,
    PipelineStepRow,
    Project,
    ProjectMember,
    Reminder,
    StatsSnapshot,
    TokenUsageRow,
    TokenUsageSummary,
    User,
    UserSettings,
)
from database.repos import (
    AnotacoesRepo,
    AtividadesRepo,
    ChunksRepo,
    ClimaRepo,
    ColaboradoresRepo,
    CronogramaEtapasRepo,
    EfetivoRepo,
    EmpresasRepo,
    FuncoesRepo,
    InteractionsRepo,
    InvitesRepo,
    MembersRepo,
    ModelPricingRepo,
    PipelineRepo,
    ProjectsRepo,
    RemindersRepo,
    SettingsRepo,
    TokenUsageRepo,
    UsersRepo,
)
from database.schema import init_schema as _init_schema


class SQLiteManager:
    """Wrapper assíncrono em torno do SQLite — metadados, settings e pipeline."""

    def __init__(
        self,
        db_path: Path,
        default_model: str = "gemma:2b",
        default_temperature: float = 0.7,
    ) -> None:
        self._db_path: Path = db_path
        self.interactions = InteractionsRepo(db_path)
        self.settings = SettingsRepo(db_path, default_model, default_temperature)
        self.reminders = RemindersRepo(db_path)
        self.pipeline = PipelineRepo(db_path)
        self.users = UsersRepo(db_path)
        self.projects = ProjectsRepo(db_path)
        self.invites = InvitesRepo(db_path)
        self.members = MembersRepo(db_path)
        self.funcoes = FuncoesRepo(db_path)
        self.empresas = EmpresasRepo(db_path)
        self.colaboradores = ColaboradoresRepo(db_path)
        self.chunks = ChunksRepo(db_path)
        self.token_usage = TokenUsageRepo(db_path)
        self.model_pricing = ModelPricingRepo(db_path)
        # Refundação 2026-05 — entidades de obra
        self.clima = ClimaRepo(db_path)
        self.efetivo = EfetivoRepo(db_path)
        self.atividades = AtividadesRepo(db_path)
        self.anotacoes = AnotacoesRepo(db_path)
        self.cronograma = CronogramaEtapasRepo(db_path)

    async def init_schema(self) -> None:
        await _init_schema(self._db_path)

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
        project_id: int | None = None,
    ) -> int:
        return await self.interactions.insert(
            user_id=user_id, chat_id=chat_id,
            user_message=user_message, bot_response=bot_response,
            tags=tags, intent=intent,
            model_used=model_used, temperature=temperature,
            prompt_tokens=prompt_tokens, response_tokens=response_tokens,
            total_duration_ms=total_duration_ms, prompt_used=prompt_used,
            positive_ids=positive_ids, negative_ids=negative_ids,
            retrieved_count=retrieved_count,
            embedding_model=embedding_model, embedding_dim=embedding_dim,
            tool_calls=tool_calls,
            media_path=media_path, media_type=media_type,
            error=error, run_id=run_id,
            project_id=project_id,
        )

    async def update_score(self, interaction_id: int, score: int) -> None:
        await self.interactions.update_score(interaction_id, score)

    async def set_correction(self, interaction_id: int, text: str) -> None:
        await self.interactions.set_correction(interaction_id, text)

    async def fetch_by_ids(
        self,
        ids: Iterable[int],
        *,
        requester_user_id: int | None,
        project_id: int | None = None,
    ) -> list[Interaction]:
        return await self.interactions.fetch_by_ids(
            ids,
            requester_user_id=requester_user_id,
            project_id=project_id,
        )

    async def list_user_history(
        self,
        user_id: int,
        limit: int = 10,
        *,
        project_id: int | None = None,
    ) -> list[Interaction]:
        return await self.interactions.list_user_history(
            user_id, limit, project_id=project_id
        )

    async def stats(self, faiss_indexed: int) -> StatsSnapshot:
        return await self.interactions.stats(faiss_indexed)

    # ---------- user_settings ----------

    async def get_user_settings(self, user_id: int) -> UserSettings:
        return await self.settings.get(user_id)

    async def set_user_model(self, user_id: int, model: str) -> None:
        await self.settings.set_model(user_id, model)

    async def set_user_temperature(self, user_id: int, temperature: float) -> None:
        await self.settings.set_temperature(user_id, temperature)

    async def reset_user_settings(self, user_id: int) -> UserSettings:
        return await self.settings.reset(user_id)

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
        return await self.reminders.insert(
            user_id=user_id, chat_id=chat_id, text=text,
            scheduled_for=scheduled_for,
            source_interaction_id=source_interaction_id,
        )

    async def mark_reminder_sent(self, reminder_id: int) -> None:
        await self.reminders.mark_sent(reminder_id)

    async def cancel_reminder(self, reminder_id: int, *, user_id: int) -> bool:
        return await self.reminders.cancel(reminder_id, user_id=user_id)

    async def list_pending_reminders(self) -> list[Reminder]:
        return await self.reminders.list_pending()

    async def list_user_reminders(
        self, user_id: int, *, only_pending: bool = True, limit: int = 20
    ) -> list[Reminder]:
        return await self.reminders.list_for_user(
            user_id, only_pending=only_pending, limit=limit
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
        await self.pipeline.save_steps(
            run_id=run_id, user_id=user_id, chat_id=chat_id,
            interaction_id=interaction_id, steps=steps,
        )

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
        return await self.users.register(
            telegram_id=telegram_id, name=name, email=email,
            role=role, invited_by=invited_by,
        )

    async def get_user_by_telegram_id(self, telegram_id: int) -> User | None:
        return await self.users.get_by_telegram_id(telegram_id)

    async def get_user_by_id(self, user_id: int) -> User | None:
        return await self.users.get_by_id(user_id)

    async def update_user_role(self, user_id: int, role: str) -> None:
        await self.users.update_role(user_id, role)

    async def update_user_status(self, user_id: int, status: str) -> None:
        await self.users.update_status(user_id, status)

    async def list_users(
        self, *, role: str | None = None, status: str = "active", limit: int = 100
    ) -> list[User]:
        return await self.users.list(role=role, status=status, limit=limit)
