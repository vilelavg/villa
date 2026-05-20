"""
Villa — Módulo M03: Qualificação Automática de Leads
Prioridade 3 do MVP. Evolução do piloto que já roda no N8N do Caio.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️  STAND_BY — Decisão reunião Caio+Thaís (19/05/2026)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Motivo: WhatsApp detecta e bana bots. A Thaís tem ~45-50
contas de anúncio numa única BM — qualquer instabilidade
derruba tudo. Ela passou 1 ano montando o tracking atual.

O módulo aprenderá com as conversas da Mari (via M14)
e será reativado quando houver segurança, direcionado
para CLÍNICAS atendendo pacientes — não a WebXP.

Para reativar: UPDATE module_configs SET is_active = true
               WHERE module = 'm03_qualificacao';
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Fluxo original (quando ativo):
    1. Lead manda mensagem no WhatsApp (trigger do webhook)
    2. Villa identifica o lead e o cliente correspondente
    3. Consulta memória (scripts aprovados, objeções comuns)
    4. Gera resposta natural via Claude (1-2 frases, sem ponto final)
    5. Avalia score do lead a cada 3 mensagens
    6. Se qualificado → [TRANSFERIR_HUMANO] + move card no Kommo
    7. Se desqualificado → encerra + registra motivo
    8. Registra tudo no feedback loop
"""

from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import (
    Client, Lead, LeadStatus, Conversation, ModuleCode, User,
)
from modules.base import BaseModule
from modules.m03_qualificacao.prompts import (
    SYSTEM_PROMPT,
    QUALIFICATION_PROMPT,
    FIRST_CONTACT_PROMPT,
    FOLLOW_UP_PROMPT,
)
from modules.m03_qualificacao.scoring import LeadScorer
from memory.feedback_loop import FeedbackLoop
from integrations.whatsapp import whatsapp
from integrations.kommo import kommo


class M03Qualificacao(BaseModule):
    """Módulo de qualificação de leads via WhatsApp."""

    code = ModuleCode.M03_QUALIFICACAO
    name = "Qualificação de Leads"
    description = (
        "Qualifica leads via WhatsApp com conversa natural, "
        "lead scoring automático e handoff inteligente para humano."
    )

    # ── STAND_BY (ver docstring do módulo) ──
    STAND_BY = True

    KEYWORDS = [
        "qualifica", "qualificar", "qualificação", "qualificacao",
        "lead", "leads",
        "atender", "atendimento",
        "whatsapp", "mensagem",
        "sdr", "comercial",
        "score", "scoring",
    ]

    # Scoring a cada N mensagens
    SCORE_EVERY_N_MESSAGES = 3
    MAX_MESSAGES_BEFORE_DECISION = 10

    def __init__(self):
        super().__init__()
        self.scorer = LeadScorer()

    async def can_handle(self, message: str, context: Optional[dict] = None) -> float:
        """Retorna confiança de 0-1. Zero enquanto em STAND_BY."""
        # Eventos de webhook têm prioridade máxima
        if context:
            event = context.get("event_type", "")
            if "whatsapp_message" in event or "inlead_new_lead" in event:
                return 0.95
            if "kommo_lead" in event:
                return 0.8

        msg_lower = message.lower()
        matches = sum(1 for kw in self.KEYWORDS if kw in msg_lower)

        if matches >= 2:
            return 0.85
        if matches >= 1:
            return 0.65
        return 0.0

    async def execute(
        self,
        message: str,
        db: AsyncSession,
        user: Optional[User] = None,
        client_slug: Optional[str] = None,
        context: Optional[dict] = None,
    ) -> dict:
        """
        Ponto de entrada do módulo. Roteia para a ação correta:
            - Evento WhatsApp → processar mensagem do lead
            - Evento InLead → primeiro contato com lead novo
            - Comando manual → ações administrativas
        """
        if self.STAND_BY:
            return {
                "success": False,
                "message": (
                    "⏸️ M03 Qualificação está em STAND_BY por decisão da reunião com Caio e Thaís (19/05/2026). "
                    "WhatsApp outbound paused para proteger as contas de anúncio da BM. "
                    "O módulo aprenderá com as conversas da Mari via M14 e será reativado quando seguro."
                ),
                "actions_taken": ["stand_by_blocked"],
            }
        context = context or {}
        event_type = context.get("event_type", "")
        payload = context.get("payload", {})

        if "whatsapp_message" in event_type:
            return await self._handle_whatsapp_message(db, payload)

        if "inlead_new_lead" in event_type:
            return await self._handle_new_lead(db, payload)

        if "kommo_lead" in event_type:
            return await self._handle_kommo_event(db, payload)

        # Comando manual
        return {
            "success": True,
            "message": "Módulo de qualificação ativo. Aguardando leads via WhatsApp ou InLead.",
            "actions_taken": ["status_check"],
        }

    # ═══════════════════════════════════════════════════
    # PROCESSAR MENSAGEM DO WHATSAPP
    # ═══════════════════════════════════════════════════

    async def _handle_whatsapp_message(self, db: AsyncSession, payload: dict) -> dict:
        """Processa mensagem recebida de um lead via WhatsApp."""
        feedback_loop = FeedbackLoop(db)

        from_number = payload.get("from", "")
        message_data = payload.get("message", {})
        msg_text = message_data.get("text", {}).get("body", "") if message_data.get("type") == "text" else ""
        msg_id = message_data.get("id", "")

        if not from_number or not msg_text:
            return {"success": False, "error": "missing_data", "actions_taken": []}

        # Marcar como lida
        if msg_id:
            try:
                await whatsapp.mark_as_read(msg_id)
            except Exception:
                pass

        # Buscar lead pelo telefone
        result = await db.execute(
            select(Lead)
            .where(Lead.phone == from_number)
            .order_by(Lead.created_at.desc())
        )
        lead = result.scalar_one_or_none()

        if not lead:
            return {"success": False, "error": "lead_not_found", "from": from_number[-4:], "actions_taken": []}

        # Buscar cliente
        client_result = await db.execute(
            select(Client).where(Client.id == lead.client_id)
        )
        client = client_result.scalar_one_or_none()
        if not client:
            return {"success": False, "error": "client_not_found", "actions_taken": []}

        # Buscar ou criar conversa ativa
        conv = await self._get_or_create_conversation(db, lead)

        # Adicionar mensagem do lead ao histórico
        conv.messages = conv.messages or []
        conv.messages.append({
            "role": "lead",
            "content": msg_text,
            "timestamp": datetime.utcnow().isoformat(),
        })

        message_count = len([m for m in conv.messages if m.get("role") == "lead"])

        # Consultar memória
        memory = await feedback_loop.build_context(
            module=self.code,
            action="qualificar_lead",
            client_slug=client.slug,
        )

        # Gerar resposta
        client_config = client.config or {}
        response = await self.ask_claude(
            message=QUALIFICATION_PROMPT.format(
                client_name=client.name,
                specialty=client.specialty or "odontologia",
                offer=client_config.get("offer", "serviços odontológicos"),
                icp=client_config.get("icp", "pacientes interessados"),
                tone=client_config.get("tom_voz", "acolhedor e objetivo"),
                lead_name=lead.name or "amigo(a)",
                lead_source=lead.source or "site",
                form_data=str(lead.raw_data or {}),
                conversation_history=self._format_messages(conv.messages),
                current_message=msg_text,
                current_score=lead.qualification_score or 0,
                message_count=message_count,
                memory_context=memory["prompt_injection"],
            ),
            db=db,
            system_override=SYSTEM_PROMPT,
            client_slug=client.slug,
        )

        reply_text = response["text"].strip()
        actions = ["message_processed"]

        # Verificar tags de controle
        should_transfer = "[TRANSFERIR_HUMANO]" in reply_text
        should_disqualify = "[DESQUALIFICADO]" in reply_text

        # Limpar tags do texto
        clean_reply = reply_text.replace("[TRANSFERIR_HUMANO]", "").replace("[DESQUALIFICADO]", "").strip()

        # Enviar resposta via WhatsApp
        if clean_reply:
            try:
                await whatsapp.send_text(from_number, clean_reply)
                actions.append("reply_sent")
            except Exception as e:
                actions.append(f"reply_failed: {str(e)}")

        # Adicionar resposta ao histórico
        conv.messages.append({
            "role": "villa",
            "content": clean_reply,
            "timestamp": datetime.utcnow().isoformat(),
        })

        # Scoring periódico
        if message_count % self.SCORE_EVERY_N_MESSAGES == 0 or should_transfer or should_disqualify:
            scoring_result = await self.scorer.score_lead(client, lead, conv.messages)
            lead.qualification_score = scoring_result.get("total_score", 0)
            lead.qualification_notes = scoring_result.get("reasoning", "")
            actions.append(f"scored:{lead.qualification_score}")

            # Override de qualificação se score alto
            if scoring_result.get("qualification") == "qualified":
                should_transfer = True

        # Processar transferência
        if should_transfer:
            lead.status = LeadStatus.QUALIFIED
            lead.qualified_by = "villa"
            conv.transferred_to_human = True
            conv.transfer_reason = "qualificado_pelo_villa"

            # Mover card no Kommo
            if lead.kommo_lead_id and client.config:
                qualified_status_id = client_config.get("kommo_status_map", {}).get("qualified")
                if qualified_status_id:
                    try:
                        await kommo.move_lead(lead.kommo_lead_id, int(qualified_status_id))
                        await kommo.add_note(lead.kommo_lead_id, f"Lead qualificado pelo Villa (score: {lead.qualification_score})")
                        actions.append("kommo_moved")
                    except Exception:
                        pass

            actions.append("transferred_to_human")

        # Processar desqualificação
        elif should_disqualify:
            lead.status = LeadStatus.DISQUALIFIED
            lead.disqualification_reason = lead.qualification_notes
            conv.is_active = False
            conv.ended_at = datetime.utcnow()
            actions.append("disqualified")

        # Limite de mensagens atingido sem decisão
        elif message_count >= self.MAX_MESSAGES_BEFORE_DECISION:
            # Forçar scoring final
            scoring_result = await self.scorer.score_lead(client, lead, conv.messages)
            lead.qualification_score = scoring_result.get("total_score", 0)

            if scoring_result.get("qualification") == "qualified":
                lead.status = LeadStatus.QUALIFIED
                conv.transferred_to_human = True
                actions.append("auto_qualified_max_messages")
            else:
                lead.status = LeadStatus.CONTACTED
                actions.append("max_messages_reached")

        # Atualizar status intermediário
        elif lead.status == LeadStatus.NEW:
            lead.status = LeadStatus.QUALIFYING

        await db.flush()

        # Registrar decisão
        await feedback_loop.record_decision(
            module=self.code,
            action="qualificar_lead",
            input_data={
                "lead_message": msg_text[:200],
                "message_count": message_count,
            },
            output_data={
                "reply": clean_reply[:200],
                "score": lead.qualification_score,
                "status": lead.status,
                "transferred": should_transfer,
            },
            reasoning=memory["reasoning_context"],
            client_slug=client.slug,
            tokens_input=response.get("tokens_input", 0),
            tokens_output=response.get("tokens_output", 0),
            model_used=response.get("model"),
            cost_usd=response.get("cost_usd", 0),
        )

        return {
            "success": True,
            "message": f"Lead {lead.name or from_number[-4:]}: {clean_reply[:100]}",
            "data": {
                "lead_id": lead.id,
                "score": lead.qualification_score,
                "status": lead.status,
                "messages_count": message_count,
                "transferred": should_transfer,
                "disqualified": should_disqualify,
            },
            "actions_taken": actions,
        }

    # ═══════════════════════════════════════════════════
    # PRIMEIRO CONTATO (lead novo do InLead)
    # ═══════════════════════════════════════════════════

    async def _handle_new_lead(self, db: AsyncSession, payload: dict) -> dict:
        """Envia primeira mensagem para lead novo captado pelo InLead."""
        from scheduler.triggers import TriggerService

        triggers = TriggerService(db)
        lead_result = await triggers.on_new_lead(payload)

        if not lead_result.get("success"):
            return lead_result

        lead_id = lead_result.get("lead_id")
        client_slug = lead_result.get("client_slug")

        # Buscar lead e cliente
        lead_q = await db.execute(select(Lead).where(Lead.id == lead_id))
        lead = lead_q.scalar_one_or_none()

        client_q = await db.execute(select(Client).where(Client.slug == client_slug))
        client = client_q.scalar_one_or_none()

        if not lead or not client or not lead.phone:
            return {
                "success": False,
                "error": "lead_or_client_not_found",
                "actions_taken": lead_result.get("actions_taken", []),
            }

        # Gerar primeira mensagem
        client_config = client.config or {}
        form_text = str(lead.raw_data or {})

        response = await self.claude.ask(
            message=FIRST_CONTACT_PROMPT.format(
                client_name=client.name,
                specialty=client.specialty or "odontologia",
                offer=client_config.get("offer", "serviços odontológicos"),
                tone=client_config.get("tom_voz", "acolhedor"),
                lead_name=lead.name or "",
                lead_source=lead.source or "anúncio",
                form_data=form_text[:300],
            ),
            system=SYSTEM_PROMPT,
            model="primary",
        )

        first_message = response["text"].strip()

        # Enviar via WhatsApp
        try:
            await whatsapp.send_text(lead.phone, first_message)
        except Exception as e:
            return {
                "success": False,
                "error": f"whatsapp_send_failed: {str(e)}",
                "actions_taken": ["lead_created", "first_message_failed"],
            }

        # Criar conversa
        conv = Conversation(
            id=str(uuid4()),
            lead_id=lead.id,
            module=self.code,
            messages=[{
                "role": "villa",
                "content": first_message,
                "timestamp": datetime.utcnow().isoformat(),
            }],
            is_active=True,
        )
        db.add(conv)

        lead.status = LeadStatus.CONTACTED
        await db.flush()

        return {
            "success": True,
            "message": f"Primeiro contato enviado para {lead.name or 'lead'}: {first_message[:100]}",
            "data": {
                "lead_id": lead.id,
                "conversation_id": conv.id,
                "first_message": first_message,
            },
            "actions_taken": ["lead_created", "first_contact_sent"],
        }

    # ═══════════════════════════════════════════════════
    # EVENTOS DO KOMMO
    # ═══════════════════════════════════════════════════

    async def _handle_kommo_event(self, db: AsyncSession, payload: dict) -> dict:
        """Processa eventos do Kommo (card movido, lead atualizado)."""
        from scheduler.triggers import TriggerService

        triggers = TriggerService(db)
        result = await triggers.on_lead_status_changed(payload)
        return {
            "success": True,
            "message": "Evento Kommo processado",
            "data": result,
            "actions_taken": ["kommo_event_processed"],
        }

    # ═══════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════

    async def _get_or_create_conversation(
        self, db: AsyncSession, lead: Lead
    ) -> Conversation:
        """Busca conversa ativa ou cria nova."""
        result = await db.execute(
            select(Conversation)
            .where(Conversation.lead_id == lead.id)
            .where(Conversation.module == self.code)
            .where(Conversation.is_active == True)
            .order_by(Conversation.started_at.desc())
        )
        conv = result.scalar_one_or_none()

        if not conv:
            conv = Conversation(
                id=str(uuid4()),
                lead_id=lead.id,
                module=self.code,
                messages=[],
                is_active=True,
            )
            db.add(conv)
            await db.flush()

        return conv

    def _format_messages(self, messages: list[dict]) -> str:
        """Formata histórico de mensagens para o prompt."""
        if not messages:
            return "(primeira mensagem)"

        lines = []
        for msg in messages[-15:]:  # Últimas 15 mensagens (contexto)
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if role == "lead":
                lines.append(f"LEAD: {content}")
            elif role == "villa":
                lines.append(f"VOCÊ: {content}")
            elif role == "human":
                lines.append(f"ATENDENTE: {content}")
        return "\n".join(lines)
