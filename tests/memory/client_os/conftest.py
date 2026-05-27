"""
Fixtures do Client OS.

Assume que o conftest.py raiz do projeto já fornece um fixture `db`
do tipo AsyncSession apontado a um banco PostgreSQL de teste (o padrão
Villa). Os testes de `test_state.py` dependem de features PG-only
(JSONB, ON CONFLICT) e não rodam em SQLite.

Os testes de `test_narrative.py` são puros (sem DB) e rodam em qualquer
ambiente.
"""
from __future__ import annotations

import pytest_asyncio

from memory.client_os import ClientOS


@pytest_asyncio.fixture
async def sample_client(db):
    """
    Cria um Client minimal com slug='test_client_os' e devolve o objeto.

    O modelo `Client` do Villa vive em `core.models` (PK UUID).
    Se houver campos NOT NULL adicionais no projeto, o conftest raiz
    pode estender ou substituir este fixture.
    """
    from core.models import Client  # type: ignore

    client = Client(slug="test_client_os", name="Test Client OS")
    db.add(client)
    await db.flush()
    return client


@pytest_asyncio.fixture
async def client_os(db, sample_client):
    """ClientOS instanciado e pronto para os testes."""
    return await ClientOS.for_slug(db, sample_client.slug)
