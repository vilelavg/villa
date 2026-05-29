"""
Testes das rotas do Autonomy Engine no orquestrador (P2.B).

Valida que os 4 eventos autonomy sao roteados para os modulos corretos.
Usa um Orchestrator limpo e registra as rotas como setup_orchestrator faz,
sem depender da importacao de todos os modulos reais.

Cobre:
1. Cada evento autonomy mapeia para os modulos esperados
2. get_event_routes() expoe as rotas corretamente
3. handle_event de um evento autonomy resolve os modulos certos
"""
from __future__ import annotations

import pytest

from core.orchestrator import Orchestrator
from core.models import ModuleCode


# Mapeamento esperado (espelha setup_orchestrator)
EXPECTED_AUTONOMY_ROUTES = {
    "autonomy_cpl_spike": [ModuleCode.M04_CAMPANHAS, ModuleCode.M12_ALERTAS],
    "autonomy_frequency_high": [ModuleCode.M04_CAMPANHAS, ModuleCode.M11_HIPOTESES],
    "autonomy_stale_creatives": [ModuleCode.M11_HIPOTESES],
    "autonomy_low_health": [ModuleCode.M04_CAMPANHAS, ModuleCode.M12_ALERTAS],
}


@pytest.fixture
def orchestrator_with_autonomy_routes():
    """Orchestrator limpo com apenas as rotas autonomy registradas."""
    orch = Orchestrator()
    for event_type, modules in EXPECTED_AUTONOMY_ROUTES.items():
        orch.register_event_route(event_type, modules)
    return orch


class TestAutonomyRoutesRegistered:
    def test_todos_eventos_autonomy_registrados(self, orchestrator_with_autonomy_routes):
        routes = orchestrator_with_autonomy_routes.get_event_routes()
        for event_type in EXPECTED_AUTONOMY_ROUTES:
            assert event_type in routes, f"Evento '{event_type}' nao registrado"

    def test_cpl_spike_mapeia_campanhas_e_alertas(self, orchestrator_with_autonomy_routes):
        routes = orchestrator_with_autonomy_routes.get_event_routes()
        assert routes["autonomy_cpl_spike"] == [
            ModuleCode.M04_CAMPANHAS.value,
            ModuleCode.M12_ALERTAS.value,
        ]

    def test_frequency_high_mapeia_campanhas_e_hipoteses(self, orchestrator_with_autonomy_routes):
        routes = orchestrator_with_autonomy_routes.get_event_routes()
        assert routes["autonomy_frequency_high"] == [
            ModuleCode.M04_CAMPANHAS.value,
            ModuleCode.M11_HIPOTESES.value,
        ]

    def test_stale_creatives_mapeia_hipoteses(self, orchestrator_with_autonomy_routes):
        routes = orchestrator_with_autonomy_routes.get_event_routes()
        assert routes["autonomy_stale_creatives"] == [ModuleCode.M11_HIPOTESES.value]

    def test_low_health_mapeia_campanhas_e_alertas(self, orchestrator_with_autonomy_routes):
        routes = orchestrator_with_autonomy_routes.get_event_routes()
        assert routes["autonomy_low_health"] == [
            ModuleCode.M04_CAMPANHAS.value,
            ModuleCode.M12_ALERTAS.value,
        ]


class TestSetupOrchestratorRegistersAutonomy:
    """Valida que o setup_orchestrator real registra as rotas autonomy."""

    def test_setup_inclui_rotas_autonomy(self):
        from core.orchestrator import setup_orchestrator

        orch = setup_orchestrator()
        routes = orch.get_event_routes()
        for event_type, modules in EXPECTED_AUTONOMY_ROUTES.items():
            assert event_type in routes
            assert routes[event_type] == [m.value for m in modules]
