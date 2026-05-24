"""
Villa — Módulo M07: Retroalimentação Comercial ↔ Marketing
Automatiza o loop de feedback entre SDR/comercial e time de marketing.
Cruza dados de conversão com performance de campanhas.
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Client, Lead, LeadStatus, ModuleCode, User
from memory.feedback_loop import FeedbackLoop
from modules.base import BaseModule

ANALYSIS_PROMPT = """Analise o loop comercial ↔ marketing deste cliente:

CLIENTE: {client_name} ({specialty})
PERÍODO: últimos 7 dias

DADOS DO FUNIL:
- Leads captados: {total_leads}
- Qualificados: {qualified} ({qual_rate}%)
- Agendados: {scheduled}
- Fechados: {won} (ticket médio: R${avg_ticket})
- Perdidos: {lost}

MOTIVOS DE PERDA:
{loss_reasons}

CAMPANHAS QUE GERARAM LEADS FECHADOS:
{winning_campaigns}

CAMPANHAS QUE GERARAM LEADS PERDIDOS:
{losing_campaigns}

{memory_context}

Gere insights em JSON:
{{
    "funnel_health": "healthy|attention|critical",
    "bottleneck": "descrição de onde o funil está travando",
    "marketing_feedback": ["insights para melhorar campanhas"],
    "commercial_feedback": ["insights para melhorar abordagem comercial"],
    "recommended_actions": [{{"action": "...", "owner": "marketing|comercial|ambos", "priority": 1-3}}]
}}
"""


class M07Retroalimentacao(BaseModule):
    code = ModuleCode.M07_RETROALIMENTACAO
    name = "Retroalimentação"
    description = "Automatiza o loop de feedback entre comercial e marketing, cruzando dados de conversão com performance de campanhas."

    KEYWORDS = [
        "retroalimentação",
        "retroalimentacao",
        "feedback",
        "funil",
        "conversão",
        "conversao",
        "comercial e marketing",
        "por que não fechou",
        "motivo de perda",
    ]

    async def can_handle(self, message: str, context: dict | None = None) -> float:
        if context and "retroalimentacao" in context.get("event_type", ""):
            return 0.9
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

        week_ago = datetime.utcnow() - timedelta(days=7)

        # Dados do funil
        leads_q = await db.execute(
            select(Lead).where(Lead.client_id == client.id).where(Lead.created_at >= week_ago)
        )
        leads = leads_q.scalars().all()

        total = len(leads)
        qualified = sum(
            1
            for l in leads
            if l.status
            in (LeadStatus.QUALIFIED, LeadStatus.SCHEDULED, LeadStatus.PROPOSAL, LeadStatus.WON)
        )
        scheduled = sum(1 for l in leads if l.status == LeadStatus.SCHEDULED)
        won = sum(1 for l in leads if l.status == LeadStatus.WON)
        lost = sum(1 for l in leads if l.status == LeadStatus.LOST)
        won_values = [l.deal_value for l in leads if l.status == LeadStatus.WON and l.deal_value]
        avg_ticket = sum(won_values) / len(won_values) if won_values else 0
        loss_reasons = [
            l.disqualification_reason or "não informado"
            for l in leads
            if l.status == LeadStatus.LOST
        ]

        # Cruzar com campanhas
        winning_utms = [
            l.utm_campaign for l in leads if l.status == LeadStatus.WON and l.utm_campaign
        ]
        losing_utms = [
            l.utm_campaign for l in leads if l.status == LeadStatus.LOST and l.utm_campaign
        ]

        memory = await feedback_loop.build_context(
            module=self.code, action="retroalimentacao", client_slug=client.slug
        )

        response = await self.ask_claude(
            message=ANALYSIS_PROMPT.format(
                client_name=client.name,
                specialty=client.specialty or "odontologia",
                total_leads=total,
                qualified=qualified,
                qual_rate=round(qualified / total * 100, 1) if total else 0,
                scheduled=scheduled,
                won=won,
                avg_ticket=f"{avg_ticket:,.2f}",
                lost=lost,
                loss_reasons="\n".join(f"- {r}" for r in loss_reasons[:10]) or "nenhum registrado",
                winning_campaigns=", ".join(set(winning_utms)) or "sem dados UTM",
                losing_campaigns=", ".join(set(losing_utms)) or "sem dados UTM",
                memory_context=memory["prompt_injection"],
            ),
            db=db,
            client_slug=client.slug,
        )

        parsed = await self.claude.extract_json(message=response["text"], model="fast")
        analysis = parsed.get("data", {})

        await feedback_loop.record_decision(
            module=self.code,
            action="retroalimentacao",
            input_data={"client": client.slug, "total_leads": total, "won": won, "lost": lost},
            output_data=analysis,
            client_slug=client.slug,
        )

        return {
            "success": True,
            "message": f"Retroalimentação {client.name}: {analysis.get('bottleneck', 'análise completa gerada')}",
            "data": {"client": client.slug, "analysis": analysis},
            "actions_taken": ["retroalimentacao_complete"],
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
