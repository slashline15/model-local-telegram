# database/models.py

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
    correction: str | None = None  # texto do usuário explicando o que o modelo errou
    visibilidade: str = "publica"  # publica | privada — controle de leitura cruzada


@dataclass(slots=True, frozen=True)
class UserSettings:
    user_id: int
    current_model: str
    temperature: float
    current_project_id: int | None
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
class Project:
    id: int
    uid: str
    name: str
    address: str | None
    type: str | None
    status: str          # active | paused | done | archived
    start_date: str | None
    end_date: str | None
    created_by: int
    admin_id: int        # único admin da obra; quem aprova RDOs
    created_at: str
    # Peso do índice global na busca dual (0.0 = ignora global, 1.0 = paridade).
    global_rag_weight: float = 0.5


@dataclass(slots=True, frozen=True)
class Invite:
    id: int
    uid: str
    token: str
    project_id: int | None
    role: str
    created_by: int
    used_by: int | None
    expires_at: str | None
    used_at: str | None
    created_at: str


@dataclass(slots=True, frozen=True)
class ProjectMember:
    project_id: int
    user_id: int
    role: str
    can_approve_rdo: bool
    can_view_financial: bool
    can_invite: bool
    joined_at: str
    invite_id: int | None


@dataclass(slots=True, frozen=True)
class Funcao:
    id: int
    nome: str
    ativo: bool
    created_at: str


@dataclass(slots=True, frozen=True)
class Empresa:
    id: int
    uid: str
    project_id: int
    nome: str
    cnpj: str | None
    tipo: str          # 'own' | 'third_party'
    ativo: bool
    created_by: int
    created_at: str
    fornecedor_id: int | None = None  # FK opcional pro catálogo global


@dataclass(slots=True, frozen=True)
class Colaborador:
    id: int
    uid: str
    project_id: int
    empresa_id: int
    funcao_id: int | None
    nome: str
    apelido: str | None
    ativo: bool
    created_by: int
    created_at: str


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


@dataclass(slots=True, frozen=True)
class InteractionChunk:
    id: int
    interaction_id: int
    chunk_idx: int
    content: str
    doc_class: str
    weight: float
    created_at: str
    document_id: int | None = None  # preenchido quando o chunk veio de /doc


@dataclass(slots=True, frozen=True)
class TokenUsageRow:
    id: int
    run_id: str
    interaction_id: int | None
    user_id: int
    project_id: int | None
    model: str
    backend: str
    operation: str
    prompt_tokens: int
    response_tokens: int
    total_tokens: int
    duration_ms: int
    quantity_secondary: float
    created_at: str


@dataclass(slots=True, frozen=True)
class TokenUsageSummary:
    model: str
    backend: str
    total_prompt: int
    total_response: int
    total_tokens: int
    total_duration_ms: int
    cost_usd: float
    count: int


@dataclass(slots=True, frozen=True)
class DailyTokenRow:
    date: str
    total_tokens: int
    cost_usd: float


@dataclass(slots=True, frozen=True)
class ModelPricing:
    model: str
    backend: str
    cost_per_1k_input: float
    cost_per_1k_output: float
    currency: str
    updated_at: str


# ───── Entidades de obra (refundação 2026-05) ─────

@dataclass(slots=True, frozen=True)
class ClimaDiario:
    id: int
    project_id: int
    dia: str           # YYYY-MM-DD
    condicao: str      # sol | nublado | chuva
    hora_inicio: str | None
    hora_fim: str | None
    interaction_id: int | None
    criado_por: int
    created_at: str


@dataclass(slots=True, frozen=True)
class EfetivoDiario:
    id: int
    project_id: int
    dia: str
    funcao_id: int
    empresa_id: int | None
    qtd: int
    interaction_id: int | None
    criado_por: int
    created_at: str


@dataclass(slots=True, frozen=True)
class Atividade:
    id: int
    project_id: int
    dia: str
    etapa_id: int | None
    responsavel_id: int | None
    estado: str        # concluida | em_andamento | atrasada | impedida
    descricao: str
    interaction_id: int | None
    criado_por: int
    created_at: str


@dataclass(slots=True, frozen=True)
class CronogramaEtapa:
    id: int
    uid: str
    project_id: int
    parent_id: int | None
    etapa: str
    descricao: str | None
    data_prevista_inicio: str | None
    data_prevista_termino: str | None
    ordem: int
    created_at: str


# ───── /doc com ACL (refundação passo 3) ─────

@dataclass(slots=True, frozen=True)
class DocClass:
    slug: str
    label: str
    peso: float
    nivel_min_classificar: int  # 1=N1, 2=N2, 3=N3 — <= libera
    nivel_min_ler: int          # mantido pra auditoria; RAG não filtra mais
    ativo: bool
    created_at: str


@dataclass(slots=True, frozen=True)
class Document:
    id: int
    uid: str
    project_id: int
    doc_class: str
    titulo: str
    arquivo_path: str | None
    arquivo_hash: str | None
    mime: str | None
    enviado_por: int
    interaction_id: int | None
    visibilidade: str  # publica | privada (privada = arquiva sem indexar)
    created_at: str


# ───── Dual RAG + fornecedores globais (2026-06) ─────

@dataclass(slots=True, frozen=True)
class GlobalChunk:
    id: int
    source: str        # 'manual' | 'norma_abnt' | 'glossario' | etc.
    doc_class: str
    titulo: str | None
    conteudo: str
    weight: float
    ativo: bool
    created_at: str


@dataclass(slots=True, frozen=True)
class Fornecedor:
    id: int
    cnpj: str
    razao_social: str
    nome_fantasia: str | None
    tipo_atividade: str | None  # 'servicos' | 'materiais' | 'ambos' | 'outro'
    situacao_rf: str | None
    fonte: str                  # 'manual' | 'receita_federal'
    dados_rf: str | None        # JSON blob completo da Receita
    consultado_em: str | None
    created_at: str


@dataclass(slots=True, frozen=True)
class Anotacao:
    id: int
    project_id: int
    dia: str
    inicio: str | None
    fim: str | None
    natureza: str      # evento | ocorrencia
    atividade_id: int | None
    recurso: str | None
    impacto: str | None
    texto: str
    visibilidade: str  # publica | privada
    interaction_id: int | None
    criado_por: int
    created_at: str
