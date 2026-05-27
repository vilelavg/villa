"""
Fixtures do Client OS.

Assume que o conftest.py raiz do projeto já fornece um fixture `db`
do tipo AsyncSession apontado a um banco PostgreSQL de teste (o padrão
Villa). Os testes de `test_state.py` dependem de features PG-only
(JSONB, ON CONFLICT) e não rodam em SQLite.

Os testes de `test_narrative.py` são puros (sem DB) e rodam em qualquer
ambiente.
"""

# ⚠ DIVIDA TECNICA — fixture 'db' ausente
# ════════════════════════════════════════════════════════════════════════════
# 40 testes em test_state.py esperam fixture chamada 'db' (ex: 'def test_x(self, db, ...)'),
# mas tests/conftest.py raiz fornece 'db_session', nao 'db'. Resultado: testes
# caem em ERROR com 'fixture "db" not found'.
#
# Alias direto (async def db(db_session): return db_session) causa race condition
# asyncpg ('cannot perform operation: another operation is in progress') porque
# a fixture db_session usa savepoint com escopo function, e tentar referenciar
# a mesma conexao por 2 nomes em testes consecutivos cria conflito.
#
# Solucao real (PR futura):
#   1. Refatorar db_session pra usar pool de conexoes isoladas por teste
#   2. OU renomear db_session -> db em todo o projeto (tests/conftest.py
#      + todos os arquivos de teste que usam 'db_session')
#   3. OU reescrever testes de test_state.py pra usar 'db_session' diretamente
#
# Status atual: 42 testes funcionais (test_narrative.py + alguns isolados),
# 40 testes em ERROR. Validacao da Fase 1.A esta sendo feita por smoke tests
# manuais e RAG end-to-end documentado na PR feat/client-os-phase-1a.
# ════════════════════════════════════════════════════════════════════════════

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
