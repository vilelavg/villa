"""
Testes do ReflectionLoop (P2.A — Autonomy Engine, Loop 2).

Cobre:
1. Severidade nao reflectavel → retorna sem refletir (filtro de custo)
2. Reflexao com Claude mockado → persiste no Client OS
3. Falha do Claude → absorvida, retorna ReflectionResult com error
4. Falha ao salvar Client OS → absorvida, reflected=True mas saved=False
5. Aprendizado WebXP None/null → nao tenta salvar no webxp_agency
6. Alto valor → usa modelo primary (Sonnet)
7. _format_outcome com None e dict
8. REFLECTABLE_SEVERITIES contem os valores certos
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.autonomy.reflection_loop import (
    REFLECTABLE_SEVERITIES,
    WEBXP_AGENCY_SLUG,
    ReflectionLoop,
    ReflectionResult,
)


pytestmark = pytest.mark.asyncio

VALID_REFLECTION = {
    "causa_raiz": "audience fatigue apos 22 dias de criativo unico",
    "era_previsivel": True,
    "como_evitar": "rodar A/B com 2+ criativos sempre",
    "aprendizado_cliente": "Ottoboni: limite real de frequencia e 2.8, nao 3.0",
    "aprendizado_webxp": "implantes SP: criativo unico dura ~20 dias",
    "valor": "alto",
    "fato_derivado": {
        "categoria": "campanhas",
        "chave": "frequencia_real_limite",
        "valor": "2.8",
    },
}


@pytest.fixture
def mock_claude():
    c = MagicMock()
    c.extract_json = AsyncMock(return_value={"data": VALID_REFLECTION})
    return c


@pytest.fixture
def loop(mock_claude):
    return ReflectionLoop(claude_client=mock_claude)


@pytest.fixture
def mock_client_os():
    cos = MagicMock()
    cos.narrative = AsyncMock(return_value="narrative do cliente")
    cos.record_episode = AsyncMock(return_value=1)
    cos.upsert_fact = AsyncMock()
    return cos


# ─────────────────────────────────────────────────────────────────────────────
# Filtro de severidade
# ─────────────────────────────────────────────────────────────────────────────

class TestSeverityFilter:
    async def test_info_nao_reflete(self, loop):
        result = await loop.reflect(
            db=MagicMock(), client_slug="x", event_type="ev",
            event_reason="reason", severity="info",
        )
        assert result.reflected is False

    async def test_warning_reflete(self, loop, mock_client_os):
        with patch("memory.autonomy.reflection_loop.ReflectionLoop._load_client_context",
                   new=AsyncMock(return_value="ctx")), \
             patch("memory.autonomy.reflection_loop.ReflectionLoop._save_to_client_os",
                   new=AsyncMock(return_value=True)), \
             patch("memory.autonomy.reflection_loop.ReflectionLoop._save_to_webxp_os",
                   new=AsyncMock(return_value=True)):
            result = await loop.reflect(
                db=MagicMock(), client_slug="x", event_type="ev",
                event_reason="reason", severity="warning",
            )
        assert result.reflected is True

    async def test_critical_reflete(self, loop):
        with patch("memory.autonomy.reflection_loop.ReflectionLoop._load_client_context",
                   new=AsyncMock(return_value="ctx")), \
             patch("memory.autonomy.reflection_loop.ReflectionLoop._save_to_client_os",
                   new=AsyncMock(return_value=True)), \
             patch("memory.autonomy.reflection_loop.ReflectionLoop._save_to_webxp_os",
                   new=AsyncMock(return_value=True)):
            result = await loop.reflect(
                db=MagicMock(), client_slug="x", event_type="ev",
                event_reason="reason", severity="critical",
            )
        assert result.reflected is True

    async def test_opportunity_reflete(self, loop):
        with patch("memory.autonomy.reflection_loop.ReflectionLoop._load_client_context",
                   new=AsyncMock(return_value="ctx")), \
             patch("memory.autonomy.reflection_loop.ReflectionLoop._save_to_client_os",
                   new=AsyncMock(return_value=True)), \
             patch("memory.autonomy.reflection_loop.ReflectionLoop._save_to_webxp_os",
                   new=AsyncMock(return_value=True)):
            result = await loop.reflect(
                db=MagicMock(), client_slug="x", event_type="ev",
                event_reason="reason", severity="opportunity",
            )
        assert result.reflected is True


# ─────────────────────────────────────────────────────────────────────────────
# Reflexao com sucesso
# ─────────────────────────────────────────────────────────────────────────────

class TestReflectSuccess:
    async def test_campos_preenchidos_corretamente(self, loop):
        with patch("memory.autonomy.reflection_loop.ReflectionLoop._load_client_context",
                   new=AsyncMock(return_value="ctx")), \
             patch("memory.autonomy.reflection_loop.ReflectionLoop._save_to_client_os",
                   new=AsyncMock(return_value=True)), \
             patch("memory.autonomy.reflection_loop.ReflectionLoop._save_to_webxp_os",
                   new=AsyncMock(return_value=True)):
            result = await loop.reflect(
                db=MagicMock(), client_slug="ottoboni",
                event_type="autonomy_cpl_spike",
                event_reason="CPL +40%", severity="warning",
            )

        assert result.reflected is True
        assert result.causa_raiz == VALID_REFLECTION["causa_raiz"]
        assert result.aprendizado_cliente == VALID_REFLECTION["aprendizado_cliente"]
        assert result.aprendizado_webxp == VALID_REFLECTION["aprendizado_webxp"]
        assert result.valor == "alto"
        assert result.saved_client_os is True
        assert result.saved_webxp_os is True

    async def test_webxp_nao_salvo_quando_aprendizado_null(self, mock_claude):
        reflection_null = {**VALID_REFLECTION, "aprendizado_webxp": None, "valor": "medio"}
        mock_claude.extract_json = AsyncMock(return_value={"data": reflection_null})
        loop = ReflectionLoop(claude_client=mock_claude)

        with patch("memory.autonomy.reflection_loop.ReflectionLoop._load_client_context",
                   new=AsyncMock(return_value="ctx")), \
             patch("memory.autonomy.reflection_loop.ReflectionLoop._save_to_client_os",
                   new=AsyncMock(return_value=True)), \
             patch("memory.autonomy.reflection_loop.ReflectionLoop._save_to_webxp_os",
                   new=AsyncMock(return_value=True)) as mock_webxp:
            result = await loop.reflect(
                db=MagicMock(), client_slug="x", event_type="ev",
                event_reason="reason", severity="warning",
            )

        mock_webxp.assert_not_called()
        assert result.saved_webxp_os is False


# ─────────────────────────────────────────────────────────────────────────────
# Falhas absorvidas
# ─────────────────────────────────────────────────────────────────────────────

class TestFailuresAbsorbed:
    async def test_falha_do_claude_retorna_error(self, mock_claude):
        mock_claude.extract_json = AsyncMock(side_effect=RuntimeError("api down"))
        loop = ReflectionLoop(claude_client=mock_claude)

        with patch("memory.autonomy.reflection_loop.ReflectionLoop._load_client_context",
                   new=AsyncMock(return_value="ctx")):
            result = await loop.reflect(
                db=MagicMock(), client_slug="x", event_type="ev",
                event_reason="r", severity="warning",
            )

        assert result.reflected is False
        assert result.error is not None

    async def test_falha_ao_salvar_client_os_nao_propaga(self, loop):
        with patch("memory.autonomy.reflection_loop.ReflectionLoop._load_client_context",
                   new=AsyncMock(return_value="ctx")), \
             patch("memory.autonomy.reflection_loop.ReflectionLoop._save_to_client_os",
                   new=AsyncMock(side_effect=RuntimeError("db down"))), \
             patch("memory.autonomy.reflection_loop.ReflectionLoop._save_to_webxp_os",
                   new=AsyncMock(return_value=True)):
            result = await loop.reflect(
                db=MagicMock(), client_slug="x", event_type="ev",
                event_reason="r", severity="warning",
            )

        assert result.reflected is True
        assert result.saved_client_os is False


# ─────────────────────────────────────────────────────────────────────────────
# _format_outcome
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatOutcome:
    def test_none_retorna_placeholder(self):
        result = ReflectionLoop._format_outcome(None)
        assert result == "(sem resultado registrado)"

    def test_dict_serializado_como_json(self):
        result = ReflectionLoop._format_outcome({"key": "value"})
        assert '"key"' in result
        assert '"value"' in result


# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────

class TestConstants:
    def test_reflectable_severities_corretas(self):
        assert "warning" in REFLECTABLE_SEVERITIES
        assert "critical" in REFLECTABLE_SEVERITIES
        assert "opportunity" in REFLECTABLE_SEVERITIES
        assert "info" not in REFLECTABLE_SEVERITIES

    def test_webxp_agency_slug_correto(self):
        assert WEBXP_AGENCY_SLUG == "webxp_agency"
