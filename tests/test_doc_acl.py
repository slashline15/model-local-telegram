from __future__ import annotations

import pytest

from core.permissions import user_level_in_project
from database.models import ProjectMember, User
from database.repos.chunks import ChunkInsert
from database.sqlite_mgr import SQLiteManager


def _user(role: str = "member") -> User:
    return User(
        id=1, telegram_id=111, name="X", email=None, role=role,
        status="active", invited_by=None, created_at="", updated_at="",
    )


def _member(role: str) -> ProjectMember:
    return ProjectMember(
        project_id=1, user_id=1, role=role,
        can_approve_rdo=False, can_view_financial=False, can_invite=False,
        joined_at="", invite_id=None,
    )


def test_user_level_in_project() -> None:
    assert user_level_in_project(_user("superadmin"), None) == 1
    assert user_level_in_project(_user(), _member("admin")) == 1
    assert user_level_in_project(_user(), _member("co_responsible")) == 2
    assert user_level_in_project(_user(), _member("operator")) == 3
    assert user_level_in_project(_user(), _member("client")) == 3
    assert user_level_in_project(_user(), None) == 3


@pytest.mark.asyncio
async def test_doc_classes_seed_present(sqlite_mgr: SQLiteManager) -> None:
    classes = await sqlite_mgr.doc_classes.list_active()
    slugs = {c.slug for c in classes}
    assert {"contrato", "folha_pgto", "planilha_orcamento", "norma"} <= slugs

    contrato = await sqlite_mgr.doc_classes.get("contrato")
    assert contrato is not None
    assert contrato.peso == 1.5
    assert contrato.nivel_min_classificar == 2

    assert await sqlite_mgr.doc_classes.get("inexistente") is None


@pytest.mark.asyncio
async def test_documents_roundtrip(sqlite_mgr: SQLiteManager) -> None:
    user = await sqlite_mgr.register_user(telegram_id=222, name="Eng", role="admin")
    proj = await sqlite_mgr.projects.create(
        uid="p1", name="Obra Doc", created_by=user.id
    )
    doc_id = await sqlite_mgr.documents.insert(
        uid="d1", project_id=proj.id, doc_class="contrato",
        titulo="Contrato de empreitada", enviado_por=user.id,
    )
    assert doc_id > 0

    doc = await sqlite_mgr.documents.get_by_uid("d1")
    assert doc is not None
    assert doc.doc_class == "contrato"
    assert doc.visibilidade == "publica"

    docs = await sqlite_mgr.documents.list_for_project(proj.id)
    assert [d.id for d in docs] == [doc_id]


@pytest.mark.asyncio
async def test_chunk_document_id_roundtrip(sqlite_mgr: SQLiteManager) -> None:
    iid = await sqlite_mgr.insert_interaction(
        user_id=1, chat_id=1, user_message="[Documento: X]", bot_response="conteúdo",
        tags=["doc"], intent="doc_upload", model_used=None, temperature=None,
        prompt_tokens=None, response_tokens=None, total_duration_ms=None,
        prompt_used=None, positive_ids=[], negative_ids=[],
        retrieved_count=None, embedding_model=None, embedding_dim=None,
        tool_calls=[], media_path=None, media_type="document",
        error=None, run_id=None,
    )
    user = await sqlite_mgr.register_user(telegram_id=333, name="A", role="admin")
    proj = await sqlite_mgr.projects.create(uid="p2", name="Obra", created_by=user.id)
    doc_id = await sqlite_mgr.documents.insert(
        uid="d2", project_id=proj.id, doc_class="norma",
        titulo="NBR", enviado_por=user.id, interaction_id=iid,
    )

    chunk_ids = await sqlite_mgr.chunks.insert_many(
        iid,
        [
            ChunkInsert(
                chunk_idx=0, content="trecho", doc_class="norma",
                weight=1.3, document_id=doc_id,
            )
        ],
    )
    chunks = await sqlite_mgr.chunks.get_by_ids(chunk_ids)
    assert len(chunks) == 1
    assert chunks[0].document_id == doc_id
    assert chunks[0].weight == 1.3


@pytest.mark.asyncio
async def test_fetch_by_ids_ignores_visibility_but_keeps_project_isolation(
    sqlite_mgr: SQLiteManager,
) -> None:
    """ACL simplificado: membro lê tudo da obra; outra obra continua isolada."""
    import aiosqlite

    async def _insert(project_id: int, visibilidade: str) -> int:
        iid = await sqlite_mgr.insert_interaction(
            user_id=10, chat_id=1, user_message="m", bot_response="r",
            tags=[], intent=None, model_used=None, temperature=None,
            prompt_tokens=None, response_tokens=None, total_duration_ms=None,
            prompt_used=None, positive_ids=[], negative_ids=[],
            retrieved_count=None, embedding_model=None, embedding_dim=None,
            tool_calls=[], media_path=None, media_type=None,
            error=None, run_id=None, project_id=project_id,
        )
        async with aiosqlite.connect(sqlite_mgr._db_path) as conn:  # noqa: SLF001
            await conn.execute(
                "UPDATE interactions SET visibilidade = ? WHERE id = ?",
                (visibilidade, iid),
            )
            await conn.commit()
        return iid

    privada_obra1 = await _insert(1, "privada")
    publica_obra2 = await _insert(2, "publica")

    # Outro usuário (id=99) da obra 1 lê a interação privada de user_id=10.
    rows = await sqlite_mgr.fetch_by_ids(
        [privada_obra1, publica_obra2], requester_user_id=99, project_id=1
    )
    assert [r.id for r in rows] == [privada_obra1]
