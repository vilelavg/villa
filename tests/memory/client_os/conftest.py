"""
Fixtures do Client OS.

Usa o fixture `db_session` do conftest.py raiz do projeto (AsyncSession
PostgreSQL em villa_test_db, isolada por teste via savepoint+rollback).

Os testes de `test_state.py` dependem de features PG-only (JSONB,
ON CONFLICT) e nao rodam em SQLite.

Os testes de `test_narrative.py` sao puros (sem DB) e rodam em qualquer
ambiente.
"""

from __future__ import annotations

import pytest_asyncio

from memory.client_os import ClientOS


@pytest_asyncio.fixture
async def sample_client(db_session):
    """
    Cria um Client minimal com slug='test_client_os' e devolve o objeto.

    O modelo `Client` do Villa vive em `core.models` (PK UUID).
    Se houver campos NOT NULL adicionais no projeto, o conftest raiz
    pode estender ou substituir este fixture.
    """
    from core.models import Client  # type: ignore

    client = Client(slug="test_client_os", name="Test Client OS")
    db_session.add(client)
    await db_session.flush()
    return client


@pytest_asyncio.fixture
async def client_os(db_session, sample_client):
    """ClientOS instanciado e pronto para os testes."""
    return await ClientOS.for_slug(db_session, sample_client.slug)
