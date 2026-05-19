"""
Villa — Integração N8N
Dispara workflows existentes e novos via webhook.
O N8N serve como middleware de automação — o Villa
pode acionar workflows que já estão rodando na WebXP.
"""

from typing import Optional

import httpx

from core.config import settings


class N8NClient:
    """
    Cliente para disparar workflows N8N via webhook.
    
    Workflows existentes da WebXP:
        1. InLead → CRM → WhatsApp (captação)
        2. Qualificação via Claude API (piloto)
        3. Meta CAPI a partir do Kommo
        4. Relatórios e BI (planilha mestre)
    
    Uso:
        n8n = N8NClient()
        await n8n.trigger("capi_conversion", {"lead_id": 123, "value": 6000})
    """

    def __init__(self):
        self.base_url = settings.n8n_base_url.rstrip("/")
        self.api_key = settings.n8n_api_key
        self._client = httpx.AsyncClient(timeout=30.0)

    async def trigger(
        self,
        workflow_name: str,
        data: dict,
        webhook_path: Optional[str] = None,
    ) -> dict:
        """
        Dispara um workflow N8N via webhook.
        
        Args:
            workflow_name: Nome identificador do workflow
            data: Dados a enviar como payload
            webhook_path: Path customizado do webhook (se diferente do padrão)
        """
        url = webhook_path or f"{self.base_url}/webhook/{workflow_name}"
        headers = {}
        if self.api_key:
            headers["X-N8N-API-KEY"] = self.api_key

        response = await self._client.post(url, json=data, headers=headers)
        response.raise_for_status()
        return response.json() if response.content else {"status": "triggered"}

    async def trigger_capi_event(
        self,
        lead_id: int,
        event_name: str,
        value: Optional[float] = None,
    ) -> dict:
        """Atalho para disparar o workflow de Meta CAPI."""
        return await self.trigger("capi_conversion", {
            "lead_id": lead_id,
            "event_name": event_name,
            "value": value,
        })

    async def trigger_report_update(
        self,
        client_slug: str,
        report_type: str = "weekly",
    ) -> dict:
        """Atalho para disparar atualização da planilha mestre de BI."""
        return await self.trigger("report_update", {
            "client_slug": client_slug,
            "report_type": report_type,
        })

    async def get_workflows(self) -> list[dict]:
        """Lista workflows ativos no N8N (requer API key)."""
        headers = {}
        if self.api_key:
            headers["X-N8N-API-KEY"] = self.api_key

        response = await self._client.get(
            f"{self.base_url}/api/v1/workflows",
            headers=headers,
        )
        response.raise_for_status()
        return response.json().get("data", [])

    async def close(self):
        await self._client.aclose()


n8n = N8NClient()
