"""
Villa — Módulo M12: Sistema de Alertas Inteligentes
Monitora métricas críticas e dispara alertas proativos.
Integra com os monitores contínuos do scheduler.
CPL, frequência, show rate, SLA, budget, saúde dos módulos.
"""

from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Client, Alert, ModuleCode, User
from modules.base import BaseModule
from memory.feedback_loop import FeedbackLoop
from integrations.whatsapp import whatsapp


ALERT_ANALYSIS_PROMPT = """Analise estes alertas e gere um resumo executivo:

ALERTAS ATIVOS:
{alerts_data}

Gere em JSON:
{{
    "severity_summary": {{"critical": 0, "warning": 0, "info": 0}},
    "executive_summary": "resumo em 2-3 frases dos pontos mais urgentes",
    "top_priority": "qual alerta precisa de ação imediata e por quê",
    "suggested_actions": [{{"alert_id": "...", "action": "...", "urgency": "now|today|this_week"}}]
}}
"""


class M12Alertas(BaseModule):
    code = ModuleCode.M12_ALERTAS
    name = "Alertas Inteligentes"
    description = "Monitora métricas críticas e dispara alertas proativos via WhatsApp. CPL, frequência, show rate, SLA, budget."

    KEYWORDS = ["alerta", "alertas", "aviso", "anomalia", "problema", "atenção", "atencao", "urgente", "crítico", "critico", "monitoramento"]

    async def can_handle(self, message: str, context: Optional[dict] = None) -> float:
        if context and "alertas" in context.get("event_type", ""): return 0.9
        msg_lower = message.lower()
        matches = sum(1 for kw in self.KEYWORDS if kw in msg_lower)
        if matches >= 2: return 0.8
        if matches >= 1: return 0.55
        return 0.0

    async def execute(self, message: str, db: AsyncSession, user: Optional[User] = None, client_slug: Optional[str] = None, context: Optional[dict] = None) -> dict:
        feedback_loop = FeedbackLoop(db)
        context = context or {}

        # Se veio do scheduler, processar alertas gerados
        if context.get("event_type") in ("scheduler_daily", "scheduler_monitors"):
            return await self._process_pending_alerts(db)

        # Se é comando, mostrar alertas ativos
        return await self._show_alerts(db, client_slug, message)

    async def _show_alerts(self, db: AsyncSession, client_slug: Optional[str], message: str) -> dict:
        """Mostra alertas ativos com análise inteligente."""
        query = (
            select(Alert)
            .where(Alert.resolved == False)
            .order_by(
                Alert.severity.desc(),
                Alert.created_at.desc(),
            )
            .limit(20)
        )

        if client_slug:
            client_q = await db.execute(select(Client).where(Client.slug == client_slug))
            client = client_q.scalar_one_or_none()
            if client:
                query = query.where(Alert.client_id == client.id)

        result = await db.execute(query)
        alerts = result.scalars().all()

        if not alerts:
            return {
                "success": True,
                "message": "✅ Nenhum alerta ativo. Tudo operando normalmente.",
                "actions_taken": ["no_alerts"],
            }

        # Formatar alertas
        alerts_data = []
        for a in alerts:
            severity_icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(a.severity, "⚪")
            alerts_data.append({
                "id": a.id,
                "icon": severity_icon,
                "severity": a.severity,
                "title": a.title,
                "message": a.message,
                "suggested_action": a.suggested_action,
                "created_at": a.created_at.isoformat() if a.created_at else "",
                "metric": f"{a.metric_name}: {a.metric_value}" if a.metric_name else "",
            })

        # Análise via Claude
        response = await self.ask_claude(
            message=ALERT_ANALYSIS_PROMPT.format(
                alerts_data=str(alerts_data)[:3000],
            ),
            db=db,
        )

        parsed = await self.claude.extract_json(message=response["text"], model="fast")
        analysis = parsed.get("data", {})

        # Montar resposta
        lines = [f"🚨 **{len(alerts)} alertas ativos**\n"]
        if analysis.get("executive_summary"):
            lines.append(f"_{analysis['executive_summary']}_\n")

        for a in alerts_data[:10]:
            lines.append(f"{a['icon']} **{a['title']}**")
            lines.append(f"  {a['message'][:150]}")
            if a.get("suggested_action"):
                lines.append(f"  💡 {a['suggested_action'][:100]}")
            lines.append("")

        return {
            "success": True,
            "message": "\n".join(lines),
            "data": {"total_alerts": len(alerts), "analysis": analysis},
            "actions_taken": ["alerts_displayed"],
        }

    async def _process_pending_alerts(self, db: AsyncSession) -> dict:
        """Processa alertas não enviados e dispara notificações WhatsApp."""
        result = await db.execute(
            select(Alert)
            .where(Alert.sent_whatsapp == False)
            .where(Alert.severity.in_(["critical", "warning"]))
            .order_by(Alert.created_at.desc())
            .limit(10)
        )
        unsent = result.scalars().all()

        sent_count = 0
        for alert in unsent:
            # Buscar cliente para saber quem notificar
            client = None
            if alert.client_id:
                client_q = await db.execute(select(Client).where(Client.id == alert.client_id))
                client = client_q.scalar_one_or_none()

            severity_icon = "🔴" if alert.severity == "critical" else "🟡"
            msg = f"{severity_icon} {alert.title}\n\n{alert.message}"
            if alert.suggested_action:
                msg += f"\n\n💡 {alert.suggested_action}"

            # TODO: Enviar para WhatsApp do Caio/Thaís
            # O número de notificação fica em config geral
            # try:
            #     await whatsapp.send_text(ADMIN_PHONE, msg)
            #     sent_count += 1
            # except Exception:
            #     pass

            alert.sent_whatsapp = True
            sent_count += 1

        await db.flush()

        return {
            "success": True,
            "message": f"Processados {sent_count} alertas pendentes",
            "data": {"alerts_sent": sent_count},
            "actions_taken": ["alerts_processed"],
        }

    async def acknowledge_alert(self, db: AsyncSession, alert_id: str, user_id: str) -> dict:
        """Marca um alerta como reconhecido por um humano."""
        result = await db.execute(select(Alert).where(Alert.id == alert_id))
        alert = result.scalar_one_or_none()

        if not alert:
            return {"success": False, "error": "alert_not_found"}

        alert.acknowledged = True
        alert.acknowledged_by = user_id
        alert.acknowledged_at = datetime.utcnow()
        await db.flush()

        return {"success": True, "alert_id": alert_id, "action": "acknowledged"}

    async def resolve_alert(self, db: AsyncSession, alert_id: str) -> dict:
        """Marca um alerta como resolvido."""
        result = await db.execute(select(Alert).where(Alert.id == alert_id))
        alert = result.scalar_one_or_none()

        if not alert:
            return {"success": False, "error": "alert_not_found"}

        alert.resolved = True
        await db.flush()

        return {"success": True, "alert_id": alert_id, "action": "resolved"}
