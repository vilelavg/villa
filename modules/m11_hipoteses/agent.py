"""
Villa — Módulo M11: Geração de Hipóteses de Criativos
A partir de dados de performance do M4, sugere variações de
gancho, copy e visual para novos criativos com teste A/B.
"""

import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Campaign, Client, ModuleCode, Roteiro, RoteiroStatus, User
from memory.feedback_loop import FeedbackLoop
from modules.base import BaseModule

logger = logging.getLogger(__name__)

HYPOTHESIS_PROMPT = """Gere hipóteses de criativos baseadas nos dados de performance:

CLIENTE: {client_name} ({specialty})

TOP CRIATIVOS (melhores performance):
{top_performers}

CRIATIVOS QUE NÃO PERFORMARAM:
{low_performers}

ROTEIROS APROVADOS COM FEEDBACK:
{approved_roteiros}

PADRÕES IDENTIFICADOS:
{patterns}

{memory_context}

Gere {count} hipóteses de novos criativos em JSON:
{{
    "hypotheses": [
        {{
            "title": "nome descritivo da hipótese",
            "hook_idea": "ideia de gancho",
            "approach": "número|pergunta|polêmica|contraste|depoimento|antes_depois",
            "rationale": "por que essa hipótese deve funcionar (baseado nos dados)",
            "expected_impact": "o que se espera melhorar",
            "test_against": "contra qual criativo atual testar",
            "priority": 1-5
        }}
    ],
    "overall_insight": "insight geral sobre o que está funcionando e o que não está"
}}
"""


class M11Hipoteses(BaseModule):
    code = ModuleCode.M11_HIPOTESES
    name = "Hipóteses de Criativos"
    description = "Gera hipóteses de novos criativos baseadas em dados de performance, para teste A/B fundamentado em dados."

    KEYWORDS = [
        "hipótese",
        "hipotese",
        "hipóteses",
        "teste ab",
        "teste a/b",
        "variação",
        "variacao",
        "criativo novo",
        "ideias de criativo",
        "o que testar",
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

        # Buscar roteiros aprovados com performance
        roteiros_q = await db.execute(
            select(Roteiro)
            .where(Roteiro.client_id == client.id)
            .where(Roteiro.status == RoteiroStatus.APPROVED)
            .order_by(Roteiro.created_at.desc())
            .limit(10)
        )
        roteiros = roteiros_q.scalars().all()

        top_performers = []
        low_performers = []
        for r in roteiros:
            perf = r.performance_data or {}
            entry = {
                "title": r.title,
                "hook": r.hook[:100],
                "score": r.overall_score,
                "feedback": r.human_feedback or "",
                "ctr": perf.get("ctr"),
                "views": perf.get("views"),
            }
            if r.overall_score and r.overall_score >= 7.5:
                top_performers.append(entry)
            elif r.overall_score and r.overall_score < 6.5:
                low_performers.append(entry)

        # Buscar campanhas ativas
        campaigns_q = await db.execute(
            select(Campaign)
            .where(Campaign.client_id == client.id)
            .where(Campaign.status == "active")
        )
        campaigns = campaigns_q.scalars().all()

        # Identificar padrões
        patterns = []
        hooks_with_numbers = sum(1 for r in roteiros if any(c.isdigit() for c in (r.hook or "")))
        if hooks_with_numbers > len(roteiros) // 2:
            patterns.append("Ganchos com números tendem a performar melhor neste cliente")
        if top_performers:
            patterns.append(
                f"Média de score dos top performers: {sum(t.get('score', 0) for t in top_performers) / len(top_performers):.1f}"
            )

        try:
            # Memória
            memory = await feedback_loop.build_context(
                module=self.code,
                action="gerar_hipoteses",
                client_slug=client.slug,
            )

            response = await self.ask_claude(
                message=HYPOTHESIS_PROMPT.format(
                    client_name=client.name,
                    specialty=client.specialty or "odontologia",
                    top_performers=str(top_performers[:5])[:1500],
                    low_performers=str(low_performers[:3])[:800],
                    approved_roteiros=str(
                        [
                            {"hook": r.hook[:80], "feedback": r.human_feedback or ""}
                            for r in roteiros[:5]
                        ]
                    )[:1000],
                    patterns="\n".join(f"- {p}" for p in patterns)
                    or "Sem padrões identificados ainda (dados insuficientes)",
                    count=5,
                    memory_context=memory["prompt_injection"],
                ),
                db=db,
                client_slug=client.slug,
            )

            parsed = await self.claude.extract_json(message=response["text"], model="fast")
            hypotheses = parsed.get("data", {})

            await feedback_loop.record_decision(
                module=self.code,
                action="gerar_hipoteses",
                input_data={"client": client.slug, "roteiros_analyzed": len(roteiros)},
                output_data={"hypotheses_count": len(hypotheses.get("hypotheses", []))},
                reasoning=memory["reasoning_context"],
                client_slug=client.slug,
            )

            # Formatar resposta
            hyps = hypotheses.get("hypotheses", [])
            lines = [f"💡 **Hipóteses de Criativos — {client.name}**\n"]
            if hypotheses.get("overall_insight"):
                lines.append(f"_{hypotheses['overall_insight']}_\n")
            for h in hyps[:5]:
                lines.append(f"**{h.get('priority', '?')}. {h.get('title', '')}**")
                lines.append(f"  Gancho: {h.get('hook_idea', '')}")
                lines.append(f"  Abordagem: {h.get('approach', '')}")
                lines.append(f"  Justificativa: {h.get('rationale', '')}")
                lines.append("")

            return {
                "success": True,
                "message": "\n".join(lines),
                "data": {"client": client.slug, "hypotheses": hypotheses},
                "actions_taken": ["hypotheses_generated"],
            }

        except Exception as e:
            logger.exception("[M11] Erro em execute(): %s", e)
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
