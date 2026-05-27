# ═══════════════════════════════════════════════════════════════
# VILLA — tests/conftest.py
# Fixtures globais compartilhadas por toda a suíte de testes.
#
# Estratégia de isolamento:
#   - Cada teste recebe uma transação que é revertida ao final
#     (rollback) — banco sempre limpo, sem DELETE manual.
#   - Todas as chamadas a APIs externas são interceptadas por mocks.
#     Nenhum teste jamais faz requisição HTTP real.
#   - Banco de testes: villa_test_db na porta 5433 (docker-compose.test.yml)
#
# CORREÇÃO v2 (2026-05):
#   - create_test_schema não faz mais downgrade "base" no teardown.
#     O downgrade destruía o schema do banco de testes e, antes da
#     correção do env.py, executava contra villa_db (banco principal).
#     Agora o teardown apenas desconecta — o schema persiste entre
#     rodadas (idempotente via "upgrade head").
# ═══════════════════════════════════════════════════════════════

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# ── URL do banco de testes ───────────────────────────────────────────────────
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://villa:villa_dev_2026@localhost:5433/villa_test_db",
)

TEST_REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://localhost:6380/1")


# ── Engine dedicada ao ambiente de testes ───────────────────────────────────
@pytest.fixture(scope="session")
def event_loop_policy():
    """Política de event loop para a sessão — evita DeprecationWarning no Python 3.12."""
    import asyncio

    return asyncio.DefaultEventLoopPolicy()


@pytest_asyncio.fixture(scope="session")
async def engine():
    _engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        pool_size=5,
        max_overflow=10,
    )
    yield _engine
    await _engine.dispose()


# ── Criação do schema de testes ─────────────────────────────────────────────
# CORREÇÃO: não faz mais downgrade no teardown.
# Motivo: downgrade "base" destruía o schema. Se o env.py não respeitava a
# URL injetada (bug corrigido em env.py v2), o downgrade batia no banco
# principal. Mesmo com env.py corrigido, destruir e recriar o schema a
# cada rodada é lento e desnecessário — upgrade head é idempotente.
@pytest_asyncio.fixture(scope="session")
async def create_test_schema(engine):
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config("alembic.ini")
    # Injeta URL do banco de testes — env.py v2 respeita esta injeção
    alembic_cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)

    # Aplica migrations pendentes (idempotente — não faz nada se já está no head)
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
    yield
    # SEM downgrade no teardown — schema persiste entre rodadas
    # Para resetar manualmente: docker compose exec postgres psql -U villa -c "DROP DATABASE villa_test_db"


# ── Sessão de banco isolada por teste (rollback automático) ─────────────────
@pytest_asyncio.fixture
async def db_session(engine, create_test_schema) -> AsyncGenerator[AsyncSession, None]:
    async with engine.connect() as conn:
        await conn.begin()
        await conn.begin_nested()

        async_session_factory = async_sessionmaker(
            bind=conn,
            class_=AsyncSession,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

        async with async_session_factory() as session:
            yield session

        if conn.in_transaction():
            await conn.rollback()


# ── Override da sessão de DB na aplicação ───────────────────────────────────
@pytest_asyncio.fixture
async def override_db(db_session: AsyncSession):
    from core.database import get_session

    async def _get_test_session():
        yield db_session

    from core.main import app

    app.dependency_overrides[get_session] = _get_test_session
    yield
    app.dependency_overrides.clear()


# ── Cliente HTTP para testes de API ─────────────────────────────────────────
@pytest_asyncio.fixture
async def client(override_db) -> AsyncGenerator[httpx.AsyncClient, None]:
    from core.main import app

    async with httpx.AsyncClient(
        app=app,
        base_url="http://test",
        headers={"Content-Type": "application/json"},
    ) as ac:
        yield ac


# ── Cliente HTTP autenticado ─────────────────────────────────────────────────
@pytest_asyncio.fixture
async def auth_client(client: httpx.AsyncClient) -> httpx.AsyncClient:
    from security.auth import create_access_token

    token = create_access_token(data={"sub": "caio@webxp.com.br", "role": "admin"})
    client.headers["Authorization"] = f"Bearer {token}"
    return client


# ══════════════════════════════════════════════════════════════════════════════
# MOCKS DE APIs EXTERNAS
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_claude() -> Generator[AsyncMock, None, None]:
    default_response = MagicMock()
    default_response.content = [MagicMock(text="Resposta padrão de teste do Claude.")]
    default_response.usage = MagicMock(input_tokens=100, output_tokens=50)

    with patch("integrations.anthropic_client.claude", new_callable=AsyncMock) as mock:
        mock.return_value = default_response
        yield mock


@pytest.fixture
def mock_kommo() -> Generator[MagicMock, None, None]:
    kommo_response = {
        "leads": [
            {
                "id": 123456,
                "name": "Lead Teste",
                "status_id": 1,
                "pipeline_id": 1,
                "created_at": 1700000000,
            }
        ],
        "_page": 1,
        "_links": {"self": {"href": "/api/v4/leads"}},
    }
    with patch("integrations.kommo.KommoClient._request", new_callable=AsyncMock) as mock:
        mock.return_value = kommo_response
        yield mock


@pytest.fixture
def mock_meta() -> Generator[MagicMock, None, None]:
    meta_response = {
        "data": [
            {
                "campaign_id": "120210000000001",
                "impressions": "10000",
                "clicks": "300",
                "spend": "250.00",
                "reach": "8500",
                "date_start": "2026-05-01",
                "date_stop": "2026-05-07",
            }
        ],
        "paging": {"cursors": {"before": "abc", "after": "def"}},
    }
    with patch("integrations.meta.MetaClient._request", new_callable=AsyncMock) as mock:
        mock.return_value = meta_response
        yield mock


@pytest.fixture
def mock_google_calendar() -> Generator[MagicMock, None, None]:
    calendar_response = {
        "items": [
            {
                "id": "event_teste_123",
                "summary": "Consulta - Dr. Linardi",
                "start": {"dateTime": "2026-06-01T10:00:00-03:00"},
                "end": {"dateTime": "2026-06-01T11:00:00-03:00"},
                "status": "confirmed",
            }
        ]
    }
    with patch(
        "integrations.google_calendar.GoogleCalendarClient._request", new_callable=AsyncMock
    ) as mock:
        mock.return_value = calendar_response
        yield mock


@pytest.fixture
def mock_google_drive() -> Generator[MagicMock, None, None]:
    with patch(
        "integrations.google_drive.GoogleDriveClient._request", new_callable=AsyncMock
    ) as mock:
        mock.return_value = {"id": "file_123", "name": "roteiro_teste.docx"}
        yield mock


@pytest.fixture
def mock_whatsapp() -> Generator[MagicMock, None, None]:
    with patch("integrations.whatsapp.WhatsAppClient._request", new_callable=AsyncMock) as mock:
        mock.return_value = {
            "messages": [{"id": "wamid.teste_123"}],
            "messaging_product": "whatsapp",
        }
        yield mock


# ══════════════════════════════════════════════════════════════════════════════
# DADOS DE TESTE
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def client_slug() -> str:
    return "clinica-demo"


@pytest.fixture
def roteiro_payload(client_slug: str) -> dict[str, Any]:
    return {
        "client_slug": client_slug,
        "especialidade": "implante",
        "procedimento": "Implante dentário All-on-4",
        "publico_alvo": "Adultos 45-65 anos, classe B/C",
        "objetivo": "gerar_lead",
        "formato": "reels",
        "tom": "empático",
        "duracao_segundos": 60,
    }


@pytest.fixture
def lead_payload(client_slug: str) -> dict[str, Any]:
    return {
        "client_slug": client_slug,
        "nome": "Maria Silva",
        "telefone": "+5511999887766",
        "especialidade_interesse": "implante",
        "origem": "meta_ads",
        "kommo_lead_id": 123456,
    }


@pytest.fixture
def campanha_data() -> dict[str, Any]:
    return {
        "campaign_id": "120210000000001",
        "campaign_name": "Implante | Remarketing | Mai26",
        "status": "ACTIVE",
        "daily_budget": 5000,
        "impressions": 10000,
        "clicks": 300,
        "spend": 250.00,
        "leads": 12,
        "cpl": 20.83,
        "ctr": 3.0,
        "periodo": "2026-05-01:2026-05-07",
    }


@pytest.fixture
def admin_user() -> dict[str, Any]:
    return {"email": "caio@webxp.com.br", "nome": "Caio", "role": "admin"}


@pytest.fixture
def operador_user() -> dict[str, Any]:
    return {"email": "thais@webxp.com.br", "nome": "Thaís", "role": "operador"}
