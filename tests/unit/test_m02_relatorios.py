# ═══════════════════════════════════════════════════════════════
# VILLA — tests/unit/test_m02_relatorios.py
#
# Testes unitários do M02Relatorios baseados no código real.
#
# Interface real confirmada:
#   Classe: M02Relatorios (modules/m02_relatorios/agent.py)
#   Método: execute(message, db, user, client_slug, context) -> dict
#   Retorno: {"success": bool, "message": str, "data": dict, "actions_taken": list, "tokens_used": int}
#
# Métricas consolidadas (em _consolidate()):
#   cpl_consolidated = total_spend / total_leads
#   roi = (total_revenue - total_spend) / total_spend * 100
#   conversion_rate = won / leads * 100
#
# Detecção de tipo de relatório (em _detect_report_type()):
#   "diário/diario" ou context["event_type"]=="scheduler_daily" → "daily"
#   "semanal" → "weekly" (default)
#   "mensal" → "monthly"
#
# DataCollector._collect_meta():
#   Checa settings.meta_access_token — se vazio ou 'TROCAR_AQUI', retorna {"status": "paused"}
#   Caso contrário chama meta_ads.get_campaign_insights()
# ═══════════════════════════════════════════════════════════════

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ── Factories ────────────────────────────────────────────────────────────────

def make_client(
    slug: str = "clinica-demo",
    meta_account: str = "act_123456789",
    google_id: str | None = None,
    kommo_pipeline: int | None = None,
) -> MagicMock:
    client = MagicMock()
    client.id = "client-uuid-123"
    client.slug = slug
    client.name = "Clínica Demo"
    client.specialty = "implante"
    client.meta_ad_account_id = meta_account
    client.google_ads_id = google_id
    client.kommo_pipeline_id = kommo_pipeline
    client.config = {}
    client.status = "active"
    return client


def make_db(client: MagicMock | None = None) -> AsyncMock:
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = client
    mock_result.scalars.return_value.all.return_value = [client] if client else []
    db.execute.return_value = mock_result
    db.flush = AsyncMock()
    db.add = MagicMock()
    return db


def make_claude_response(text: str = "Performance estável na semana.") -> dict:
    return {
        "text": text,
        "tokens_input": 400,
        "tokens_output": 250,
        "model": "claude-sonnet-4-20250514",
        "cost_usd": 0.004,
        "stop_reason": "end_turn",
    }


def make_meta_insights(
    spend: float = 500.0,
    impressions: int = 20000,
    clicks: int = 600,
    leads: int = 10,
) -> list[dict]:
    """Retorna lista de insights no formato que meta_ads.get_campaign_insights() retorna."""
    return [
        {
            "campaign_id": "camp_001",
            "campaign_name": "Implante | Awareness | Mai26",
            "spend": str(spend),
            "impressions": str(impressions),
            "clicks": str(clicks),
            "ctr": str(round(clicks / impressions * 100, 2)) if impressions else "0",
            "actions": [{"action_type": "lead", "value": str(leads)}],
            "cost_per_action_type": [{"action_type": "lead", "value": str(round(spend / leads, 2)) if leads else "0"}],
        }
    ]


FEEDBACK_CONTEXT = {
    "prompt_injection": "",
    "reasoning_context": "M02 em execução.",
    "sources": [],
}


# ── Fixture base ──────────────────────────────────────────────────────────────

@pytest.fixture
def setup_m02():
    """Monta o ambiente de mocks para M02Relatorios."""
    client = make_client()
    db = make_db(client)

    fl_mock = AsyncMock()
    fl_mock.build_context.return_value = FEEDBACK_CONTEXT
    fl_mock.record_decision.return_value = "decision-uuid-789"

    patches = {
        "feedback_loop": patch("modules.m02_relatorios.agent.FeedbackLoop", return_value=fl_mock),
    }

    return client, db, patches


# ── Testes de execução básica ─────────────────────────────────────────────────

class TestExecutarRelatorio:

    async def test_relatorio_semanal_retorna_success_true(self, setup_m02):
        client, db, patches = setup_m02

        with patches["feedback_loop"]:
            from modules.m02_relatorios.agent import M02Relatorios

            module = M02Relatorios()
            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch("modules.m02_relatorios.agent.DataCollector") as MockCollector:

                mock_ask.return_value = make_claude_response()
                MockCollector.return_value.collect_all = AsyncMock(return_value={
                    "client": "clinica-demo",
                    "meta_ads": {"total_spend": 500.0, "total_leads": 10, "avg_ctr": 3.0, "avg_cpl": 50.0, "campaigns": []},
                    "leads_summary": {"total": 10, "qualified": 6, "won": 2, "total_value": 4000.0, "qualification_rate": 60.0},
                    "appointments": {"total": 5, "show_rate": 80.0},
                    "consolidated": {
                        "total_investment": 500.0, "total_leads": 10, "total_qualified": 6,
                        "total_won": 2, "total_revenue": 4000.0,
                        "cpl_consolidated": 50.0, "roi": 700.0, "conversion_rate": 20.0,
                        "show_rate": 80.0, "qualification_rate": 60.0,
                    },
                })

                result = await module.execute(
                    message="Relatório semanal da Clínica Demo",
                    db=db,
                    client_slug="clinica-demo",
                )

        assert result["success"] is True

    async def test_resultado_contem_campos_obrigatorios(self, setup_m02):
        client, db, patches = setup_m02

        with patches["feedback_loop"]:
            from modules.m02_relatorios.agent import M02Relatorios

            module = M02Relatorios()
            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch("modules.m02_relatorios.agent.DataCollector") as MockCollector:

                mock_ask.return_value = make_claude_response()
                MockCollector.return_value.collect_all = AsyncMock(return_value={
                    "client": "clinica-demo", "meta_ads": {}, "google_ads": None,
                    "leads_summary": {"total": 0, "qualified": 0, "won": 0, "total_value": 0, "qualification_rate": 0},
                    "appointments": {"total": 0, "show_rate": 0},
                    "consolidated": {"total_investment": 0, "total_leads": 0, "cpl_consolidated": 0},
                })

                result = await module.execute(
                    message="Semanal",
                    db=db,
                    client_slug="clinica-demo",
                )

        for campo in ("success", "message", "data", "actions_taken"):
            assert campo in result, f"Campo obrigatório '{campo}' ausente"

    async def test_data_contem_report_id_e_tipo(self, setup_m02):
        client, db, patches = setup_m02

        with patches["feedback_loop"]:
            from modules.m02_relatorios.agent import M02Relatorios

            module = M02Relatorios()
            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch("modules.m02_relatorios.agent.DataCollector") as MockCollector:

                mock_ask.return_value = make_claude_response()
                MockCollector.return_value.collect_all = AsyncMock(return_value={
                    "client": "clinica-demo", "meta_ads": None, "google_ads": None,
                    "leads_summary": {"total": 5, "qualified": 2, "won": 1, "total_value": 2000, "qualification_rate": 40},
                    "appointments": {"total": 3, "show_rate": 66.7},
                    "consolidated": {"total_investment": 300, "total_leads": 5, "cpl_consolidated": 60},
                })

                result = await module.execute(
                    message="Relatório semanal",
                    db=db,
                    client_slug="clinica-demo",
                )

        assert result["success"] is True
        assert "report_id" in result["data"]
        assert result["data"]["type"] in ("daily", "weekly", "monthly")


# ── Testes de detecção de tipo de relatório ───────────────────────────────────

class TestDeteccaoTipoRelatorio:
    """
    _detect_report_type() usa o texto da mensagem para inferir o tipo.
    Não precisa de DB — testa o método isolado.
    """

    def test_detecta_diario_pelo_texto(self):
        from modules.m02_relatorios.agent import M02Relatorios
        module = M02Relatorios()
        tipo = module._detect_report_type("Quero o relatório diário do Ottoboni", {})
        assert tipo == "daily"

    def test_detecta_semanal_pelo_texto(self):
        from modules.m02_relatorios.agent import M02Relatorios
        module = M02Relatorios()
        tipo = module._detect_report_type("Manda o relatório semanal", {})
        assert tipo == "weekly"

    def test_detecta_mensal_pelo_texto(self):
        from modules.m02_relatorios.agent import M02Relatorios
        module = M02Relatorios()
        tipo = module._detect_report_type("Preciso do relatório mensal", {})
        assert tipo == "monthly"

    def test_detecta_diario_pelo_context_scheduler(self):
        from modules.m02_relatorios.agent import M02Relatorios
        module = M02Relatorios()
        tipo = module._detect_report_type("", {"event_type": "scheduler_daily"})
        assert tipo == "daily"

    def test_detecta_semanal_pelo_context_scheduler(self):
        from modules.m02_relatorios.agent import M02Relatorios
        module = M02Relatorios()
        tipo = module._detect_report_type("", {"event_type": "scheduler_weekly"})
        assert tipo == "weekly"

    def test_default_e_weekly_quando_ambiguo(self):
        from modules.m02_relatorios.agent import M02Relatorios
        module = M02Relatorios()
        tipo = module._detect_report_type("Como estão as campanhas?", {})
        assert tipo == "weekly"


# ── Testes de cálculo de período ─────────────────────────────────────────────

class TestDeteccaoPeriodo:
    """
    _detect_period() retorna (period_start, period_end) baseado no tipo.
    Testa sem DB.
    """

    def test_periodo_daily_e_ontem(self):
        from modules.m02_relatorios.agent import M02Relatorios
        module = M02Relatorios()
        start, end = module._detect_period("daily")
        hoje = date.today()
        assert end == hoje - timedelta(days=1)
        assert start == hoje - timedelta(days=1)

    def test_periodo_weekly_e_7_dias(self):
        from modules.m02_relatorios.agent import M02Relatorios
        module = M02Relatorios()
        start, end = module._detect_period("weekly")
        hoje = date.today()
        assert end == hoje - timedelta(days=1)
        assert start == hoje - timedelta(days=7)

    def test_periodo_monthly_e_mes_anterior(self):
        from modules.m02_relatorios.agent import M02Relatorios
        module = M02Relatorios()
        start, end = module._detect_period("monthly")
        # end deve ser o último dia do mês anterior
        hoje = date.today()
        ultimo_dia_mes_anterior = hoje.replace(day=1) - timedelta(days=1)
        assert end == ultimo_dia_mes_anterior


# ── Testes do DataCollector._consolidate() ───────────────────────────────────

class TestConsolidacao:
    """
    _consolidate() é síncrono — testa sem mocks, apenas com dados.
    Valida os cálculos de CPL, ROI e conversion_rate.
    """

    def test_cpl_calculado_corretamente(self):
        """CPL = total_spend / total_leads."""
        from modules.m02_relatorios.collectors import DataCollector

        collector = DataCollector.__new__(DataCollector)
        data = {
            "meta_ads": {"total_spend": 500.0, "total_leads": 10},
            "google_ads": None,
            "leads_summary": {"total": 10, "qualified": 5, "won": 2, "total_value": 4000.0, "qualification_rate": 50.0},
            "appointments": {"total": 4, "show_rate": 75.0},
        }

        consolidated = collector._consolidate(data)

        assert consolidated["cpl_consolidated"] == 50.0, (
            f"CPL esperado R$50,00, obtido R${consolidated['cpl_consolidated']}"
        )

    def test_cpl_zero_leads_retorna_zero_sem_excecao(self):
        """Divisão por zero deve ser protegida — retorna 0."""
        from modules.m02_relatorios.collectors import DataCollector

        collector = DataCollector.__new__(DataCollector)
        data = {
            "meta_ads": {"total_spend": 500.0, "total_leads": 0},
            "google_ads": None,
            "leads_summary": {"total": 0, "qualified": 0, "won": 0, "total_value": 0, "qualification_rate": 0},
            "appointments": {"show_rate": 0},
        }

        consolidated = collector._consolidate(data)  # não deve lançar ZeroDivisionError

        assert consolidated["cpl_consolidated"] == 0

    def test_roi_calculado_corretamente(self):
        """ROI = (receita - investimento) / investimento * 100."""
        from modules.m02_relatorios.collectors import DataCollector

        collector = DataCollector.__new__(DataCollector)
        data = {
            "meta_ads": {"total_spend": 1000.0, "total_leads": 20},
            "google_ads": None,
            "leads_summary": {"total": 20, "qualified": 10, "won": 5, "total_value": 5000.0, "qualification_rate": 50.0},
            "appointments": {"show_rate": 80.0},
        }

        consolidated = collector._consolidate(data)

        # ROI esperado: (5000 - 1000) / 1000 * 100 = 400%
        assert consolidated["roi"] == 400.0, f"ROI esperado 400.0%, obtido {consolidated['roi']}"

    def test_roi_zero_investimento_retorna_zero(self):
        """Sem investimento, ROI deve ser 0 sem dividir por zero."""
        from modules.m02_relatorios.collectors import DataCollector

        collector = DataCollector.__new__(DataCollector)
        data = {
            "meta_ads": None,
            "google_ads": None,
            "leads_summary": {"total": 5, "qualified": 2, "won": 1, "total_value": 2000.0, "qualification_rate": 40.0},
            "appointments": {"show_rate": 60.0},
        }

        consolidated = collector._consolidate(data)

        assert consolidated["roi"] == 0

    def test_conversion_rate_calculado_corretamente(self):
        """conversion_rate = won / leads * 100."""
        from modules.m02_relatorios.collectors import DataCollector

        collector = DataCollector.__new__(DataCollector)
        data = {
            "meta_ads": {"total_spend": 500.0, "total_leads": 20},
            "google_ads": None,
            "leads_summary": {"total": 20, "qualified": 10, "won": 4, "total_value": 8000.0, "qualification_rate": 50.0},
            "appointments": {"show_rate": 80.0},
        }

        consolidated = collector._consolidate(data)

        # 4 won de 20 leads = 20.0%
        assert consolidated["conversion_rate"] == 20.0

    def test_consolida_meta_e_google_investimento(self):
        """total_investment deve somar Meta + Google."""
        from modules.m02_relatorios.collectors import DataCollector

        collector = DataCollector.__new__(DataCollector)
        data = {
            "meta_ads": {"total_spend": 300.0, "total_leads": 5},
            "google_ads": {"total_spend": 200.0},
            "leads_summary": {"total": 5, "qualified": 2, "won": 1, "total_value": 1000.0, "qualification_rate": 40.0},
            "appointments": {"show_rate": 60.0},
        }

        consolidated = collector._consolidate(data)

        assert consolidated["total_investment"] == 500.0


# ── Testes de resiliência ─────────────────────────────────────────────────────

class TestResiliencia:

    async def test_cliente_nao_encontrado_retorna_success_false(self):
        db = make_db(client=None)

        with patch("modules.m02_relatorios.agent.FeedbackLoop"):
            from modules.m02_relatorios.agent import M02Relatorios

            module = M02Relatorios()
            result = await module.execute(
                message="Relatório semanal",
                db=db,
                client_slug="cliente-inexistente",
            )

        assert result["success"] is False
        assert "client_not_found" in result.get("actions_taken", [])

    async def test_falha_do_claude_nao_lanca_excecao(self, setup_m02):
        client, db, patches = setup_m02

        with patches["feedback_loop"]:
            from modules.m02_relatorios.agent import M02Relatorios

            module = M02Relatorios()
            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch("modules.m02_relatorios.agent.DataCollector") as MockCollector:

                MockCollector.return_value.collect_all = AsyncMock(return_value={
                    "client": "clinica-demo", "meta_ads": None, "google_ads": None,
                    "leads_summary": {"total": 0, "qualified": 0, "won": 0, "total_value": 0, "qualification_rate": 0},
                    "appointments": {"show_rate": 0},
                    "consolidated": {},
                })
                mock_ask.side_effect = Exception("Claude timeout")

                result = await module.execute(
                    message="Semanal",
                    db=db,
                    client_slug="clinica-demo",
                )
                assert result["success"] is False


# ── Testes de can_handle ──────────────────────────────────────────────────────

class TestCanHandle:

    async def test_alta_confianca_para_relatorio(self):
        from modules.m02_relatorios.agent import M02Relatorios
        module = M02Relatorios()
        score = await module.can_handle("Quero ver os resultados e métricas de performance da semana")
        assert score >= 0.8

    async def test_confianca_maxima_para_scheduler(self):
        from modules.m02_relatorios.agent import M02Relatorios
        module = M02Relatorios()
        score = await module.can_handle("", context={"event_type": "scheduler_weekly"})
        assert score >= 0.9

    async def test_confianca_zero_para_roteiro(self):
        from modules.m02_relatorios.agent import M02Relatorios
        module = M02Relatorios()
        score = await module.can_handle("Gera um gancho para o vídeo de implante")
        assert score == 0.0
