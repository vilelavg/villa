"""
Villa — Integração Meta Ads API + Conversion API (CAPI)
Leitura de métricas de campanhas e envio de eventos de conversão.
"""

import hashlib
import time
from typing import Optional
from datetime import date, timedelta

import httpx

from core.config import settings


class MetaAdsClient:
    """
    Cliente async para Meta Marketing API + Conversion API.
    
    Uso:
        meta = MetaAdsClient()
        
        # Métricas de campanha
        insights = await meta.get_campaign_insights("act_123", date_start, date_end)
        
        # Enviar evento de conversão via CAPI
        await meta.send_conversion_event(
            event_name="Lead",
            email="lead@email.com",
            phone="5511999998888",
            value=6000.0,
        )
    """

    BASE_URL = "https://graph.facebook.com/v21.0"

    def __init__(self):
        self.token = settings.meta_access_token
        self.pixel_id = settings.meta_pixel_id
        self.capi_token = settings.meta_capi_token or self.token
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=60.0,
        )

    # ═══════════════════════════════════════════════════
    # INSIGHTS (leitura de métricas)
    # ═══════════════════════════════════════════════════

    async def get_account_insights(
        self,
        ad_account_id: str,
        date_start: date,
        date_end: date,
        level: str = "campaign",
        fields: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        Busca insights (métricas) de uma conta de anúncios.
        
        Args:
            ad_account_id: ID da conta (ex: "act_123456789")
            date_start: Data início
            date_end: Data fim
            level: "account" | "campaign" | "adset" | "ad"
            fields: Campos a retornar (default: métricas comuns)
        """
        if not fields:
            fields = [
                "campaign_name", "campaign_id",
                "spend", "impressions", "clicks", "ctr",
                "cpc", "cpm", "reach", "frequency",
                "actions", "cost_per_action_type",
                "conversions", "conversion_values",
            ]

        params = {
            "fields": ",".join(fields),
            "time_range": f'{{"since":"{date_start}","until":"{date_end}"}}',
            "level": level,
            "limit": 500,
        }

        response = await self._client.get(f"/{ad_account_id}/insights", params=params)
        response.raise_for_status()
        data = response.json()
        return data.get("data", [])

    async def get_campaign_insights(
        self,
        ad_account_id: str,
        date_start: date,
        date_end: date,
    ) -> list[dict]:
        """Atalho para insights a nível de campanha."""
        return await self.get_account_insights(
            ad_account_id, date_start, date_end, level="campaign"
        )

    async def get_daily_insights(
        self,
        ad_account_id: str,
        days: int = 7,
    ) -> list[dict]:
        """Insights diários dos últimos N dias."""
        end = date.today()
        start = end - timedelta(days=days)

        params = {
            "fields": "campaign_name,campaign_id,spend,impressions,clicks,ctr,actions",
            "time_range": f'{{"since":"{start}","until":"{end}"}}',
            "time_increment": 1,  # Breakdown diário
            "level": "campaign",
            "limit": 500,
        }

        response = await self._client.get(f"/{ad_account_id}/insights", params=params)
        response.raise_for_status()
        return response.json().get("data", [])

    # ═══════════════════════════════════════════════════
    # CAMPANHAS (leitura)
    # ═══════════════════════════════════════════════════

    async def get_campaigns(
        self,
        ad_account_id: str,
        status_filter: Optional[list[str]] = None,
    ) -> list[dict]:
        """Lista campanhas de uma conta."""
        params = {
            "fields": "id,name,status,objective,daily_budget,lifetime_budget,start_time",
            "limit": 200,
        }
        if status_filter:
            params["effective_status"] = str(status_filter)

        response = await self._client.get(f"/{ad_account_id}/campaigns", params=params)
        response.raise_for_status()
        return response.json().get("data", [])

    # ═══════════════════════════════════════════════════
    # CONVERSION API (CAPI) — Eventos de conversão
    # ═══════════════════════════════════════════════════

    async def send_conversion_event(
        self,
        event_name: str,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        value: Optional[float] = None,
        currency: str = "BRL",
        fbclid: Optional[str] = None,
        source_url: Optional[str] = None,
        custom_data: Optional[dict] = None,
    ) -> dict:
        """
        Envia evento de conversão via Conversion API.
        
        Eventos comuns: "Lead", "Schedule", "Purchase", "Contact"
        
        Os dados pessoais (email, phone) são hasheados com SHA256
        antes do envio, conforme exigido pela Meta.
        """
        user_data = {}
        if email:
            user_data["em"] = [self._hash(email.lower().strip())]
        if phone:
            clean_phone = phone.replace("+", "").replace(" ", "").replace("-", "")
            user_data["ph"] = [self._hash(clean_phone)]
        if fbclid:
            user_data["fbc"] = f"fb.1.{int(time.time())}.{fbclid}"

        event = {
            "event_name": event_name,
            "event_time": int(time.time()),
            "action_source": "website",
            "user_data": user_data,
        }

        if source_url:
            event["event_source_url"] = source_url

        event_custom = {}
        if value is not None:
            event_custom["value"] = value
            event_custom["currency"] = currency
        if custom_data:
            event_custom.update(custom_data)
        if event_custom:
            event["custom_data"] = event_custom

        payload = {
            "data": [event],
            "access_token": self.capi_token,
        }

        response = await self._client.post(
            f"/{self.pixel_id}/events",
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    # ═══════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════

    @staticmethod
    def _hash(value: str) -> str:
        """SHA256 hash para dados pessoais (exigido pela Meta CAPI)."""
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def extract_leads_from_actions(actions: list[dict]) -> int:
        """Extrai contagem de leads da lista de actions do insight."""
        for action in actions or []:
            if action.get("action_type") == "lead":
                return int(action.get("value", 0))
        return 0

    @staticmethod
    def extract_cpl(cost_per_action: list[dict]) -> Optional[float]:
        """Extrai CPL da lista de cost_per_action_type."""
        for cpa in cost_per_action or []:
            if cpa.get("action_type") == "lead":
                return float(cpa.get("value", 0))
        return None

    async def close(self):
        await self._client.aclose()


# Instância global
meta_ads = MetaAdsClient()
