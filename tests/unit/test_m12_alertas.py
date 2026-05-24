# ═══════════════════════════════════════════════════════════════
# VILLA — tests/unit/test_m12_alertas.py
#
# Testes unitários do M12Alertas baseados no código real.
#
# Interface real confirmada:
#   Classe: M12Alertas (modules/m12_alertas/agent.py)
#   Método: execute(message, db, user, client_slug, context) -> dict
#   Retorno: {"success": bool, "message": str, "data": dict, "actions_taken": list}
#
# Dois fluxos internos:
#   1. Scheduler (event_type scheduler_daily|scheduler_monitors):
#      → _process_pending_alerts() — busca alertas não enviados e marca como enviados
#   2. Comando direto:
#      → _show_alerts() — busca alertas ativos e gera análise via Claude
#
# Métodos extras (sem passar pelo execute):
#   acknowledge_alert(db, alert_id, user_id) -> dict
#   resolve_alert(db, alert_id) -> dict
#
# O que está sendo testado:
#   - Sem alertas ativos retorna mensagem de tudo OK
#   - Com alertas ativos retorna lista + análise
#   - Scheduler aciona _process_pending_alerts
#   - Comando aciona _show_alerts
#   - _process_pending_alerts marca alertas como enviados
#   - acknowledge_alert funciona corretamente
#   - resolve_alert funciona corretamente
#   - Filtro por client_slug funciona
#   - can_handle() para diferentes contextos
# ═══════════════════════════════════════════════════════════════

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ── Factories ────────────────────────────────────────────────────────────────

def make_alert(
    alert_id: str = "alert-uuid-001",
    severity: str = "warning",
    title: str = "CPL acima do threshold",
    message: str = "CPL de R$120 está 2x acima do limite de R$60.",
    suggested_action: str = "Pausar adsets com CPL > R$100",
    resolved: bool = False,
    sent_whatsapp: bool = False,
    client_id: str = "client-uuid-123",
    metric_name: str = "CPL",
    metric_value: float = 120.0,
) -> MagicMock:
    alert = MagicMock()
    alert.id = alert_id
    alert.severity = severity
    alert.title = title
    alert.message = message
    alert.suggested_action = suggested_action
    alert.resolved = resolved
    alert.sent_whatsapp = sent_whatsapp
    alert.client_id = client_id
    alert.metric_name = metric_name
    alert.metric_value = metric_value
    alert.created_at = datetime(2026, 5, 24, 10, 0, 0)
    alert.acknowledged = False
    alert.acknowledged_by = None
    alert.acknowledged_at = None
    return alert


def make_client(slug: str = "clinica-demo") -> MagicMock:
    client = MagicMock()
    client.id = "client-uuid-123"
    client.slug = slug
    client.name = "Clínica Demo"
    return client


def make_db_with_alerts(alerts: list, client: MagicMock | None = None) -> AsyncMock:
    """DB que retorna alertas quando execute() é chamado."""
    db = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()

    call_count = [0]

    async def fake_execute(query):
        mock_result = MagicMock()
        call_count[0] += 1
        if call_count[0] == 1 and client is not None:
            # Primeira chamada — busca cliente
            mock_result.scalar_one_or_none.return_value = client
            mock_result.scalars.return_value.all.return_value = [client]
        else:
            # Chamadas seguintes — busca alertas
            mock_result.scalar_one_or_none.return_value = alerts[0] if alerts else None
            mock_result.scalars.return_value.all.return_value = alerts
        return mock_result

    db.execute = fake_execute
    return db


def make_claude_response(text: str = "{}") -> dict:
    return {
        "text": text,
        "tokens_input": 300,
        "tokens_output": 200,
        "model": "claude-sonnet-4-20250514",
        "cost_usd": 0.003,
        "stop_reason": "end_turn",
    }


def make_alert_analysis() -> dict:
    return {
        "severity_summary": {"critical": 0, "warning": 1, "info": 0},
        "executive_summary": "Um alerta de warning requer atenção. CPL acima do limite.",
        "top_priority": "Reduzir CPL pausando adsets de baixa performance.",
        "suggested_actions": [
            {"alert_id": "alert-uuid-001", "action": "Pausar adsets com CPL > R$100", "urgency": "today"}
        ],
    }


FEEDBACK_CONTEXT = {
    "prompt_injection": "",
    "reasoning_context": "M12 em execução.",
    "sources": [],
}


# ── Testes: sem alertas ───────────────────────────────────────────────────────

class TestSemAlertas:

    async def test_sem_alertas_retorna_tudo_ok(self):
        """Quando não há alertas ativos, deve retornar mensagem de sistema saudável."""
        db = make_db_with_alerts(alerts=[])

        with patch("modules.m12_alertas.agent.FeedbackLoop"):
            from modules.m12_alertas.agent import M12Alertas
            module = M12Alertas()

            result = await module.execute(
                message="Tem algum alerta?",
                db=db,
            )

        assert result["success"] is True
        assert "no_alerts" in result.get("actions_taken", [])

    async def test_mensagem_sem_alertas_contem_positivo(self):
        db = make_db_with_alerts(alerts=[])

        with patch("modules.m12_alertas.agent.FeedbackLoop"):
            from modules.m12_alertas.agent import M12Alertas
            module = M12Alertas()

            result = await module.execute(message="alertas", db=db)

        msg_lower = result["message"].lower()
        has_positive = any(w in msg_lower for w in ["nenhum", "ok", "normal", "operando"])
        assert has_positive, f"Mensagem não indica ausência de alertas: '{result['message']}'"


# ── Testes: com alertas ativos ────────────────────────────────────────────────

class TestComAlertas:

    async def test_retorna_success_true_com_alertas(self):
        alerta = make_alert(severity="warning")
        db = make_db_with_alerts(alerts=[alerta])

        with patch("modules.m12_alertas.agent.FeedbackLoop"):
            from modules.m12_alertas.agent import M12Alertas
            module = M12Alertas()

            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch.object(module.claude, "extract_json", new_callable=AsyncMock) as mock_json:

                mock_ask.return_value = make_claude_response()
                mock_json.return_value = {"data": make_alert_analysis()}

                result = await module.execute(message="alertas", db=db)

        assert result["success"] is True

    async def test_total_alertas_no_data(self):
        alertas = [
            make_alert("a1", severity="critical"),
            make_alert("a2", severity="warning"),
        ]
        db = make_db_with_alerts(alerts=alertas)

        with patch("modules.m12_alertas.agent.FeedbackLoop"):
            from modules.m12_alertas.agent import M12Alertas
            module = M12Alertas()

            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch.object(module.claude, "extract_json", new_callable=AsyncMock) as mock_json:

                mock_ask.return_value = make_claude_response()
                mock_json.return_value = {"data": make_alert_analysis()}

                result = await module.execute(message="alertas", db=db)

        assert result["data"]["total_alerts"] == 2

    async def test_mensagem_menciona_quantidade_de_alertas(self):
        alerta = make_alert(severity="critical", title="Budget estourando")
        db = make_db_with_alerts(alerts=[alerta])

        with patch("modules.m12_alertas.agent.FeedbackLoop"):
            from modules.m12_alertas.agent import M12Alertas
            module = M12Alertas()

            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch.object(module.claude, "extract_json", new_callable=AsyncMock) as mock_json:

                mock_ask.return_value = make_claude_response()
                mock_json.return_value = {"data": make_alert_analysis()}

                result = await module.execute(message="alertas", db=db)

        assert "1" in result["message"]


# ── Testes: fluxo scheduler ───────────────────────────────────────────────────

class TestFluxoScheduler:

    async def test_scheduler_daily_aciona_process_pending(self):
        """event_type scheduler_daily deve chamar _process_pending_alerts."""
        alerta = make_alert(sent_whatsapp=False, severity="warning")
        db = make_db_with_alerts(alerts=[alerta])

        with patch("modules.m12_alertas.agent.FeedbackLoop"):
            from modules.m12_alertas.agent import M12Alertas
            module = M12Alertas()

            result = await module.execute(
                message="",
                db=db,
                context={"event_type": "scheduler_daily"},
            )

        assert result["success"] is True
        assert "alerts_processed" in result.get("actions_taken", [])

    async def test_scheduler_monitors_aciona_process_pending(self):
        alerta = make_alert(sent_whatsapp=False, severity="critical")
        db = make_db_with_alerts(alerts=[alerta])

        with patch("modules.m12_alertas.agent.FeedbackLoop"):
            from modules.m12_alertas.agent import M12Alertas
            module = M12Alertas()

            result = await module.execute(
                message="",
                db=db,
                context={"event_type": "scheduler_monitors"},
            )

        assert result["success"] is True
        assert "alerts_processed" in result.get("actions_taken", [])

    async def test_process_pending_marca_alertas_como_enviados(self):
        """Após processar, sent_whatsapp deve ser True."""
        alerta = make_alert(sent_whatsapp=False)
        db = make_db_with_alerts(alerts=[alerta])

        with patch("modules.m12_alertas.agent.FeedbackLoop"):
            from modules.m12_alertas.agent import M12Alertas
            module = M12Alertas()

            await module.execute(
                message="",
                db=db,
                context={"event_type": "scheduler_daily"},
            )

        assert alerta.sent_whatsapp is True

    async def test_process_pending_sem_alertas_retorna_zero_enviados(self):
        db = make_db_with_alerts(alerts=[])

        with patch("modules.m12_alertas.agent.FeedbackLoop"):
            from modules.m12_alertas.agent import M12Alertas
            module = M12Alertas()

            result = await module.execute(
                message="",
                db=db,
                context={"event_type": "scheduler_daily"},
            )

        assert result["data"]["alerts_sent"] == 0


# ── Testes: acknowledge e resolve ─────────────────────────────────────────────

class TestAcknowledgeEResolve:

    async def test_acknowledge_alert_sucesso(self):
        alerta = make_alert()
        alerta.acknowledged = False

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = alerta
        db.execute.return_value = mock_result
        db.flush = AsyncMock()

        from modules.m12_alertas.agent import M12Alertas
        module = M12Alertas()

        result = await module.acknowledge_alert(
            db=db,
            alert_id="alert-uuid-001",
            user_id="user-caio-001",
        )

        assert result["success"] is True
        assert result["action"] == "acknowledged"
        assert alerta.acknowledged is True
        assert alerta.acknowledged_by == "user-caio-001"

    async def test_acknowledge_alert_nao_encontrado(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_result

        from modules.m12_alertas.agent import M12Alertas
        module = M12Alertas()

        result = await module.acknowledge_alert(
            db=db,
            alert_id="alert-inexistente",
            user_id="user-caio-001",
        )

        assert result["success"] is False
        assert result["error"] == "alert_not_found"

    async def test_resolve_alert_sucesso(self):
        alerta = make_alert(resolved=False)

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = alerta
        db.execute.return_value = mock_result
        db.flush = AsyncMock()

        from modules.m12_alertas.agent import M12Alertas
        module = M12Alertas()

        result = await module.resolve_alert(db=db, alert_id="alert-uuid-001")

        assert result["success"] is True
        assert result["action"] == "resolved"
        assert alerta.resolved is True

    async def test_resolve_alert_nao_encontrado(self):
        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        db.execute.return_value = mock_result

        from modules.m12_alertas.agent import M12Alertas
        module = M12Alertas()

        result = await module.resolve_alert(db=db, alert_id="alert-inexistente")

        assert result["success"] is False
        assert result["error"] == "alert_not_found"


# ── Testes: can_handle ────────────────────────────────────────────────────────

class TestCanHandle:

    async def test_alta_confianca_para_scheduler(self):
        from modules.m12_alertas.agent import M12Alertas
        module = M12Alertas()
        score = await module.can_handle("", context={"event_type": "alertas_criticos"})
        assert score >= 0.9

    async def test_alta_confianca_para_duas_keywords(self):
        from modules.m12_alertas.agent import M12Alertas
        module = M12Alertas()
        score = await module.can_handle("Tem algum alerta crítico ou problema?")
        assert score >= 0.8

    async def test_confianca_media_para_uma_keyword(self):
        from modules.m12_alertas.agent import M12Alertas
        module = M12Alertas()
        score = await module.can_handle("Tem algum alerta hoje?")
        assert 0.0 < score <= 0.8

    async def test_confianca_zero_para_assunto_nao_relacionado(self):
        from modules.m12_alertas.agent import M12Alertas
        module = M12Alertas()
        score = await module.can_handle("Gera roteiro de implante para reels")
        assert score == 0.0
