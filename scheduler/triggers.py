"""
Villa — Triggers (Gatilhos Event-Driven)
Reações automáticas a eventos em tempo real.

Diferente das rotinas agendadas (daily/weekly), os triggers
disparam IMEDIATAMENTE quando um evento acontece:
    - Lead novo chega no InLead → qualificar
    - Card muda de etapa no Kommo → ação correspondente
    - Mensagem chega no WhatsApp → atender
    - Campanha atinge threshold → alertar

Os triggers são registrados no orquestrador e executados
pelo webhook receiver. Este arquivo define a LÓGICA de
cada trigger — o que acontece quando o evento chega.
"""

from typing import Optional
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Client, Lead, LeadStatus, ModuleCode
from security.audit_log import AuditService


class TriggerService:
    """
    Processa eventos em tempo real e executa ações automáticas.
    
    Uso (chamado pelo webhook receiver via orquestrador):
        triggers = TriggerService(db)
        await triggers.on_new_lead(payload)
        await triggers.on_lead_status_changed(payload)
        await triggers.on_whatsapp_message(payload)
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditService(db)

    # ═══════════════════════════════════════════════════
    # INLEAD — Novo lead
    # ═══════════════════════════════════════════════════

    async def on_new_lead(self, payload: dict) -> dict:
        """
        Trigger: novo lead captado pelo InLead.
        
        Ações automáticas:
            1. Identificar o cliente pelo form_id
            2. Parsear campos aleatórios do InLead
            3. Criar lead no banco do Villa
            4. Criar/atualizar no Kommo
            5. Disparar qualificação (M3)
        """
        from integrations.inlead import InLeadParser

        parser = InLeadParser(self.db)

        # Identificar cliente pelo form_id
        form_id = payload.get("form_id") or payload.get("formId", "")
        client = await parser.identify_client_by_form(form_id)

        if not client:
            await self.audit.log(
                action="trigger_new_lead_unknown_client",
                module=ModuleCode.M03_QUALIFICACAO,
                details={"form_id": form_id, "payload_keys": list(payload.keys())},
                success=False,
                error_message=f"Cliente não encontrado para form_id: {form_id}",
            )
            return {"success": False, "error": "client_not_found", "form_id": form_id}

        # Parsear dados
        parsed = await parser.parse(payload, client_id=client.id)

        # Criar lead no banco
        from uuid import uuid4
        lead = Lead(
            id=str(uuid4()),
            client_id=client.id,
            status=LeadStatus.NEW,
            name=parsed.get("name"),
            phone=parsed.get("phone"),
            email=parsed.get("email"),
            source="inlead",
            inlead_submission_id=parsed.get("submission_id"),
            raw_data=payload,
        )
        self.db.add(lead)
        await self.db.flush()

        await self.audit.log(
            action="trigger_new_lead_created",
            module=ModuleCode.M03_QUALIFICACAO,
            resource_type="lead",
            resource_id=lead.id,
            details={
                "client": client.slug,
                "has_name": bool(parsed.get("name")),
                "has_phone": bool(parsed.get("phone")),
                "has_email": bool(parsed.get("email")),
            },
        )

        return {
            "success": True,
            "lead_id": lead.id,
            "client_slug": client.slug,
            "parsed_fields": list(parsed.keys()),
            "actions_taken": ["lead_created"],
        }

    # ═══════════════════════════════════════════════════
    # KOMMO — Card mudou de etapa
    # ═══════════════════════════════════════════════════

    async def on_lead_status_changed(self, payload: dict) -> dict:
        """
        Trigger: lead mudou de etapa no Kommo.
        
        Ações automáticas por etapa:
            → "Consulta Agendada": criar evento no Calendar, enviar confirmação WhatsApp
            → "Ganho": registrar valor, enviar CAPI, atualizar BI
            → "Perdido": registrar motivo, alimentar retroalimentação
        """
        leads_data = payload.get("leads", {}).get("status", [])
        if not leads_data:
            return {"success": False, "error": "no_lead_data"}

        lead_info = leads_data[0]
        kommo_lead_id = lead_info.get("id")
        new_status_id = lead_info.get("status_id")
        pipeline_id = lead_info.get("pipeline_id")

        # Buscar lead no banco
        result = await self.db.execute(
            select(Lead).where(Lead.kommo_lead_id == kommo_lead_id)
        )
        lead = result.scalar_one_or_none()

        actions = []

        await self.audit.log(
            action="trigger_lead_status_changed",
            module=ModuleCode.M03_QUALIFICACAO,
            resource_type="lead",
            resource_id=lead.id if lead else str(kommo_lead_id),
            details={
                "kommo_lead_id": kommo_lead_id,
                "new_status_id": new_status_id,
                "pipeline_id": pipeline_id,
                "lead_found_in_db": lead is not None,
            },
        )

        # TODO: Mapear status_id para ações específicas por pipeline do cliente
        # Cada cliente tem pipeline diferente no Kommo — o mapeamento
        # de status_id → ação fica em clients.config
        #
        # Exemplo de config esperada:
        # {
        #   "kommo_status_map": {
        #     "142": {"action": "scheduled", "villa_status": "scheduled"},
        #     "143": {"action": "won", "villa_status": "won"},
        #     "144": {"action": "lost", "villa_status": "lost"}
        #   }
        # }

        return {
            "success": True,
            "kommo_lead_id": kommo_lead_id,
            "new_status_id": new_status_id,
            "actions_taken": actions,
        }

    # ═══════════════════════════════════════════════════
    # WHATSAPP — Mensagem recebida
    # ═══════════════════════════════════════════════════

    async def on_whatsapp_message(self, payload: dict) -> dict:
        """
        Trigger: mensagem recebida via WhatsApp.
        
        Ações automáticas:
            1. Identificar o lead pelo número
            2. Determinar contexto (qualificação? atendimento? suporte?)
            3. Rotear para M3 (qualificação) ou M6 (atendimento)
        """
        from_number = payload.get("from", "")
        message = payload.get("message", {})
        msg_type = message.get("type", "text")

        # Buscar lead pelo telefone
        result = await self.db.execute(
            select(Lead).where(Lead.phone == from_number).order_by(Lead.created_at.desc())
        )
        lead = result.scalar_one_or_none()

        context = {
            "from_number": from_number,
            "message_type": msg_type,
            "lead_found": lead is not None,
            "lead_status": lead.status if lead else None,
            "lead_id": lead.id if lead else None,
            "client_id": lead.client_id if lead else None,
        }

        # Determinar destino
        if lead and lead.status in (LeadStatus.NEW, LeadStatus.CONTACTED, LeadStatus.QUALIFYING):
            target = "m03_qualificacao"
        else:
            target = "m06_atendimento"

        context["routed_to"] = target

        await self.audit.log(
            action=f"trigger_whatsapp_routed_{target}",
            module=ModuleCode.M06_ATENDIMENTO,
            details={"from_last4": from_number[-4:] if from_number else "", "target": target},
        )

        return {
            "success": True,
            "routed_to": target,
            "context": context,
        }

    # ═══════════════════════════════════════════════════
    # N8N — Evento de workflow
    # ═══════════════════════════════════════════════════

    async def on_n8n_event(self, event_type: str, payload: dict) -> dict:
        """
        Trigger: evento de workflow N8N.
        
        Eventos comuns:
            - capi_sent: CAPI event enviado com sucesso
            - report_data_ready: dados do relatório coletados
            - automation_error: erro em automação
        """
        await self.audit.log(
            action=f"trigger_n8n_{event_type}",
            details={"workflow": payload.get("workflow"), "data_keys": list(payload.get("data", {}).keys())},
        )

        return {
            "success": True,
            "event_type": event_type,
            "processed": True,
        }
