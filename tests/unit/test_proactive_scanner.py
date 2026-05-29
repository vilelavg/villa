"""
Testes do ProactiveScanner (P2.A — Autonomy Engine, Loop 1).

Cobre:
1. Condicoes declarativas — disparo correto e campos do ScanAction
2. Condicoes — nao disparam com dados insuficientes (None)
3. scan_all() — defensivo: erro num cliente nao para os demais
4. scan_all() — sem orchestrator (dry-run) nao propaga erro
5. _build_client_views() — pula webxp_agency e clientes inativos
6. ScanResult — campos corretos
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.autonomy.proactive_scanner import (
    DEFAULT_CONDITIONS,
    ScanAction,
    ScanResult,
    ProactiveScanner,
    _cond_cpl_spike,
    _cond_frequency_high,
    _cond_low_health_score,
    _cond_stale_creatives,
)


pytestmark = pytest.mark.asyncio


def _view(**kwargs):
    """Monta um client_view minimo com defaults."""
    base = {
        "slug": "test_client",
        "name": "Test Client",
        "frequency_max": 3.0,
        "cpl_current": None,
        "cpl_previous": None,
        "frequency": None,
        "health_score": None,
        "days_since_last_roteiro": None,
    }
    base.update(kwargs)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# Condicao: CPL spike
# ─────────────────────────────────────────────────────────────────────────────

class TestCondCplSpike:
    def test_dispara_com_alta_de_30pct(self):
        view = _view(cpl_current=130.0, cpl_previous=100.0)
        action = _cond_cpl_spike(view)
        assert action is not None
        assert action.event_type == "autonomy_cpl_spike"
        assert action.severity == "warning"
        assert action.client_slug == "test_client"

    def test_dispara_critical_acima_de_50pct(self):
        view = _view(cpl_current=160.0, cpl_previous=100.0)
        action = _cond_cpl_spike(view)
        assert action is not None
        assert action.severity == "critical"

    def test_nao_dispara_abaixo_de_30pct(self):
        view = _view(cpl_current=125.0, cpl_previous=100.0)
        assert _cond_cpl_spike(view) is None

    def test_nao_dispara_sem_cpl_current(self):
        view = _view(cpl_previous=100.0)
        assert _cond_cpl_spike(view) is None

    def test_nao_dispara_sem_cpl_previous(self):
        view = _view(cpl_current=150.0)
        assert _cond_cpl_spike(view) is None

    def test_nao_dispara_com_cpl_previous_zero(self):
        view = _view(cpl_current=150.0, cpl_previous=0.0)
        assert _cond_cpl_spike(view) is None


# ─────────────────────────────────────────────────────────────────────────────
# Condicao: frequency high
# ─────────────────────────────────────────────────────────────────────────────

class TestCondFrequencyHigh:
    def test_dispara_quando_frequencia_no_limite(self):
        view = _view(frequency=3.0, frequency_max=3.0)
        action = _cond_frequency_high(view)
        assert action is not None
        assert action.event_type == "autonomy_frequency_high"

    def test_dispara_quando_frequencia_acima(self):
        view = _view(frequency=3.5, frequency_max=3.0)
        assert _cond_frequency_high(view) is not None

    def test_nao_dispara_abaixo_do_limite(self):
        view = _view(frequency=2.9, frequency_max=3.0)
        assert _cond_frequency_high(view) is None

    def test_nao_dispara_sem_frequencia(self):
        view = _view()
        assert _cond_frequency_high(view) is None

    def test_respeita_threshold_customizado(self):
        view = _view(frequency=2.5, frequency_max=2.5)
        assert _cond_frequency_high(view) is not None


# ─────────────────────────────────────────────────────────────────────────────
# Condicao: stale creatives
# ─────────────────────────────────────────────────────────────────────────────

class TestCondStaleCreatives:
    def test_dispara_com_14_dias_sem_roteiro(self):
        view = _view(days_since_last_roteiro=14)
        action = _cond_stale_creatives(view)
        assert action is not None
        assert action.event_type == "autonomy_stale_creatives"
        assert action.severity == "opportunity"

    def test_nao_dispara_com_13_dias(self):
        view = _view(days_since_last_roteiro=13)
        assert _cond_stale_creatives(view) is None

    def test_nao_dispara_sem_dado(self):
        view = _view()
        assert _cond_stale_creatives(view) is None


# ─────────────────────────────────────────────────────────────────────────────
# Condicao: low health score
# ─────────────────────────────────────────────────────────────────────────────

class TestCondLowHealthScore:
    def test_dispara_warning_abaixo_de_50(self):
        view = _view(health_score=49)
        action = _cond_low_health_score(view)
        assert action is not None
        assert action.severity == "warning"

    def test_dispara_critical_abaixo_de_30(self):
        view = _view(health_score=29)
        action = _cond_low_health_score(view)
        assert action is not None
        assert action.severity == "critical"

    def test_nao_dispara_com_50_ou_acima(self):
        assert _cond_low_health_score(_view(health_score=50)) is None
        assert _cond_low_health_score(_view(health_score=100)) is None

    def test_nao_dispara_sem_dado(self):
        assert _cond_low_health_score(_view()) is None


# ─────────────────────────────────────────────────────────────────────────────
# ProactiveScanner.scan_all() — defensivo
# ─────────────────────────────────────────────────────────────────────────────

class TestScanAll:
    async def test_retorna_scan_result_vazio_sem_clientes(self):
        scanner = ProactiveScanner()
        with patch.object(scanner, "_build_client_views", new=AsyncMock(return_value=[])):
            result = await scanner.scan_all(db=MagicMock())
        assert isinstance(result, ScanResult)
        assert result.clients_scanned == 0
        assert result.actions == []

    async def test_conta_clientes_scaneados(self):
        scanner = ProactiveScanner(conditions=[])
        views = [_view(slug="a"), _view(slug="b"), _view(slug="c")]
        with patch.object(scanner, "_build_client_views", new=AsyncMock(return_value=views)):
            result = await scanner.scan_all(db=MagicMock())
        assert result.clients_scanned == 3

    async def test_erro_num_cliente_nao_para_os_demais(self):
        def bad_condition(view):
            if view["slug"] == "bad":
                raise RuntimeError("deu ruim")
            return None

        scanner = ProactiveScanner(conditions=[bad_condition])
        views = [_view(slug="bad"), _view(slug="ok1"), _view(slug="ok2")]
        with patch.object(scanner, "_build_client_views", new=AsyncMock(return_value=views)):
            result = await scanner.scan_all(db=MagicMock())

        assert result.clients_scanned == 3
        assert len(result.errors) == 1
        assert "bad" in result.errors[0]

    async def test_sem_orchestrator_nao_propaga_erro(self):
        scanner = ProactiveScanner(conditions=[lambda v: ScanAction(
            client_slug=v["slug"],
            event_type="test_event",
            reason="test",
            severity="warning",
        )])
        views = [_view()]
        with patch.object(scanner, "_build_client_views", new=AsyncMock(return_value=views)):
            # orchestrator=None → dry-run, nao deve estourar
            result = await scanner.scan_all(db=MagicMock(), orchestrator=None)
        assert result.actions_count == 1

    async def test_erro_em_build_views_retorna_result_com_erro(self):
        scanner = ProactiveScanner()
        with patch.object(
            scanner, "_build_client_views", new=AsyncMock(side_effect=RuntimeError("db down"))
        ):
            result = await scanner.scan_all(db=MagicMock())
        assert result.clients_scanned == 0
        assert len(result.errors) == 1


# ─────────────────────────────────────────────────────────────────────────────
# P2.B — Enriquecimento de CPL/frequencia a partir de villa_analysis
# ─────────────────────────────────────────────────────────────────────────────

class _FakeCampaign:
    """Campanha fake com villa_analysis para testes de enriquecimento."""

    def __init__(self, villa_analysis=None, health_score=None):
        self.villa_analysis = villa_analysis
        self.health_score = health_score


class TestEnrichMetricsFromAnalysis:
    def test_extrai_cpl_e_frequencia_en(self):
        view = _view()
        campaigns = [_FakeCampaign({"cpl_current": 150.0, "cpl_previous": 100.0, "frequency": 3.2})]
        ProactiveScanner._enrich_metrics_from_analysis(view, campaigns)
        assert view["cpl_current"] == 150.0
        assert view["cpl_previous"] == 100.0
        assert view["frequency"] == 3.2

    def test_extrai_cpl_e_frequencia_pt(self):
        view = _view()
        campaigns = [_FakeCampaign({"cpl_atual": 120.0, "cpl_anterior": 90.0, "frequencia": 2.8})]
        ProactiveScanner._enrich_metrics_from_analysis(view, campaigns)
        assert view["cpl_current"] == 120.0
        assert view["cpl_previous"] == 90.0
        assert view["frequency"] == 2.8

    def test_pega_pior_caso_entre_campanhas(self):
        view = _view()
        campaigns = [
            _FakeCampaign({"cpl_current": 100.0, "cpl_previous": 80.0, "frequency": 2.0}),
            _FakeCampaign({"cpl_current": 200.0, "cpl_previous": 150.0, "frequency": 4.0}),
        ]
        ProactiveScanner._enrich_metrics_from_analysis(view, campaigns)
        assert view["cpl_current"] == 200.0       # maior CPL atual
        assert view["cpl_previous"] == 150.0      # anterior correspondente
        assert view["frequency"] == 4.0           # maior frequencia

    def test_villa_analysis_none_nao_quebra(self):
        view = _view()
        campaigns = [_FakeCampaign(None), _FakeCampaign({"cpl_current": 50.0})]
        ProactiveScanner._enrich_metrics_from_analysis(view, campaigns)
        assert view["cpl_current"] == 50.0

    def test_valores_invalidos_sao_ignorados(self):
        view = _view()
        campaigns = [_FakeCampaign({"cpl_current": "abc", "frequency": None})]
        ProactiveScanner._enrich_metrics_from_analysis(view, campaigns)
        assert view["cpl_current"] is None
        assert view["frequency"] is None

    def test_sem_campanhas_mantem_none(self):
        view = _view()
        ProactiveScanner._enrich_metrics_from_analysis(view, [])
        assert view["cpl_current"] is None
        assert view["cpl_previous"] is None
        assert view["frequency"] is None


# ─────────────────────────────────────────────────────────────────────────────
# P2.B — _summarize_outcome
# ─────────────────────────────────────────────────────────────────────────────

class TestSummarizeOutcome:
    def test_none_retorna_none(self):
        assert ProactiveScanner._summarize_outcome(None) is None

    def test_dict_passa_direto(self):
        result = ProactiveScanner._summarize_outcome({"health_score": 40})
        assert result == {"health_score": 40}

    def test_lista_vira_dict_com_results(self):
        result = ProactiveScanner._summarize_outcome(["mod1 ok", "mod2 ok"])
        assert "results" in result
        assert len(result["results"]) == 2

    def test_outro_tipo_vira_dict_com_result(self):
        result = ProactiveScanner._summarize_outcome("texto qualquer")
        assert "result" in result
        assert "texto" in result["result"]


# ─────────────────────────────────────────────────────────────────────────────
# P2.B — Ciclo ReflectionLoop no _dispatch
# ─────────────────────────────────────────────────────────────────────────────

class TestDispatchReflectionCycle:
    async def test_dispatch_chama_reflection_apos_handle_event(self):
        scanner = ProactiveScanner()
        action = ScanAction(
            client_slug="ottoboni",
            event_type="autonomy_cpl_spike",
            reason="CPL +40%",
            severity="warning",
            payload={"delta": 0.4},
        )
        orchestrator = MagicMock()
        orchestrator.handle_event = AsyncMock(return_value={"health_score": 45})

        with patch("memory.autonomy.reflection_loop.reflection_loop") as mock_refl:
            mock_refl.reflect = AsyncMock()
            await scanner._dispatch(action, db=MagicMock(), orchestrator=orchestrator)

        orchestrator.handle_event.assert_awaited_once()
        mock_refl.reflect.assert_awaited_once()
        # reflect recebeu o outcome do handle_event
        _, kwargs = mock_refl.reflect.call_args
        assert kwargs["client_slug"] == "ottoboni"
        assert kwargs["event_type"] == "autonomy_cpl_spike"
        assert kwargs["severity"] == "warning"

    async def test_dispatch_reflete_mesmo_se_handle_event_falha(self):
        scanner = ProactiveScanner()
        action = ScanAction(
            client_slug="x", event_type="autonomy_low_health",
            reason="score baixo", severity="critical",
        )
        orchestrator = MagicMock()
        orchestrator.handle_event = AsyncMock(side_effect=RuntimeError("modulo quebrou"))

        with patch("memory.autonomy.reflection_loop.reflection_loop") as mock_refl:
            mock_refl.reflect = AsyncMock()
            # nao deve propagar excecao
            await scanner._dispatch(action, db=MagicMock(), orchestrator=orchestrator)

        # reflexao ainda acontece (aprende ate com a falha)
        mock_refl.reflect.assert_awaited_once()

    async def test_dispatch_falha_na_reflexao_nao_propaga(self):
        scanner = ProactiveScanner()
        action = ScanAction(
            client_slug="x", event_type="autonomy_cpl_spike",
            reason="r", severity="warning",
        )
        orchestrator = MagicMock()
        orchestrator.handle_event = AsyncMock(return_value=None)

        with patch("memory.autonomy.reflection_loop.reflection_loop") as mock_refl:
            mock_refl.reflect = AsyncMock(side_effect=RuntimeError("reflection down"))
            # nao deve estourar
            await scanner._dispatch(action, db=MagicMock(), orchestrator=orchestrator)

        orchestrator.handle_event.assert_awaited_once()
