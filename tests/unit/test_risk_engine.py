"""
Testes do Risk Engine (P2.C).

Cobre:
1. Categorizacao de modulos (read_only, write_internal, write_external)
2. Regras declarativas — cada caso de risco
3. Fail-open em caso de excecao
4. Refinamento LLM — chamado apenas em medium com contexto
5. Refinamento LLM — falha mantem assessment original
6. RiskAssessment — campos default
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.risk.risk_engine import (
    KNOWN_AUTONOMY_EVENTS,
    READ_ONLY_MODULES,
    WRITE_EXTERNAL_MODULES,
    WRITE_INTERNAL_MODULES,
    RiskAssessment,
    RiskEngine,
)


pytestmark = pytest.mark.asyncio


# ─────────────────────────────────────────────────────────────────────────────
# Categorizacao de modulos
# ─────────────────────────────────────────────────────────────────────────────

class TestModuleCategory:
    def test_read_only_modules(self):
        for mod in ["m02_relatorios", "m04_campanhas", "m09_arquivos"]:
            assert RiskEngine._module_category(mod) == "read_only"

    def test_write_internal_modules(self):
        for mod in ["m01_roteiros", "m11_hipoteses", "m12_alertas"]:
            assert RiskEngine._module_category(mod) == "write_internal"

    def test_modulo_desconhecido_default_write_internal(self):
        # Conservador: modulo nao mapeado vira write_internal
        assert RiskEngine._module_category("m99_imaginario") == "write_internal"


# ─────────────────────────────────────────────────────────────────────────────
# Regras declarativas — risco baixo
# ─────────────────────────────────────────────────────────────────────────────

class TestRulesLowRisk:
    async def test_read_only_sempre_liberado(self):
        engine = RiskEngine()
        result = await engine.evaluate(
            event_type="autonomy_cpl_spike",
            module_code="m04_campanhas",
            severity="warning",
        )
        assert result.approved is True
        assert result.risk_level == "low"
        assert result.requires_confirmation is False

    async def test_opportunity_sempre_liberado(self):
        engine = RiskEngine()
        result = await engine.evaluate(
            event_type="autonomy_stale_creatives",
            module_code="m11_hipoteses",
            severity="opportunity",
        )
        assert result.approved is True
        assert result.risk_level == "low"

    async def test_default_e_low(self):
        engine = RiskEngine()
        result = await engine.evaluate(
            event_type="custom_event",
            module_code="m01_roteiros",
            severity="info",
        )
        assert result.approved is True
        assert result.risk_level == "low"


# ─────────────────────────────────────────────────────────────────────────────
# Regras declarativas — risco medio
# ─────────────────────────────────────────────────────────────────────────────

class TestRulesMediumRisk:
    async def test_write_internal_com_warning_e_medium(self):
        engine = RiskEngine()
        result = await engine.evaluate(
            event_type="autonomy_frequency_high",
            module_code="m11_hipoteses",
            severity="warning",
        )
        assert result.approved is True
        assert result.risk_level == "medium"

    async def test_evento_autonomy_desconhecido_e_medium(self):
        engine = RiskEngine()
        result = await engine.evaluate(
            event_type="autonomy_invented_event",  # nao mapeado
            module_code="m11_hipoteses",
            severity="warning",
        )
        assert result.risk_level == "medium"
        assert "nao mapeado" in result.reason


# ─────────────────────────────────────────────────────────────────────────────
# Regras declarativas — risco alto
# ─────────────────────────────────────────────────────────────────────────────

class TestRulesHighRisk:
    async def test_critical_sempre_exige_confirmacao(self):
        engine = RiskEngine()
        result = await engine.evaluate(
            event_type="autonomy_low_health",
            module_code="m11_hipoteses",
            severity="critical",
        )
        assert result.approved is False
        assert result.requires_confirmation is True
        assert result.risk_level == "high"

    async def test_critical_mesmo_em_read_only_e_bloqueado(self):
        # Critical sempre bloqueia, mesmo em read-only
        engine = RiskEngine()
        result = await engine.evaluate(
            event_type="autonomy_cpl_spike",
            module_code="m04_campanhas",  # read-only
            severity="critical",
        )
        assert result.approved is False
        assert result.requires_confirmation is True

    async def test_write_external_sempre_exige_confirmacao(self):
        # Adiciona um modulo write_external temporario
        WRITE_EXTERNAL_MODULES.add("m99_kommo_operator")
        try:
            engine = RiskEngine()
            result = await engine.evaluate(
                event_type="autonomy_cpl_spike",
                module_code="m99_kommo_operator",
                severity="warning",
            )
            assert result.approved is False
            assert result.requires_confirmation is True
            assert result.risk_level == "high"
        finally:
            WRITE_EXTERNAL_MODULES.discard("m99_kommo_operator")


# ─────────────────────────────────────────────────────────────────────────────
# Fail-open
# ─────────────────────────────────────────────────────────────────────────────

class TestFailOpen:
    async def test_excecao_interna_retorna_approved(self):
        engine = RiskEngine()
        # Forca falha mockando _evaluate_rules
        with patch.object(
            engine,
            "_evaluate_rules",
            side_effect=RuntimeError("bug imaginario"),
        ):
            result = await engine.evaluate(
                event_type="autonomy_cpl_spike",
                module_code="m04_campanhas",
            )
        assert result.approved is True
        assert result.extras.get("fail_open") is True


# ─────────────────────────────────────────────────────────────────────────────
# Refinamento LLM
# ─────────────────────────────────────────────────────────────────────────────

class TestLLMRefinement:
    async def test_refinamento_so_acontece_em_medium(self):
        """LLM nao deve ser chamado se assessment ja e low ou high."""
        mock_claude = MagicMock()
        mock_claude.extract_json = AsyncMock(return_value={"data": {}})
        engine = RiskEngine(claude_client=mock_claude)

        # Low — nao deve chamar LLM
        result = await engine.evaluate(
            event_type="autonomy_cpl_spike",
            module_code="m04_campanhas",  # read-only -> low
            severity="warning",
            client_slug="ottoboni",
            db=MagicMock(),
        )
        assert result.used_llm is False
        mock_claude.extract_json.assert_not_called()

    async def test_refinamento_acontece_em_medium_com_contexto(self):
        mock_claude = MagicMock()
        mock_claude.extract_json = AsyncMock(return_value={
            "data": {
                "approved": False,
                "requires_confirmation": True,
                "risk_level": "high",
                "reason": "historico do cliente sugere cautela",
            }
        })
        engine = RiskEngine(claude_client=mock_claude)

        # write_internal + warning => medium => deve refinar
        with patch("memory.client_os.ClientOS.for_slug", new=AsyncMock()) as mock_for_slug:
            mock_cos = MagicMock()
            mock_cos.narrative = AsyncMock(
                return_value="cliente sensivel, historico de mudancas mal recebidas " * 5
            )
            mock_for_slug.return_value = mock_cos

            result = await engine.evaluate(
                event_type="autonomy_frequency_high",
                module_code="m11_hipoteses",
                severity="warning",
                client_slug="ottoboni",
                db=MagicMock(),
            )

        assert result.used_llm is True
        assert result.approved is False
        assert result.requires_confirmation is True

    async def test_refinamento_sem_db_nao_chama_llm(self):
        mock_claude = MagicMock()
        mock_claude.extract_json = AsyncMock()
        engine = RiskEngine(claude_client=mock_claude)

        await engine.evaluate(
            event_type="autonomy_frequency_high",
            module_code="m11_hipoteses",
            severity="warning",
            client_slug="ottoboni",
            db=None,  # sem db nao tem como buscar contexto
        )
        mock_claude.extract_json.assert_not_called()

    async def test_refinamento_sem_slug_nao_chama_llm(self):
        mock_claude = MagicMock()
        mock_claude.extract_json = AsyncMock()
        engine = RiskEngine(claude_client=mock_claude)

        await engine.evaluate(
            event_type="autonomy_frequency_high",
            module_code="m11_hipoteses",
            severity="warning",
            client_slug=None,  # sem slug
            db=MagicMock(),
        )
        mock_claude.extract_json.assert_not_called()

    async def test_refinamento_com_narrative_vazia_mantem_original(self):
        mock_claude = MagicMock()
        mock_claude.extract_json = AsyncMock()
        engine = RiskEngine(claude_client=mock_claude)

        with patch("memory.client_os.ClientOS.for_slug", new=AsyncMock()) as mock_for_slug:
            mock_cos = MagicMock()
            mock_cos.narrative = AsyncMock(return_value="")  # vazia
            mock_for_slug.return_value = mock_cos

            result = await engine.evaluate(
                event_type="autonomy_frequency_high",
                module_code="m11_hipoteses",
                severity="warning",
                client_slug="ottoboni",
                db=MagicMock(),
            )

        # LLM nao foi chamado (narrative vazia)
        mock_claude.extract_json.assert_not_called()
        assert result.used_llm is False
        # Assessment original preservado
        assert result.risk_level == "medium"

    async def test_falha_no_llm_mantem_assessment_original(self):
        mock_claude = MagicMock()
        mock_claude.extract_json = AsyncMock(side_effect=RuntimeError("api down"))
        engine = RiskEngine(claude_client=mock_claude)

        with patch("memory.client_os.ClientOS.for_slug", new=AsyncMock()) as mock_for_slug:
            mock_cos = MagicMock()
            mock_cos.narrative = AsyncMock(return_value="contexto util " * 30)
            mock_for_slug.return_value = mock_cos

            result = await engine.evaluate(
                event_type="autonomy_frequency_high",
                module_code="m11_hipoteses",
                severity="warning",
                client_slug="ottoboni",
                db=MagicMock(),
            )

        # Falha no LLM => mantem o assessment medium original
        assert result.risk_level == "medium"
        assert result.used_llm is False


# ─────────────────────────────────────────────────────────────────────────────
# RiskAssessment dataclass
# ─────────────────────────────────────────────────────────────────────────────

class TestRiskAssessmentDataclass:
    def test_defaults(self):
        a = RiskAssessment(approved=True)
        assert a.approved is True
        assert a.requires_confirmation is False
        assert a.risk_level == "low"
        assert a.reason == ""
        assert a.used_llm is False
        assert a.extras == {}


# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────

class TestConstants:
    def test_known_autonomy_events(self):
        assert "autonomy_cpl_spike" in KNOWN_AUTONOMY_EVENTS
        assert "autonomy_frequency_high" in KNOWN_AUTONOMY_EVENTS
        assert "autonomy_stale_creatives" in KNOWN_AUTONOMY_EVENTS
        assert "autonomy_low_health" in KNOWN_AUTONOMY_EVENTS

    def test_module_sets_disjuntos(self):
        # READ_ONLY e WRITE_INTERNAL nao podem ter intersecao
        intersection = READ_ONLY_MODULES & WRITE_INTERNAL_MODULES
        assert intersection == set(), f"Modulos em ambas categorias: {intersection}"
