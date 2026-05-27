"""
Testes do enriquecimento de system prompt com Client OS no BaseModule.

Cobre os 4 cenarios da integracao Fase 1.B (P1.C):
1. client_slug ausente -> sem enriquecimento (comportamento original)
2. client_slug existe mas Client OS falha -> sem enriquecimento (defensivo)
3. client_slug com narrative valido -> system prompt enriquecido
4. narrative grande -> truncado em CLIENT_OS_NARRATIVE_MAX_CHARS

Estes testes nao tocam no banco. Usam mocks porque o objetivo e validar
o COMPORTAMENTO do enriquecimento, nao a integracao com Client OS real
(ja coberta em tests/memory/client_os/).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.models import ModuleCode
from modules.base import CLIENT_OS_NARRATIVE_MAX_CHARS, BaseModule


pytestmark = pytest.mark.asyncio


class _FakeModule(BaseModule):
    """Modulo concreto minimo apenas para instanciar BaseModule."""

    code = ModuleCode.M01_ROTEIROS
    name = "Fake"
    description = "Modulo de teste"

    async def execute(self, message, db, user=None, client_slug=None, context=None):
        return {"success": True}

    async def can_handle(self, message, context=None):
        return 0.0


@pytest.fixture
def fake_module():
    return _FakeModule()


@pytest.fixture
def fake_db():
    """AsyncSession mockado — _enrich_system_with_client_os nao toca nele
    diretamente, apenas o repassa pra ClientOS.for_slug."""
    return MagicMock()


# ─────────────────────────────────────────────────────────────────────────────
# Cenario 1: sem client_slug → retorna system prompt inalterado
# ─────────────────────────────────────────────────────────────────────────────

class TestNoClientSlug:
    async def test_returns_original_when_slug_is_none(self, fake_module, fake_db):
        original = "Voce eh o Villa."
        result = await fake_module._enrich_system_with_client_os(
            original, fake_db, None
        )
        assert result == original

    async def test_returns_original_when_slug_is_empty_string(
        self, fake_module, fake_db
    ):
        original = "Voce eh o Villa."
        result = await fake_module._enrich_system_with_client_os(
            original, fake_db, ""
        )
        assert result == original


# ─────────────────────────────────────────────────────────────────────────────
# Cenario 2: Client OS falha → retorna system prompt inalterado (defensivo)
# ─────────────────────────────────────────────────────────────────────────────

class TestClientOsFailureIsAbsorbed:
    async def test_client_not_found_returns_original(self, fake_module, fake_db):
        """Cliente nao existe na tabela clients."""
        from memory.client_os import ClientNotFoundError

        original = "Voce eh o Villa."
        with patch("memory.client_os.ClientOS.for_slug", new=AsyncMock(
            side_effect=ClientNotFoundError("not found")
        )):
            result = await fake_module._enrich_system_with_client_os(
                original, fake_db, "nao_existe"
            )
        assert result == original

    async def test_generic_exception_returns_original(self, fake_module, fake_db):
        """Erro generico de banco ou outro."""
        original = "Voce eh o Villa."
        with patch("memory.client_os.ClientOS.for_slug", new=AsyncMock(
            side_effect=RuntimeError("db unavailable")
        )):
            result = await fake_module._enrich_system_with_client_os(
                original, fake_db, "qualquer"
            )
        assert result == original

    async def test_narrative_raises_returns_original(self, fake_module, fake_db):
        """for_slug funciona mas narrative() lanca."""
        original = "Voce eh o Villa."
        cos_mock = MagicMock()
        cos_mock.narrative = AsyncMock(side_effect=RuntimeError("nope"))
        with patch("memory.client_os.ClientOS.for_slug", new=AsyncMock(
            return_value=cos_mock
        )):
            result = await fake_module._enrich_system_with_client_os(
                original, fake_db, "x"
            )
        assert result == original


# ─────────────────────────────────────────────────────────────────────────────
# Cenario 3: narrative valido → system prompt enriquecido
# ─────────────────────────────────────────────────────────────────────────────

class TestNarrativeIsAppended:
    async def test_appends_narrative_with_header(self, fake_module, fake_db):
        original = "Voce eh o Villa."
        narrative = "Cliente X. Especialidade: implantes. CPL atual: R$45."

        cos_mock = MagicMock()
        cos_mock.narrative = AsyncMock(return_value=narrative)
        with patch("memory.client_os.ClientOS.for_slug", new=AsyncMock(
            return_value=cos_mock
        )):
            result = await fake_module._enrich_system_with_client_os(
                original, fake_db, "cliente_x"
            )

        assert result.startswith(original)
        assert "## Contexto do cliente" in result
        assert narrative in result

    async def test_empty_narrative_returns_original(self, fake_module, fake_db):
        original = "Voce eh o Villa."
        cos_mock = MagicMock()
        cos_mock.narrative = AsyncMock(return_value="")
        with patch("memory.client_os.ClientOS.for_slug", new=AsyncMock(
            return_value=cos_mock
        )):
            result = await fake_module._enrich_system_with_client_os(
                original, fake_db, "x"
            )
        assert result == original

    async def test_whitespace_only_narrative_returns_original(
        self, fake_module, fake_db
    ):
        original = "Voce eh o Villa."
        cos_mock = MagicMock()
        cos_mock.narrative = AsyncMock(return_value="   \n  \t  ")
        with patch("memory.client_os.ClientOS.for_slug", new=AsyncMock(
            return_value=cos_mock
        )):
            result = await fake_module._enrich_system_with_client_os(
                original, fake_db, "x"
            )
        assert result == original


# ─────────────────────────────────────────────────────────────────────────────
# Cenario 4: narrative grande → truncado em CLIENT_OS_NARRATIVE_MAX_CHARS
# ─────────────────────────────────────────────────────────────────────────────

class TestNarrativeTruncation:
    async def test_narrative_within_limit_is_not_truncated(
        self, fake_module, fake_db
    ):
        original = "Voce eh o Villa."
        narrative = "x" * (CLIENT_OS_NARRATIVE_MAX_CHARS - 100)

        cos_mock = MagicMock()
        cos_mock.narrative = AsyncMock(return_value=narrative)
        with patch("memory.client_os.ClientOS.for_slug", new=AsyncMock(
            return_value=cos_mock
        )):
            result = await fake_module._enrich_system_with_client_os(
                original, fake_db, "x"
            )

        assert narrative in result
        assert "[...narrative truncado]" not in result

    async def test_narrative_exceeding_limit_is_truncated(
        self, fake_module, fake_db
    ):
        original = "Voce eh o Villa."
        # narrative maior que o limite
        narrative = "x" * (CLIENT_OS_NARRATIVE_MAX_CHARS + 500)

        cos_mock = MagicMock()
        cos_mock.narrative = AsyncMock(return_value=narrative)
        with patch("memory.client_os.ClientOS.for_slug", new=AsyncMock(
            return_value=cos_mock
        )):
            result = await fake_module._enrich_system_with_client_os(
                original, fake_db, "x"
            )

        assert "[...narrative truncado]" in result
        # narrative final menor que original
        # (max chars + marker, mas total << narrative original)
        assert len(result) < len(narrative) + len(original) + 100
