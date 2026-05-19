"""
Villa — Integração WhatsApp Business API
Envio de mensagens, templates, mídia e reações.
Usa a Cloud API oficial da Meta.
"""

from typing import Optional

import httpx

from core.config import settings


class WhatsAppClient:
    """
    Cliente async para WhatsApp Business Cloud API.
    
    Uso:
        wa = WhatsAppClient()
        await wa.send_text("5511999998888", "Olá! Seu relatório está pronto.")
        await wa.send_template("5511999998888", "relatorio_semanal", {"1": "Ottoboni"})
    """

    BASE_URL = "https://graph.facebook.com/v21.0"

    def __init__(self):
        self.phone_id = settings.whatsapp_phone_id
        self.token = settings.whatsapp_token
        self._client = httpx.AsyncClient(
            base_url=f"{self.BASE_URL}/{self.phone_id}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    # ═══════════════════════════════════════════════════
    # MENSAGENS DE TEXTO
    # ═══════════════════════════════════════════════════

    async def send_text(
        self,
        to: str,
        text: str,
        preview_url: bool = False,
    ) -> dict:
        """
        Envia mensagem de texto simples.
        
        Args:
            to: Número do destinatário com código do país (ex: "5511999998888")
            text: Texto da mensagem
            preview_url: Se True, gera preview de URLs no texto
        """
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": text, "preview_url": preview_url},
        }
        response = await self._client.post("/messages", json=payload)
        response.raise_for_status()
        return response.json()

    # ═══════════════════════════════════════════════════
    # TEMPLATES (mensagens pré-aprovadas)
    # ═══════════════════════════════════════════════════

    async def send_template(
        self,
        to: str,
        template_name: str,
        parameters: Optional[dict[str, str]] = None,
        language: str = "pt_BR",
    ) -> dict:
        """
        Envia mensagem de template pré-aprovado.
        
        Args:
            to: Número do destinatário
            template_name: Nome do template aprovado no Meta Business
            parameters: Dict de parâmetros {"1": "valor1", "2": "valor2"}
            language: Código do idioma
        """
        components = []
        if parameters:
            body_params = [
                {"type": "text", "text": v}
                for k, v in sorted(parameters.items())
            ]
            components.append({
                "type": "body",
                "parameters": body_params,
            })

        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language},
                "components": components,
            },
        }
        response = await self._client.post("/messages", json=payload)
        response.raise_for_status()
        return response.json()

    # ═══════════════════════════════════════════════════
    # MENSAGENS INTERATIVAS
    # ═══════════════════════════════════════════════════

    async def send_buttons(
        self,
        to: str,
        body: str,
        buttons: list[dict],
        header: Optional[str] = None,
        footer: Optional[str] = None,
    ) -> dict:
        """
        Envia mensagem com botões de resposta rápida.
        
        Args:
            to: Número do destinatário
            body: Texto principal
            buttons: Lista de botões [{"id": "btn_1", "title": "Sim"}]
            header: Texto do header (opcional)
            footer: Texto do footer (opcional)
        """
        action = {
            "buttons": [
                {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                for b in buttons[:3]  # Máximo 3 botões
            ]
        }

        interactive = {
            "type": "button",
            "body": {"text": body},
            "action": action,
        }
        if header:
            interactive["header"] = {"type": "text", "text": header}
        if footer:
            interactive["footer"] = {"text": footer}

        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": interactive,
        }
        response = await self._client.post("/messages", json=payload)
        response.raise_for_status()
        return response.json()

    async def send_list(
        self,
        to: str,
        body: str,
        button_text: str,
        sections: list[dict],
        header: Optional[str] = None,
        footer: Optional[str] = None,
    ) -> dict:
        """
        Envia mensagem com lista de opções.
        
        Args:
            sections: [{"title": "Horários", "rows": [{"id": "h1", "title": "09:00"}]}]
        """
        interactive = {
            "type": "list",
            "body": {"text": body},
            "action": {
                "button": button_text,
                "sections": sections,
            },
        }
        if header:
            interactive["header"] = {"type": "text", "text": header}
        if footer:
            interactive["footer"] = {"text": footer}

        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": interactive,
        }
        response = await self._client.post("/messages", json=payload)
        response.raise_for_status()
        return response.json()

    # ═══════════════════════════════════════════════════
    # MÍDIA
    # ═══════════════════════════════════════════════════

    async def send_document(
        self,
        to: str,
        document_url: str,
        caption: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> dict:
        """Envia documento (PDF, XLSX, etc.) via URL."""
        doc = {"link": document_url}
        if caption:
            doc["caption"] = caption
        if filename:
            doc["filename"] = filename

        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "document",
            "document": doc,
        }
        response = await self._client.post("/messages", json=payload)
        response.raise_for_status()
        return response.json()

    async def send_image(
        self,
        to: str,
        image_url: str,
        caption: Optional[str] = None,
    ) -> dict:
        """Envia imagem via URL."""
        image = {"link": image_url}
        if caption:
            image["caption"] = caption

        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "image",
            "image": image,
        }
        response = await self._client.post("/messages", json=payload)
        response.raise_for_status()
        return response.json()

    # ═══════════════════════════════════════════════════
    # REAÇÕES E STATUS
    # ═══════════════════════════════════════════════════

    async def send_reaction(self, to: str, message_id: str, emoji: str) -> dict:
        """Reage a uma mensagem com emoji."""
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "reaction",
            "reaction": {"message_id": message_id, "emoji": emoji},
        }
        response = await self._client.post("/messages", json=payload)
        response.raise_for_status()
        return response.json()

    async def mark_as_read(self, message_id: str) -> dict:
        """Marca uma mensagem como lida (blue ticks)."""
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        response = await self._client.post("/messages", json=payload)
        response.raise_for_status()
        return response.json()

    async def close(self):
        await self._client.aclose()


# Instância global
whatsapp = WhatsAppClient()
