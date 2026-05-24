"""
Villa — Módulo M08: Onboarding Automatizado de Clientes
Automatiza as 10 etapas de ativação de novo cliente na WebXP.
Checklist, cobrança de prazos, análise preliminar de mapeamento.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Client, ModuleCode, User
from memory.feedback_loop import FeedbackLoop
from modules.base import BaseModule

logger = logging.getLogger(__name__)

ONBOARDING_STEPS = [
    {"step": 1, "name": "Contrato assinado", "owner": "comercial", "sla_days": 0},
    {"step": 2, "name": "Acesso ao CRM configurado", "owner": "operacional", "sla_days": 1},
    {"step": 3, "name": "Questionário de mapeamento enviado", "owner": "villa", "sla_days": 1},
    {"step": 4, "name": "Mapeamento preenchido pelo cliente", "owner": "cliente", "sla_days": 5},
    {"step": 5, "name": "Análise do mapeamento", "owner": "villa", "sla_days": 1},
    {"step": 6, "name": "Pipeline Kommo criado", "owner": "villa", "sla_days": 1},
    {
        "step": 7,
        "name": "InLead configurado com mapeamento de campos",
        "owner": "operacional",
        "sla_days": 2,
    },
    {"step": 8, "name": "Primeiros roteiros gerados", "owner": "villa", "sla_days": 3},
    {"step": 9, "name": "Campanhas configuradas e ativas", "owner": "performance", "sla_days": 3},
    {"step": 10, "name": "Operação em ritmo normal", "owner": "todos", "sla_days": 0},
]

ANALYSIS_PROMPT = """Analise o mapeamento estratégico deste novo cliente da WebXP:

CLIENTE: {client_name}
ESPECIALIDADE: {specialty}

MAPEAMENTO:
{mapping_data}

Gere em JSON:
{{
    "icp_summary": "resumo do perfil ideal de cliente/paciente",
    "main_offer": "principal serviço/curso oferecido",
    "differentiators": ["diferenciais do profissional"],
    "suggested_tone": "tom de voz recomendado para comunicação",
    "suggested_hooks": ["3 ideias de ganchos para primeiros roteiros"],
    "initial_thresholds": {{"cpl_max": 0, "ctr_min": 0}},
    "risks": ["possíveis desafios no onboarding"],
    "recommended_approach": "abordagem recomendada para as campanhas"
}}
"""


class M08Onboarding(BaseModule):
    code = ModuleCode.M08_ONBOARDING
    name = "Onboarding de Clientes"
    description = "Automatiza as 10 etapas de ativação de novo cliente: mapeamento, análise, configuração CRM, primeiros roteiros."

    KEYWORDS = [
        "onboarding",
        "novo cliente",
        "ativar cliente",
        "mapeamento",
        "checklist",
        "ativação",
        "ativacao",
    ]

    async def can_handle(self, message: str, context: dict | None = None) -> float:
        msg_lower = message.lower()
        matches = sum(1 for kw in self.KEYWORDS if kw in msg_lower)
        if matches >= 2:
            return 0.85
        if matches >= 1:
            return 0.6
        return 0.0

    async def execute(
        self,
        message: str,
        db: AsyncSession,
        user: User | None = None,
        client_slug: str | None = None,
        context: dict | None = None,
    ) -> dict:
        feedback_loop = FeedbackLoop(db)

        client = await self._resolve_client(db, client_slug, message)
        if not client:
            return {"success": False, "message": "Cliente não identificado.", "actions_taken": []}

        # Verificar etapa atual do onboarding
        onboarding_data = (client.config or {}).get("onboarding", {})
        current_step = onboarding_data.get("current_step", 1)
        completed_steps = onboarding_data.get("completed_steps", [])
        start_date = onboarding_data.get("start_date")

        # Montar status do checklist
        checklist = []
        for step in ONBOARDING_STEPS:
            status = (
                "✅"
                if step["step"] in completed_steps
                else "⏳"
                if step["step"] == current_step
                else "⬜"
            )
            checklist.append(f"{status} Etapa {step['step']}: {step['name']} ({step['owner']})")

        try:
            # Se tem mapeamento pendente de análise (etapa 5)
            actions = ["checklist_generated"]
            analysis = None

            if current_step == 5:
                mapping_data = onboarding_data.get("mapping_data", {})
                if mapping_data:
                    response = await self.ask_claude(
                        message=ANALYSIS_PROMPT.format(
                            client_name=client.name,
                            specialty=client.specialty or "odontologia",
                            mapping_data=str(mapping_data)[:3000],
                        ),
                        db=db,
                        client_slug=client.slug,
                    )
                    parsed = await self.claude.extract_json(message=response["text"], model="fast")
                    analysis = parsed.get("data", {})
                    actions.append("mapping_analyzed")

            # Verificar SLA de etapas
            sla_alerts = []
            if start_date:
                start = datetime.fromisoformat(start_date)
                for step in ONBOARDING_STEPS:
                    if step["step"] not in completed_steps and step["step"] <= current_step:
                        expected_date = start + timedelta(
                            days=sum(s["sla_days"] for s in ONBOARDING_STEPS[: step["step"]])
                        )
                        if datetime.utcnow() > expected_date:
                            days_late = (datetime.utcnow() - expected_date).days
                            sla_alerts.append(
                                f"Etapa {step['step']} ({step['name']}) atrasada {days_late} dia(s)"
                            )

            msg = f"📋 **Onboarding — {client.name}**\n\n" + "\n".join(checklist)
            if sla_alerts:
                msg += "\n\n⚠️ **Atrasos:**\n" + "\n".join(f"  • {a}" for a in sla_alerts)
            if analysis:
                msg += f"\n\n💡 **Análise do mapeamento:**\n{analysis.get('recommended_approach', '')}"

            await feedback_loop.record_decision(
                module=self.code,
                action="check_onboarding",
                input_data={"client": client.slug, "current_step": current_step},
                output_data={"completed": len(completed_steps), "sla_alerts": len(sla_alerts)},
                client_slug=client.slug,
            )

            return {
                "success": True,
                "message": msg,
                "data": {
                    "client": client.slug,
                    "current_step": current_step,
                    "analysis": analysis,
                    "sla_alerts": sla_alerts,
                },
                "actions_taken": actions,
            }

        except Exception as e:
            logger.exception("[M08] Erro em execute(): %s", e)
            await self.increment_execution(db, success=False)
            return {
                "success": False,
                "message": "Erro interno. Tente novamente em instantes.",
                "actions_taken": ["error"],
                "data": {"error": str(e)},
            }

    async def _resolve_client(self, db, slug, message):
        if slug:
            r = await db.execute(select(Client).where(Client.slug == slug))
            return r.scalar_one_or_none()
        r = await db.execute(select(Client))
        for c in r.scalars().all():
            if c.name.lower() in message.lower() or c.slug.lower() in message.lower():
                return c
        return None
