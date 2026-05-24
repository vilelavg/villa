# ═══════════════════════════════════════════════════════════════
# VILLA — tests/unit/test_m04_campanhas.py
#
# Testes unitários do M04Campanhas baseados no código real.
#
# Interface real confirmada:
#   Classe: M04Campanhas (modules/m04_campanhas/agent.py)
#   Método: execute(message, db, user, client_slug, context) -> dict
#   Retorno: {"success": bool, "message": str, "data": dict, "actions_taken": list}
#   data["analysis"]: {health_score, summary, anomalies, recommendations, trends, pareto}
#
# Fluxo interno:
#   _resolve_client → coleta Meta + Google (já com try/except individuais)
#   → ask_claude (análise) → extract_json → salva no banco → retorna
#
# O que está sendo testado:
#   - Happy path completo com análise
#   - health_score presente e dentro do range 0-100
#   - Anomalias detectadas quando CPL acima do threshold
#   - Recomendações presentes e priorizadas
#   - Meta indisponível não quebra o módulo (já tratado no código)
#   - Google indisponível não quebra o módulo (já tratado no código)
#   - Cliente não encontrado retorna success=False
#   - can_handle() para diferentes mensagens e contextos
# ═══════════════════════════════════════════════════════════════

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ── Factories ────────────────────────────────────────────────────────────────

def make_client(slug: str = "clinica-demo") -> MagicMock:
    client = MagicMock()
    client.id = "client-uuid-123"
    client.slug = slug
    client.name = "Clínica Demo"
    client.specialty = "implante"
    client.meta_ad_account_id = "act_123456789"
    client.google_ads_id = None
    client.config = {
        "thresholds": {
            "cpl_max": 60.0,
            "ctr_min": 1.2,
            "frequency_max": 3.0,
        }
    }
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


def make_claude_response(text: str) -> dict:
    return {
        "text": text,
        "tokens_input": 500,
        "tokens_output": 300,
        "model": "claude-sonnet-4-20250514",
        "cost_usd": 0.005,
        "stop_reason": "end_turn",
    }


def make_analysis(
    health_score: int = 72,
    cpl_trend: str = "stable",
    anomalies: list | None = None,
    recommendations: list | None = None,
) -> dict:
    return {
        "health_score": health_score,
        "summary": "Performance estável. CPL dentro do esperado, CTR acima da média.",
        "anomalies": anomalies or [],
        "pareto": {"top_campaign": "Implante | Awareness", "percentage_of_results": 78},
        "recommendations": recommendations or [
            {"action": "Renovar criativo da campanha Implante v2", "priority": 1,
             "expected_impact": "CTR +15%", "reasoning": "Frequência acima de 3x"},
            {"action": "Expandir público lookalike 3%", "priority": 2,
             "expected_impact": "Reduzir CPL ~10%", "reasoning": "Público atual saturado"},
        ],
        "trends": {"cpl": cpl_trend, "ctr": "up", "leads": "stable"},
    }


FEEDBACK_CONTEXT = {
    "prompt_injection": "",
    "reasoning_context": "M04 em análise.",
    "sources": [],
}


# ── Fixture base ──────────────────────────────────────────────────────────────

@pytest.fixture
def setup_m04():
    client = make_client()
    db = make_db(client)

    fl_mock = AsyncMock()
    fl_mock.build_context.return_value = FEEDBACK_CONTEXT
    fl_mock.record_decision.return_value = "decision-uuid-m04"

    patches = {
        "feedback_loop": patch("modules.m04_campanhas.agent.FeedbackLoop", return_value=fl_mock),
        "meta_ads": patch("modules.m04_campanhas.agent.meta_ads"),
        "google_ads": patch("modules.m04_campanhas.agent.google_ads"),
    }
    return client, db, patches


# ── Testes de execução básica ─────────────────────────────────────────────────

class TestAnalisarCampanhas:

    async def test_retorna_success_true_com_dados_validos(self, setup_m04):
        client, db, patches = setup_m04

        with patches["feedback_loop"], \
             patches["meta_ads"] as mock_meta, \
             patches["google_ads"] as mock_google:

            mock_meta.get_campaign_insights = AsyncMock(return_value=[])
            mock_google.get_metrics = AsyncMock(return_value={})

            from modules.m04_campanhas.agent import M04Campanhas
            module = M04Campanhas()

            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch.object(module.claude, "extract_json", new_callable=AsyncMock) as mock_json:

                mock_ask.return_value = make_claude_response("{}")
                mock_json.return_value = {"data": make_analysis()}

                result = await module.execute(
                    message="Analisa campanhas da Clínica Demo",
                    db=db,
                    client_slug="clinica-demo",
                )

        assert result["success"] is True

    async def test_resultado_contem_campos_obrigatorios(self, setup_m04):
        client, db, patches = setup_m04

        with patches["feedback_loop"], patches["meta_ads"] as m, patches["google_ads"] as g:
            m.get_campaign_insights = AsyncMock(return_value=[])
            g.get_metrics = AsyncMock(return_value={})

            from modules.m04_campanhas.agent import M04Campanhas
            module = M04Campanhas()

            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch.object(module.claude, "extract_json", new_callable=AsyncMock) as mock_json:

                mock_ask.return_value = make_claude_response("{}")
                mock_json.return_value = {"data": make_analysis()}

                result = await module.execute(
                    message="Performance campanhas",
                    db=db,
                    client_slug="clinica-demo",
                )

        for campo in ("success", "message", "data", "actions_taken"):
            assert campo in result, f"Campo '{campo}' ausente"

    async def test_data_contem_analysis(self, setup_m04):
        client, db, patches = setup_m04

        with patches["feedback_loop"], patches["meta_ads"] as m, patches["google_ads"] as g:
            m.get_campaign_insights = AsyncMock(return_value=[])
            g.get_metrics = AsyncMock(return_value={})

            from modules.m04_campanhas.agent import M04Campanhas
            module = M04Campanhas()

            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch.object(module.claude, "extract_json", new_callable=AsyncMock) as mock_json:

                mock_ask.return_value = make_claude_response("{}")
                mock_json.return_value = {"data": make_analysis(health_score=85)}

                result = await module.execute(
                    message="Analisa campanhas",
                    db=db,
                    client_slug="clinica-demo",
                )

        assert result["success"] is True
        assert "analysis" in result["data"]
        assert result["data"]["client"] == "clinica-demo"


# ── Testes de health_score ────────────────────────────────────────────────────

class TestHealthScore:

    async def test_health_score_presente_no_resultado(self, setup_m04):
        client, db, patches = setup_m04

        with patches["feedback_loop"], patches["meta_ads"] as m, patches["google_ads"] as g:
            m.get_campaign_insights = AsyncMock(return_value=[])
            g.get_metrics = AsyncMock(return_value={})

            from modules.m04_campanhas.agent import M04Campanhas
            module = M04Campanhas()

            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch.object(module.claude, "extract_json", new_callable=AsyncMock) as mock_json:

                mock_ask.return_value = make_claude_response("{}")
                mock_json.return_value = {"data": make_analysis(health_score=72)}

                result = await module.execute(
                    message="Performance",
                    db=db,
                    client_slug="clinica-demo",
                )

        analysis = result["data"]["analysis"]
        assert "health_score" in analysis
        assert 0 <= analysis["health_score"] <= 100

    async def test_health_score_refletido_na_mensagem(self, setup_m04):
        client, db, patches = setup_m04

        with patches["feedback_loop"], patches["meta_ads"] as m, patches["google_ads"] as g:
            m.get_campaign_insights = AsyncMock(return_value=[])
            g.get_metrics = AsyncMock(return_value={})

            from modules.m04_campanhas.agent import M04Campanhas
            module = M04Campanhas()

            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch.object(module.claude, "extract_json", new_callable=AsyncMock) as mock_json:

                mock_ask.return_value = make_claude_response("{}")
                mock_json.return_value = {"data": make_analysis(health_score=55)}

                result = await module.execute(
                    message="Analisa campanhas",
                    db=db,
                    client_slug="clinica-demo",
                )

        assert "55" in result["message"]


# ── Testes de anomalias ───────────────────────────────────────────────────────

class TestAnomalias:

    async def test_anomalias_retornadas_quando_cpl_alto(self, setup_m04):
        client, db, patches = setup_m04

        anomalias = [
            {"metric": "CPL", "value": 120.0, "expected": 60.0, "severity": "critical"},
        ]

        with patches["feedback_loop"], patches["meta_ads"] as m, patches["google_ads"] as g:
            m.get_campaign_insights = AsyncMock(return_value=[])
            g.get_metrics = AsyncMock(return_value={})

            from modules.m04_campanhas.agent import M04Campanhas
            module = M04Campanhas()

            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch.object(module.claude, "extract_json", new_callable=AsyncMock) as mock_json:

                mock_ask.return_value = make_claude_response("{}")
                mock_json.return_value = {"data": make_analysis(health_score=35, anomalies=anomalias)}

                result = await module.execute(
                    message="Analisa campanhas",
                    db=db,
                    client_slug="clinica-demo",
                )

        analysis = result["data"]["analysis"]
        assert len(analysis["anomalies"]) > 0
        assert analysis["anomalies"][0]["severity"] == "critical"

    async def test_sem_anomalias_quando_tudo_normal(self, setup_m04):
        client, db, patches = setup_m04

        with patches["feedback_loop"], patches["meta_ads"] as m, patches["google_ads"] as g:
            m.get_campaign_insights = AsyncMock(return_value=[])
            g.get_metrics = AsyncMock(return_value={})

            from modules.m04_campanhas.agent import M04Campanhas
            module = M04Campanhas()

            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch.object(module.claude, "extract_json", new_callable=AsyncMock) as mock_json:

                mock_ask.return_value = make_claude_response("{}")
                mock_json.return_value = {"data": make_analysis(anomalies=[])}

                result = await module.execute(
                    message="Analisa campanhas",
                    db=db,
                    client_slug="clinica-demo",
                )

        assert result["data"]["analysis"]["anomalies"] == []


# ── Testes de recomendações ───────────────────────────────────────────────────

class TestRecomendacoes:

    async def test_recomendacoes_presentes_e_priorizadas(self, setup_m04):
        recs = [
            {"action": "Pausar adset X", "priority": 1, "expected_impact": "CPL -20%", "reasoning": "Frequência alta"},
            {"action": "Testar gancho com número", "priority": 2, "expected_impact": "CTR +10%", "reasoning": "Copy genérico"},
        ]

        client, db, patches = setup_m04

        with patches["feedback_loop"], patches["meta_ads"] as m, patches["google_ads"] as g:
            m.get_campaign_insights = AsyncMock(return_value=[])
            g.get_metrics = AsyncMock(return_value={})

            from modules.m04_campanhas.agent import M04Campanhas
            module = M04Campanhas()

            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch.object(module.claude, "extract_json", new_callable=AsyncMock) as mock_json:

                mock_ask.return_value = make_claude_response("{}")
                mock_json.return_value = {"data": make_analysis(recommendations=recs)}

                result = await module.execute(
                    message="Analisa campanhas",
                    db=db,
                    client_slug="clinica-demo",
                )

        recomendacoes = result["data"]["analysis"]["recommendations"]
        assert len(recomendacoes) >= 1
        prioridades = [r["priority"] for r in recomendacoes]
        assert 1 in prioridades


# ── Testes de degradação graciosa (integrações) ───────────────────────────────

class TestDegradacaoGraciosa:
    """
    M04 já tem try/except individuais para Meta e Google.
    Valida que uma fonte fora do ar não cancela a análise.
    """

    async def test_meta_indisponivel_nao_cancela_analise(self, setup_m04):
        client, db, patches = setup_m04

        with patches["feedback_loop"], patches["meta_ads"] as m, patches["google_ads"] as g:
            m.get_campaign_insights = AsyncMock(side_effect=Exception("Meta API down"))
            g.get_metrics = AsyncMock(return_value={})

            from modules.m04_campanhas.agent import M04Campanhas
            module = M04Campanhas()

            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch.object(module.claude, "extract_json", new_callable=AsyncMock) as mock_json:

                mock_ask.return_value = make_claude_response("{}")
                mock_json.return_value = {"data": make_analysis()}

                result = await module.execute(
                    message="Analisa campanhas",
                    db=db,
                    client_slug="clinica-demo",
                )

        assert result["success"] is True, "Meta indisponível não deve cancelar a análise"

    async def test_google_indisponivel_nao_cancela_analise(self, setup_m04):
        client, db, patches = setup_m04

        client.google_ads_id = "customers/123456789"
        db = make_db(client)

        with patches["feedback_loop"], patches["meta_ads"] as m, patches["google_ads"] as g:
            m.get_campaign_insights = AsyncMock(return_value=[])
            g.get_metrics = AsyncMock(side_effect=Exception("Google Ads API timeout"))

            from modules.m04_campanhas.agent import M04Campanhas
            module = M04Campanhas()

            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch.object(module.claude, "extract_json", new_callable=AsyncMock) as mock_json:

                mock_ask.return_value = make_claude_response("{}")
                mock_json.return_value = {"data": make_analysis()}

                result = await module.execute(
                    message="Analisa campanhas",
                    db=db,
                    client_slug="clinica-demo",
                )

        assert result["success"] is True, "Google indisponível não deve cancelar a análise"


# ── Testes de resolução de cliente ────────────────────────────────────────────

class TestResolucaoCliente:

    async def test_cliente_nao_encontrado_retorna_success_false(self):
        db = make_db(client=None)

        with patch("modules.m04_campanhas.agent.FeedbackLoop"):
            from modules.m04_campanhas.agent import M04Campanhas
            module = M04Campanhas()
            result = await module.execute(
                message="Analisa campanhas",
                db=db,
                client_slug="cliente-inexistente",
            )

        assert result["success"] is False


# ── Testes de can_handle ──────────────────────────────────────────────────────

class TestCanHandle:

    async def test_alta_confianca_para_multiplas_keywords(self):
        from modules.m04_campanhas.agent import M04Campanhas
        module = M04Campanhas()
        score = await module.can_handle("Quero analisar a performance das campanhas de Meta Ads")
        assert score >= 0.75

    async def test_confianca_maxima_para_evento_scheduler(self):
        from modules.m04_campanhas.agent import M04Campanhas
        module = M04Campanhas()
        score = await module.can_handle("", context={"event_type": "campanhas_diarias"})
        assert score >= 0.9

    async def test_confianca_zero_para_assunto_nao_relacionado(self):
        from modules.m04_campanhas.agent import M04Campanhas
        module = M04Campanhas()
        score = await module.can_handle("Gera um roteiro de implante para reels")
        assert score == 0.0

    async def test_confianca_media_para_uma_keyword(self):
        from modules.m04_campanhas.agent import M04Campanhas
        module = M04Campanhas()
        score = await module.can_handle("Quero ver o CPL")
        assert 0.0 < score < 0.9
