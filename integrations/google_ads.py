"""
Villa — Integração Google Ads
Acessa métricas via Google Apps Script Web App
(o Caio já tem 6 contas configuradas dessa forma).
"""

from datetime import date

import httpx

from core.config import settings


class GoogleAdsClient:
    """
    Cliente para Google Ads via Apps Script Web App.
    A WebXP já usa esse modelo — o Apps Script expõe um endpoint
    que retorna dados do Google Ads em JSON.
    
    Uso:
        gads = GoogleAdsClient()
        data = await gads.get_metrics(customer_id="123-456-7890", days=7)
    """

    def __init__(self):
        self.script_url = settings.google_ads_script_url
        self._client = httpx.AsyncClient(timeout=60.0)

    async def get_metrics(
        self,
        customer_id: str | None = None,
        date_start: date | None = None,
        date_end: date | None = None,
        days: int = 7,
    ) -> dict:
        """
        Busca métricas do Google Ads via Apps Script.
        
        Args:
            customer_id: ID da conta Google Ads (opcional se o script gerencia)
            date_start: Data início (padrão: últimos N dias)
            date_end: Data fim (padrão: hoje)
            days: Últimos N dias (usado se date_start não definido)
        """
        params = {"days": days}
        if customer_id:
            params["customer_id"] = customer_id
        if date_start:
            params["date_start"] = date_start.isoformat()
        if date_end:
            params["date_end"] = date_end.isoformat()

        response = await self._client.get(self.script_url, params=params)
        response.raise_for_status()
        return response.json()

    async def get_campaign_data(
        self,
        customer_id: str | None = None,
        days: int = 30,
    ) -> list[dict]:
        """Busca dados de campanhas (nome, status, spend, clicks, impressions)."""
        params = {"action": "campaigns", "days": days}
        if customer_id:
            params["customer_id"] = customer_id

        response = await self._client.get(self.script_url, params=params)
        response.raise_for_status()
        data = response.json()
        return data.get("campaigns", [])

    async def close(self):
        await self._client.aclose()


google_ads = GoogleAdsClient()
