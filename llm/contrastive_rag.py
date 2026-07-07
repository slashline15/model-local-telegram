from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

_QUERY_EMBED_MAX_CHARS: int = 3000  # espelha _EMBED_INPUT_MAX_CHARS em handlers.py

from core.codes import format_code
from core.logger import get_logger
from database.faiss_mgr import FaissManager
from database.models import GlobalChunk
from database.repos.chunks import ChunksRepo
from database.repos.global_chunks import GlobalChunksRepo
from database.sqlite_mgr import Interaction, SQLiteManager
from llm.ollama_client import OllamaClient
from llm.prompt_templates import (
    FewShotExample,
    GlobalRef,
    build_system_prompt,
    render_contrastive_prompt,
    render_global_refs,
    render_neutral_context,
    render_qa_prompt,
)

# Score mínimo (sim × weight × global_weight) pra referência global entrar no
# prompt — evita poluir com hit fraco quando o peso global do projeto é baixo.
_GLOBAL_MIN_SCORE: float = 0.25


def _to_example(row: Interaction) -> FewShotExample:
    return FewShotExample(
        user_message=row.user_message,
        bot_response=row.bot_response,
        code=format_code(row.id),
        correction=row.correction,
    )


def _with_global_refs(user_prompt: str, refs: list[GlobalChunk]) -> str:
    """Prefixa o bloco de referências globais no prompt (vazio = no-op)."""
    if not refs:
        return user_prompt
    block = render_global_refs(
        [GlobalRef(conteudo=c.conteudo, titulo=c.titulo, source=c.source) for c in refs]
    )
    return f"{block}\n\n{user_prompt}"

log = get_logger(__name__)


@dataclass(slots=True, frozen=True)
class RetrievedHit:
    interaction_id: int
    similarity: float
    score: int | None
    bucket: str  # "positive" | "negative" | "neutral" | "discarded"


@dataclass(slots=True)
class RagBundle:
    """Saída pronta para alimentar o LLM, com tudo que o handler precisa logar."""

    system_prompt: str
    user_prompt: str
    positives: list[Interaction] = field(default_factory=list)
    negatives: list[Interaction] = field(default_factory=list)
    neutral: list[Interaction] = field(default_factory=list)
    history: list[Interaction] = field(default_factory=list)
    hits: list[RetrievedHit] = field(default_factory=list)
    global_refs: list[GlobalChunk] = field(default_factory=list)
    fallback_used: bool = False
    embedding_dim: int = 0
    embedding_model: str | None = None

    @property
    def positive_ids(self) -> list[int]:
        return [p.id for p in self.positives]

    @property
    def negative_ids(self) -> list[int]:
        return [n.id for n in self.negatives]

    @property
    def neutral_ids(self) -> list[int]:
        return [n.id for n in self.neutral]

    @property
    def history_ids(self) -> list[int]:
        return [h.id for h in self.history]


class ContrastiveRAG:
    """Two-Stage Retrieval contrastivo + histórico cronológico + fallback neutro.

    1) Busca histórico cronológico recente do user_id (últimos N turnos).
    2) Top-K chunk_ids em FAISS.
    3) Resolve chunk_id → interaction_id via ChunksRepo; aplica weight.
    4) score_final = similarity * weight (pré-calculado na inserção).
    5) Deduplica por interaction_id (mantém o chunk com maior score_final).
    6) Metadados em SQLite, separa por score:
        - positivos:  score >= positive_threshold (até max_positive)
        - negativos:  score <= negative_threshold (até max_negative)
    7) Se positivos+negativos = 0, usa Top-N como contexto neutro.
    8) Para intent="summarize", joga fora o template contrastivo.
    """

    def __init__(
        self,
        ollama: OllamaClient,
        sqlite: SQLiteManager,
        faiss: FaissManager,
        chunks: ChunksRepo,
        top_k: int = 20,
        max_positive: int = 3,
        max_negative: int = 2,
        max_neutral: int = 3,
        positive_threshold: int = 4,
        negative_threshold: int = 2,
        embedding_model: str | None = None,
        global_faiss: FaissManager | None = None,
        global_chunks: GlobalChunksRepo | None = None,
        max_global: int = 3,
    ) -> None:
        self._ollama = ollama
        self._sqlite = sqlite
        self._faiss = faiss
        self._chunks = chunks
        self._top_k = top_k
        self._max_pos = max_positive
        self._max_neg = max_negative
        self._max_neutral = max_neutral
        self._pos_thr = positive_threshold
        self._neg_thr = negative_threshold
        self._embedding_model = embedding_model
        # Dual RAG — índice global de nicho (None = desligado).
        self._global_faiss = global_faiss
        self._global_chunks = global_chunks
        self._max_global = max_global

    async def build(
        self,
        user_message: str,
        *,
        user_id: int | None = None,
        project_id: int | None = None,
        n_recent_history: int = 0,
        intent: str | None = None,
        now_iso: str | None = None,
        style_directive: str = "",
        obra_context: str | None = None,
        global_weight: float = 0.5,
    ) -> RagBundle:
        system_prompt = build_system_prompt(
            now_iso=now_iso,
            style_directive=style_directive,
            obra_context=obra_context,
        )

        # 1) Histórico cronológico (independente do FAISS).
        history_rows: list[Interaction] = []
        history_examples: list[FewShotExample] = []
        if user_id is not None and n_recent_history > 0:
            # Histórico cronológico filtrado pela obra ativa pra não
            # misturar contexto de obras diferentes do mesmo usuário.
            recent = await self._sqlite.list_user_history(
                user_id, limit=n_recent_history, project_id=project_id
            )
            history_rows = list(reversed(recent))  # mais antigo primeiro
            history_examples = [_to_example(r) for r in history_rows]

        # 2) Para summarize, contrastivo atrapalha — usa só histórico.
        if intent == "summarize":
            log.info(
                "RAG: intent=summarize → histórico-only (%d turno(s)).",
                len(history_examples),
            )
            return RagBundle(
                system_prompt=system_prompt,
                user_prompt=render_qa_prompt(
                    user_message, history=history_examples or None
                ),
                history=history_rows,
                embedding_model=self._embedding_model,
            )

        # 3) Sem índice nenhum com conteúdo → só histórico cronológico.
        has_local = self._faiss.ntotal > 0
        has_global = (
            self._global_faiss is not None
            and self._global_chunks is not None
            and self._global_faiss.ntotal > 0
            and global_weight > 0.0
        )
        if not has_local and not has_global:
            log.info(
                "RAG: índices FAISS vazios — só histórico cronológico (%d).",
                len(history_examples),
            )
            user_prompt = (
                render_qa_prompt(user_message, history=history_examples)
                if history_examples
                else render_contrastive_prompt(user_message, [], [])
            )
            return RagBundle(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                history=history_rows,
                embedding_model=self._embedding_model,
            )

        # 4) Busca semântica — um embed serve pros dois índices.
        query_text = user_message[:_QUERY_EMBED_MAX_CHARS]
        if len(user_message) > _QUERY_EMBED_MAX_CHARS:
            log.debug(
                "RAG: query truncada para embedding %d → %d chars",
                len(user_message), _QUERY_EMBED_MAX_CHARS,
            )
        query_vec: np.ndarray = await self._ollama.embed(query_text)
        embedding_dim = int(query_vec.shape[-1])
        log.debug("RAG: embedding gerado dim=%d", embedding_dim)

        # 4a) Índice global — referências de nicho, sem ACL por design.
        global_refs: list[GlobalChunk] = []
        if has_global:
            global_refs = await self._search_global(query_vec, global_weight)

        raw_hits = (
            await self._faiss.search(query_vec, top_k=self._top_k)
            if has_local
            else []
        )
        if not raw_hits:
            user_prompt = (
                render_qa_prompt(user_message, history=history_examples)
                if history_examples
                else render_contrastive_prompt(user_message, [], [])
            )
            return RagBundle(
                system_prompt=system_prompt,
                user_prompt=_with_global_refs(user_prompt, global_refs),
                history=history_rows,
                global_refs=global_refs,
                embedding_dim=embedding_dim,
                embedding_model=self._embedding_model,
            )

        # 5) Resolve chunk_id → interaction_id + weight.
        chunk_ids = [cid for cid, _ in raw_hits]
        sim_by_chunk: dict[int, float] = {cid: sim for cid, sim in raw_hits}

        chunk_rows = await self._chunks.get_by_ids(chunk_ids)
        chunk_by_id = {c.id: c for c in chunk_rows}

        # score_final = similarity * weight; deduplica por interaction_id.
        best_by_interaction: dict[int, tuple[float, float]] = {}  # iid → (score_final, sim)
        for cid, sim in raw_hits:
            chunk = chunk_by_id.get(cid)
            if chunk is None:
                continue
            iid = chunk.interaction_id
            score_final = sim * chunk.weight
            prev = best_by_interaction.get(iid)
            if prev is None or score_final > prev[0]:
                best_by_interaction[iid] = (score_final, sim)

        # Ordena por score_final decrescente.
        sorted_iids = sorted(
            best_by_interaction.keys(),
            key=lambda iid: best_by_interaction[iid][0],
            reverse=True,
        )

        # Evita duplicar no prompt o que já está no histórico cronológico.
        history_id_set = {r.id for r in history_rows}
        candidate_ids = [iid for iid in sorted_iids if iid not in history_id_set]

        # ACL: só carrega interações públicas ou do próprio user_id.
        # user_id=None aqui = bypass intencional (teste/admin).
        # project_id também filtra: hits do FAISS podem vir de outras obras
        # do mesmo dono — sem esse filtro o prompt vira sopa.
        rows = await self._sqlite.fetch_by_ids(
            candidate_ids,
            requester_user_id=user_id,
            project_id=project_id,
        )
        # Mantém a ordem de score_final.
        row_by_id = {r.id: r for r in rows}
        rows_sorted = [row_by_id[iid] for iid in candidate_ids if iid in row_by_id]

        positives: list[Interaction] = []
        negatives: list[Interaction] = []
        neutral_pool: list[Interaction] = []
        hits: list[RetrievedHit] = []

        for r in rows_sorted:
            score_final, sim = best_by_interaction[r.id]
            if (
                r.score is not None
                and r.score >= self._pos_thr
                and len(positives) < self._max_pos
            ):
                positives.append(r)
                hits.append(RetrievedHit(r.id, score_final, r.score, "positive"))
            elif (
                r.score is not None
                and r.score <= self._neg_thr
                and len(negatives) < self._max_neg
            ):
                negatives.append(r)
                hits.append(RetrievedHit(r.id, score_final, r.score, "negative"))
            else:
                neutral_pool.append(r)
                hits.append(RetrievedHit(r.id, score_final, r.score, "neutral"))

        fallback_used = (not positives) and (not negatives)
        neutral: list[Interaction] = []
        if fallback_used and neutral_pool:
            neutral = neutral_pool[: self._max_neutral]
            user_prompt = render_neutral_context(
                user_message=user_message,
                examples=[_to_example(n) for n in neutral],
                history=history_examples or None,
            )
            log.info(
                "RAG: fallback neutro ativo — %d exemplo(s) sem score usados como contexto.",
                len(neutral),
            )
        else:
            user_prompt = render_contrastive_prompt(
                user_message=user_message,
                positives=[_to_example(p) for p in positives],
                negatives=[_to_example(n) for n in negatives],
                history=history_examples or None,
            )

        log.info(
            "RAG: hits=%d → pos=%d neg=%d neutral=%d hist=%d global=%d (fallback=%s)",
            len(hits),
            len(positives),
            len(negatives),
            len(neutral),
            len(history_rows),
            len(global_refs),
            fallback_used,
        )

        return RagBundle(
            system_prompt=system_prompt,
            user_prompt=_with_global_refs(user_prompt, global_refs),
            positives=positives,
            negatives=negatives,
            neutral=neutral,
            history=history_rows,
            hits=hits,
            global_refs=global_refs,
            fallback_used=fallback_used,
            embedding_dim=embedding_dim,
            embedding_model=self._embedding_model,
        )

    async def _search_global(
        self, query_vec: np.ndarray, global_weight: float
    ) -> list[GlobalChunk]:
        """Top hits da base global — score = sim × chunk.weight × global_weight.

        O peso do projeto modula quantos hits sobrevivem ao corte
        `_GLOBAL_MIN_SCORE`: obra com peso baixo só recebe referência muito
        similar; peso alto deixa a base global falar mais.
        """
        assert self._global_faiss is not None and self._global_chunks is not None
        raw = await self._global_faiss.search(query_vec, top_k=self._top_k)
        if not raw:
            return []
        sim_by_id = {cid: sim for cid, sim in raw}
        rows = await self._global_chunks.get_by_ids(list(sim_by_id.keys()))
        scored = [
            (sim_by_id[c.id] * c.weight * global_weight, c)
            for c in rows
            if c.ativo
        ]
        scored = [(s, c) for s, c in scored if s >= _GLOBAL_MIN_SCORE]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [c for _, c in scored[: self._max_global]]

    async def debug_recall(
        self,
        user_message: str,
        *,
        user_id: int | None = None,
        project_id: int | None = None,
        global_weight: float = 0.5,
    ) -> RagBundle:
        """Mesma lógica de `build`, exposta para o comando /recall.

        Propaga user_id e project_id pros filtros — sem isso /recall vazaria
        interações privadas e mostraria hits de outras obras do mesmo user.
        """
        return await self.build(
            user_message,
            user_id=user_id,
            project_id=project_id,
            global_weight=global_weight,
        )
