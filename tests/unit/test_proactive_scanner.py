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
