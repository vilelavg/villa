"""
Villa — M02 Relatórios: Coletores de Dados
Puxa métricas de todas as fontes e consolida em formato padronizado.

Fontes:
    - Meta Ads API (campanhas, CPL, CTR, spend, leads)
    - Google Ads via Apps Script (campanhas, clicks, spend)
    - Kommo CRM (leads por etapa, conversões, valores)
    - InLead (leads captados, fluxos completos)
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.models import Appointment, Client, Lead, LeadStatus
from integrations.google_ads import google_ads
from integrations.kommo import kommo
from integrations.meta_ads import meta_ads


class DataCollector:
    """
    Coleta e consolida dados de múltiplas fontes para um cliente.

    Uso:
        collector = DataCollector(db)
        data = await collector.collect_all(client, period_start, period_end)
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def collect_all(
        self,
        client: Client,
        period_start: date,
        period_end: date,
    ) -> dict:
        """
        Coleta dados de todas as fontes disponíveis para um cliente.
        Retorna dicionário consolidado com todas as métricas.
        """
        data = {
            "client": client.slug,
            "client_name": client.name,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "meta_ads": None,
            "google_ads": None,
            "crm": None,
            "leads_summary": None,
            "appointments": None,
        }

        # ── Meta Ads ──
        if client.meta_ad_account_id:
            try:
                data["meta_ads"] = await self._collect_meta(
                    client.meta_ad_account_id, period_start, period_end
                )
            except Exception as e:
                data["meta_ads"] = {"error": str(e)}

        # ── Google Ads ──
        if client.google_ads_id:
            try:
                data["google_ads"] = await self._collect_google(
                    client.google_ads_id, period_start, period_end
                )
            except Exception as e:
                data["google_ads"] = {"error": str(e)}

        # ── CRM (Kommo) ──
        if client.kommo_pipeline_id:
            try:
                data["crm"] = await self._collect_kommo(client.kommo_pipeline_id)
            except Exception as e:
                data["crm"] = {"error": str(e)}

        # ── Leads do banco Villa ──
        data["leads_summary"] = await self._collect_leads(client.id, period_start, period_end)

        # ── Agendamentos ──
        data["appointments"] = await self._collect_appointments(client.id, period_start, period_end)

        # ── Calcular métricas consolidadas ──
        data["consolidated"] = self._consolidate(data)

        return data

    async def _collect_meta(self, ad_account_id: str, start: date, end: date) -> dict:
        """Coleta métricas do Meta Ads."""
        # Meta Ads pausado por decisão da reunião Caio+Thaís (19/05/2026)
        # Token não configurado ou integração pausada → retorna vazio silenciosamente
        if not getattr(settings, "meta_access_token", "") or settings.meta_access_token in (
            "",
            "TROCAR_AQUI",
        ):
            return {"status": "paused", "reason": "Meta Ads pausado — decisão reunião 19/05/2026"}

        insights = await meta_ads.get_campaign_insights(ad_account_id, start, end)

        total_spend = 0
        total_impressions = 0
        total_clicks = 0
        total_leads = 0
        campaigns_data = []

        for ins in insights:
            spend = float(ins.get("spend", 0))
            impressions = int(ins.get("impressions", 0))
            clicks = int(ins.get("clicks", 0))
            leads = meta_ads.extract_leads_from_actions(ins.get("actions", []))
            cpl = meta_ads.extract_cpl(ins.get("cost_per_action_type", []))

            total_spend += spend
            total_impressions += impressions
            total_clicks += clicks
            total_leads += leads

            campaigns_data.append(
                {
                    "name": ins.get("campaign_name", ""),
                    "id": ins.get("campaign_id", ""),
                    "spend": spend,
                    "impressions": impressions,
                    "clicks": clicks,
                    "ctr": float(ins.get("ctr", 0)),
                    "leads": leads,
                    "cpl": cpl,
                }
            )

        return {
            "total_spend": round(total_spend, 2),
            "total_impressions": total_impressions,
            "total_clicks": total_clicks,
            "total_leads": total_leads,
            "avg_ctr": round(total_clicks / total_impressions * 100, 2) if total_impressions else 0,
            "avg_cpl": round(total_spend / total_leads, 2) if total_leads else 0,
            "campaigns": campaigns_data,
        }

    async def _collect_google(self, customer_id: str, start: date, end: date) -> dict:
        """Coleta métricas do Google Ads via Apps Script."""
        days = (end - start).days
        data = await google_ads.get_metrics(customer_id=customer_id, days=days)
        return data

    async def _collect_kommo(self, pipeline_id: int) -> dict:
        """Coleta dados do Kommo CRM (leads por etapa)."""
        # Verificar se Kommo está configurado antes de chamar
        if not getattr(settings, "kommo_api_token", "") or settings.kommo_api_token in (
            "",
            "TROCAR_AQUI",
        ):
            return {"status": "not_configured", "reason": "KOMMO_API_TOKEN não configurado"}
        if not getattr(settings, "kommo_account_url", "") or settings.kommo_account_url in (
            "",
            "TROCAR_AQUI",
        ):
            return {"status": "not_configured", "reason": "KOMMO_ACCOUNT_URL não configurado"}

        leads = await kommo.get_leads(pipeline_id=pipeline_id, limit=200)

        by_status = {}
        total_value = 0

        for lead in leads:
            status = lead.get("status_id", 0)
            by_status[status] = by_status.get(status, 0) + 1
            total_value += lead.get("price", 0)

        statuses = await kommo.get_pipeline_statuses(pipeline_id)
        status_names = {s["id"]: s["name"] for s in statuses}

        return {
            "total_leads": len(leads),
            "total_value": total_value,
            "by_status": {
                status_names.get(sid, f"status_{sid}"): count for sid, count in by_status.items()
            },
        }

    async def _collect_leads(self, client_id: str, start: date, end: date) -> dict:
        """Coleta resumo de leads do banco Villa."""
        from datetime import datetime

        start_dt = datetime.combine(start, datetime.min.time())
        end_dt = datetime.combine(end, datetime.max.time())

        result = await self.db.execute(
            select(Lead)
            .where(Lead.client_id == client_id)
            .where(Lead.created_at.between(start_dt, end_dt))
        )
        leads = result.scalars().all()

        by_status = {}
        by_source = {}
        total_value = 0
        qualified = 0

        for lead in leads:
            by_status[lead.status] = by_status.get(lead.status, 0) + 1
            if lead.source:
                by_source[lead.source] = by_source.get(lead.source, 0) + 1
            if lead.deal_value:
                total_value += lead.deal_value
            if lead.status in (
                LeadStatus.QUALIFIED,
                LeadStatus.SCHEDULED,
                LeadStatus.PROPOSAL,
                LeadStatus.WON,
            ):
                qualified += 1

        total = len(leads)
        return {
            "total": total,
            "by_status": {str(k): v for k, v in by_status.items()},
            "by_source": by_source,
            "qualified": qualified,
            "qualification_rate": round(qualified / total * 100, 1) if total else 0,
            "total_value": total_value,
            "won": by_status.get(LeadStatus.WON, 0),
            "lost": by_status.get(LeadStatus.LOST, 0),
        }

    async def _collect_appointments(self, client_id: str, start: date, end: date) -> dict:
        """Coleta resumo de agendamentos."""
        from datetime import datetime

        start_dt = datetime.combine(start, datetime.min.time())
        end_dt = datetime.combine(end, datetime.max.time())

        result = await self.db.execute(
            select(Appointment)
            .where(Appointment.client_id == client_id)
            .where(Appointment.scheduled_at.between(start_dt, end_dt))
        )
        appointments = result.scalars().all()

        by_status = {}
        for apt in appointments:
            by_status[apt.status] = by_status.get(apt.status, 0) + 1

        total = len(appointments)
        completed = by_status.get("completed", 0)
        no_show = by_status.get("no_show", 0)

        return {
            "total": total,
            "by_status": by_status,
            "show_rate": round(completed / total * 100, 1) if total else 0,
            "no_show_rate": round(no_show / total * 100, 1) if total else 0,
        }

    def _consolidate(self, data: dict) -> dict:
        """Calcula métricas consolidadas a partir de todas as fontes."""
        meta = data.get("meta_ads") or {}
        google = data.get("google_ads") or {}
        leads = data.get("leads_summary") or {}
        apts = data.get("appointments") or {}

        total_spend = (meta.get("total_spend", 0) or 0) + (google.get("total_spend", 0) or 0)
        total_leads = leads.get("total", 0)
        total_won = leads.get("won", 0)
        total_value = leads.get("total_value", 0)

        return {
            "total_investment": round(total_spend, 2),
            "total_leads": total_leads,
            "total_qualified": leads.get("qualified", 0),
            "total_won": total_won,
            "total_revenue": total_value,
            "roi": round((total_value - total_spend) / total_spend * 100, 1) if total_spend else 0,
            "cpl_consolidated": round(total_spend / total_leads, 2) if total_leads else 0,
            "conversion_rate": round(total_won / total_leads * 100, 1) if total_leads else 0,
            "show_rate": apts.get("show_rate", 0),
            "qualification_rate": leads.get("qualification_rate", 0),
        }
