"""
Villa — Módulo M04: Análise de Campanhas e Otimizações
Analisa performance de Meta Ads + Google Ads, identifica anomalias,
sugere otimizações baseadas em dados e feedback loop.
"""

from datetime import date, timedelta
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Client, Campaign, ModuleCode, User
from modules.base import BaseModule
from memory.feedback_loop import FeedbackLoop
from integrations.meta_ads import meta_ads
from integrations.google_ads import google_ads


ANALYSIS_SYSTEM = """Você é o Villa, módulo de análise de campanhas da WebXP Agency.

Você analisa dados de Meta Ads e Google Ads para clientes odontológicos e gera recomendações acionáveis.

## Regras
1. Comece pelo dado mais crítico (CPL disparando, budget estourando, campanha sem leads)
2. Compare sempre com o threshold do cliente e com o período anterior
3. Identifique a CAUSA, não apenas o sintoma — CPL alto pode ser criativo cansado, público saturado ou landing page quebrada
4. Recomendações devem ser ações concretas: "pausar adset X", "testar gancho com número", "expandir público lookalike 3%"
5. Máximo 5 recomendações, priorizadas por impacto
6. Use a Lei de Pareto: qual campanha/adset/criativo gera 80% do resultado?
7. Se frequência > 3.0, sempre recomendar renovação de criativos
"""

ANALYSIS_PROMPT = """Analise a performance das campanhas deste cliente:

CLIENTE: {client_name} ({specialty})
PERÍODO: {period}
THRESHOLDS: CPL máx R${cpl_max}, CTR mín {ctr_min}%, Frequência máx {freq_max}

DADOS META ADS:
{meta_data}

DADOS GOOGLE ADS:
{google_data}

HISTÓRICO DE PERFORMANCE (últimas 4 semanas):
{history}

{memory_context}

Gere em JSON:
{{
    "health_score": 0-100,
    "summary": "resumo em 2-3 frases",
    "anomalies": [{{"metric": "...", "value": 0, "expected": 0, "severity": "info|warning|critical"}}],
    "pareto": {{"top_campaign": "...", "percentage_of_results": 0}},
    "recommendations": [{{"action": "...", "priority": 1-5, "expected_impact": "...", "reasoning": "..."}}],
    "trends": {{"cpl": "up|down|stable", "ctr": "up|down|stable", "leads": "up|down|stable"}}
}}
"""


class M04Campanhas(BaseModule):
    """Módulo de análise de campanhas e otimizações."""

    code = ModuleCode.M04_CAMPANHAS
    name = "Análise de Campanhas"
    description = (
        "Analisa performance de Meta Ads e Google Ads, identifica anomalias, "
        "tendências e sugere otimizações baseadas em dados e feedback loop."
    )

    KEYWORDS = [
        "campanha", "campanhas", "campaign",
        "performance", "desempenho",
        "otimizar", "otimização", "otimizacao",
        "meta ads", "facebook", "instagram", "google ads",
        "cpl", "ctr", "roas", "frequência", "frequencia",
        "anúncio", "anuncio", "ads",
        "criativo", "criativos",
        "budget", "orçamento", "orcamento",
    ]

    async def can_handle(self, message: str, context: Optional[dict] = None) -> float:
        msg_lower = message.lower()
        if context and "campanhas" in context.get("event_type", ""):
            return 0.9
        matches = sum(1 for kw in self.KEYWORDS if kw in msg_lower)
        if matches >= 3: return 0.9
        if matches >= 2: return 0.75
        if matches >= 1: return 0.55
        return 0.0

    async def execute(
        self,
        message: str,
        db: AsyncSession,
        user: Optional[User] = None,
        client_slug: Optional[str] = None,
        context: Optional[dict] = None,
    ) -> dict:
        feedback_loop = FeedbackLoop(db)

        client = await self._resolve_client(db, client_slug, message)
        if not client:
            return {"success": False, "message": "Cliente não identificado.", "actions_taken": []}

        # Coletar dados
        period_end = date.today()
        period_start = period_end - timedelta(days=7)
        config = client.config or {}
        thresholds = config.get("thresholds", {})

        meta_data = {}
        if client.meta_ad_account_id:
            try:
                meta_data = await self._collect_meta_insights(client.meta_ad_account_id, period_start, period_end)
            except Exception as e:
                meta_data = {"error": str(e)}

        google_data = {}
        if client.google_ads_id:
            try:
                google_data = await google_ads.get_metrics(client.google_ads_id, days=7)
            except Exception as e:
                google_data = {"error": str(e)}

        # Buscar histórico de 4 semanas
        history_data = {}
        if client.meta_ad_account_id:
            try:
                history_data = await self._collect_meta_insights(
                    client.meta_ad_account_id, period_end - timedelta(days=28), period_end
                )
            except Exception:
                pass

        # Memória
        memory = await feedback_loop.build_context(
            module=self.code, action="analisar_campanha", client_slug=client.slug,
        )

        # Análise via Claude
        response = await self.ask_claude(
            message=ANALYSIS_PROMPT.format(
                client_name=client.name,
                specialty=client.specialty or "odontologia",
                period=f"{period_start} a {period_end}",
                cpl_max=thresholds.get("cpl_max", 80),
                ctr_min=thresholds.get("ctr_min", 1.2),
                freq_max=thresholds.get("frequency_max", 3.0),
                meta_data=str(meta_data)[:2000],
                google_data=str(google_data)[:1000],
                history=str(history_data)[:1500],
                memory_context=memory["prompt_injection"],
            ),
            db=db,
            system_override=ANALYSIS_SYSTEM,
            client_slug=client.slug,
        )

        # Parsear resultado
        parsed = await self.claude.extract_json(message=response["text"], model="fast")
        analysis = parsed.get("data", {})

        # Salvar análise nas campanhas
        result_db = await db.execute(
            select(Campaign).where(Campaign.client_id == client.id).where(Campaign.status == "active")
        )
        for campaign in result_db.scalars().all():
            campaign.villa_analysis = analysis.get("summary")
            campaign.villa_recommendations = analysis.get("recommendations")
            campaign.health_score = analysis.get("health_score")
        await db.flush()

        # Registrar decisão
        await feedback_loop.record_decision(
            module=self.code, action="analisar_campanha",
            input_data={"client": client.slug, "period": f"{period_start} a {period_end}"},
            output_data={"health_score": analysis.get("health_score"), "recommendations_count": len(analysis.get("recommendations", []))},
            reasoning=memory["reasoning_context"],
            client_slug=client.slug,
            tokens_input=response.get("tokens_input", 0),
            tokens_output=response.get("tokens_output", 0),
            model_used=response.get("model"),
            cost_usd=response.get("cost_usd", 0),
        )

        # Formatar resposta
        msg_lines = [f"📊 **Análise — {client.name}** (Score: {analysis.get('health_score', '?')}/100)"]
        msg_lines.append(analysis.get("summary", ""))
        if analysis.get("anomalies"):
            msg_lines.append("\n⚠️ **Anomalias:**")
            for a in analysis["anomalies"][:3]:
                msg_lines.append(f"  • {a.get('metric', '?')}: {a.get('value')} (esperado: {a.get('expected')})")
        if analysis.get("recommendations"):
            msg_lines.append("\n💡 **Recomendações:**")
            for r in analysis["recommendations"][:5]:
                msg_lines.append(f"  {r.get('priority', '?')}. {r.get('action', '')}")

        return {
            "success": True,
            "message": "\n".join(msg_lines),
            "data": {"client": client.slug, "analysis": analysis},
            "actions_taken": ["campaign_analysis_complete"],
        }

    async def _collect_meta_insights(self, account_id: str, start: date, end: date) -> dict:
        insights = await meta_ads.get_campaign_insights(account_id, start, end)
        return {"campaigns": insights, "count": len(insights)}

    async def _resolve_client(self, db, slug, message):
        if slug:
            r = await db.execute(select(Client).where(Client.slug == slug))
            return r.scalar_one_or_none()
        r = await db.execute(select(Client))
        for c in r.scalars().all():
            if c.name.lower() in message.lower() or c.slug.lower() in message.lower():
                return c
        return None
