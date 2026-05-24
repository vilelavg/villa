# ═══════════════════════════════════════════════════════════════
# VILLA — tests/conftest.py
# Fixtures globais compartilhadas por toda a suíte de testes.
#
# Estratégia de isolamento:
#   - Cada teste recebe uma transação que é revertida ao final
#     (rollback) — banco sempre limpo, sem DELETE manual.
#   - Todas as chamadas a APIs externas (Anthropic, Kommo, Meta,
#     Google, WhatsApp) são interceptadas por mocks automáticos.
#     Nenhum teste jamais faz uma requisição HTTP real.
#   - Redis usa instância real do docker-compose.test.yml (porta
#     6380). Em CI, o serviço redis-test é provisionado pelo
#     workflow antes dos testes rodarem.
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
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# ── URL do banco de testes ───────────────────────────────────────────────────
# Em CI: injetada pelo GitHub Actions como variável de ambiente.
# Local: definida no .env.test (nunca comitar).
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://villa:villa_dev_2026@localhost:5433/villa_test_db",
)

TEST_REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://localhost:6380/1")


# ── Engine dedicada ao ambiente de testes ───────────────────────────────────
# Criada uma vez por sessão de pytest. Pool mínimo para não desperdiçar
# conexões durante os testes paralelos.
@pytest.fixture(scope="session")
def engine():
    _engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,          # True para debug SQL; False para output limpo
        pool_size=5,
        max_overflow=10,
    )
    yield _engine
    # Encerra o pool ao final de toda a suíte
    asyncio.get_event_loop().run_until_complete(_engine.dispose())


# ── Criação do schema de testes ─────────────────────────────────────────────
# Roda uma única vez por sessão APENAS quando testes de integração precisam
# de banco real. NÃO tem autouse=True — testes unitários usam AsyncMock
# e nunca acionam esta fixture, evitando a dependência do Alembic.
@pytest_asyncio.fixture(scope="session")
async def create_test_schema(engine):
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)

    # Aplica todas as migrations no banco de testes
    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
    yield
    # Ao final da suíte, reverte tudo (banco volta ao estado vazio)
    await asyncio.to_thread(command.downgrade, alembic_cfg, "base")


# ── Sessão de banco isolada por teste (rollback automático) ─────────────────
# Cada teste roda dentro de uma transação aberta que é revertida ao final.
# Isso garante isolamento perfeito sem DELETE de dados entre testes.
# Depende explicitamente de create_test_schema — só testes de integração
# que usam db_session acionam a migration do Alembic.
@pytest_asyncio.fixture
async def db_session(engine, create_test_schema) -> AsyncGenerator[AsyncSession, None]:
    async with engine.connect() as conn:
        await conn.begin()          # abre a transação externa
        await conn.begin_nested()   # savepoint — revertido ao final do teste

        async_session_factory = async_sessionmaker(
            bind=conn,
            class_=AsyncSession,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

        async with async_session_factory() as session:
            yield session

        # Garante rollback mesmo se o teste falhar no meio
        if conn.in_transaction():
            await conn.rollback()


# ── Override da sessão de DB na aplicação ───────────────────────────────────
# Substitui a sessão real do FastAPI pela sessão de teste (com rollback).
# Qualquer endpoint que abre uma sessão de DB recebe a sessão de teste.
@pytest_asyncio.fixture
async def override_db(db_session: AsyncSession):
    from core.database import get_session  # importação lazy — evita circular

    async def _get_test_session():
        yield db_session

    from core.main import app
    app.dependency_overrides[get_session] = _get_test_session
    yield
    app.dependency_overrides.clear()


# ── Cliente HTTP para testes de API (FastAPI) ────────────────────────────────
# Usa httpx.AsyncClient apontando para a app FastAPI diretamente,
# sem precisar subir o servidor. Inclui o override de DB automaticamente.
@pytest_asyncio.fixture
async def client(override_db) -> AsyncGenerator[httpx.AsyncClient, None]:
    from core.main import app

    async with httpx.AsyncClient(
        app=app,
        base_url="http://test",
        headers={"Content-Type": "application/json"},
    ) as ac:
        yield ac


# ── Cliente HTTP autenticado (token de admin) ────────────────────────────────
@pytest_asyncio.fixture
async def auth_client(client: httpx.AsyncClient) -> httpx.AsyncClient:
    from security.auth import create_access_token

    token = create_access_token(
        data={"sub": "caio@webxp.com.br", "role": "admin"}
    )
    client.headers["Authorization"] = f"Bearer {token}"
    return client


# ══════════════════════════════════════════════════════════════════════════════
# MOCKS DE APIs EXTERNAS
# Cada mock é uma fixture que intercepta chamadas HTTP para a API correspondente.
# Os testes nunca fazem chamadas reais — são isolados e repeníveis offline.
# ══════════════════════════════════════════════════════════════════════════════

# ── Mock Anthropic (Claude) ──────────────────────────────────────────────────
# Retorna uma resposta padrão configurável por teste.
# Uso: def test_algo(mock_claude): mock_claude.return_value = "meu output"
@pytest.fixture
def mock_claude() -> Generator[AsyncMock, None, None]:
    default_response = MagicMock()
    default_response.content = [MagicMock(text="Resposta padrão de teste do Claude.")]
    default_response.usage = MagicMock(input_tokens=100, output_tokens=50)

    with patch("integrations.anthropic_client.claude", new_callable=AsyncMock) as mock:
        mock.return_value = default_response
        yield mock


# ── Mock Kommo CRM ───────────────────────────────────────────────────────────
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


# ── Mock Meta Ads API ────────────────────────────────────────────────────────
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


# ── Mock Google Calendar ─────────────────────────────────────────────────────
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
        "integrations.google_calendar.GoogleCalendarClient._request",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = calendar_response
        yield mock


# ── Mock Google Drive ────────────────────────────────────────────────────────
@pytest.fixture
def mock_google_drive() -> Generator[MagicMock, None, None]:
    with patch(
        "integrations.google_drive.GoogleDriveClient._request",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = {"id": "file_123", "name": "roteiro_teste.docx"}
        yield mock


# ── Mock WhatsApp Business API ───────────────────────────────────────────────
@pytest.fixture
def mock_whatsapp() -> Generator[MagicMock, None, None]:
    with patch(
        "integrations.whatsapp.WhatsAppClient._request",
        new_callable=AsyncMock,
    ) as mock:
        mock.return_value = {
            "messages": [{"id": "wamid.teste_123"}],
            "messaging_product": "whatsapp",
        }
        yield mock


# ══════════════════════════════════════════════════════════════════════════════
# DADOS DE TESTE — fixtures com entidades prontas para uso nos testes
# ══════════════════════════════════════════════════════════════════════════════

# ── Slug de cliente de teste ─────────────────────────────────────────────────
@pytest.fixture
def client_slug() -> str:
    return "clinica-demo"


# ── Payload de roteiro válido ─────────────────────────────────────────────────
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


# ── Payload de lead de teste ─────────────────────────────────────────────────
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


# ── Dados de campanha de teste ────────────────────────────────────────────────
@pytest.fixture
def campanha_data() -> dict[str, Any]:
    return {
        "campaign_id": "120210000000001",
        "campaign_name": "Implante | Remarketing | Mai26",
        "status": "ACTIVE",
        "daily_budget": 5000,   # centavos (R$50,00)
        "impressions": 10000,
        "clicks": 300,
        "spend": 250.00,
        "leads": 12,
        "cpl": 20.83,
        "ctr": 3.0,
        "periodo": "2026-05-01:2026-05-07",
    }


# ── Usuário admin de teste ────────────────────────────────────────────────────
@pytest.fixture
def admin_user() -> dict[str, Any]:
    return {
        "email": "caio@webxp.com.br",
        "nome": "Caio",
        "role": "admin",
    }


# ── Usuário operador de teste ─────────────────────────────────────────────────
@pytest.fixture
def operador_user() -> dict[str, Any]:
    return {
        "email": "thais@webxp.com.br",
        "nome": "Thaís",
        "role": "operador",
    }
