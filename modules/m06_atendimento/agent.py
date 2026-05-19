"""
Villa — Módulo M06: Atendimento WhatsApp
Atendimento completo via WhatsApp para clientes já qualificados.
Substitui o GPT Maker atual. Memória de conversa, nutrição com conteúdo,
FAQ por especialidade, handoff inteligente.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️  STAND_BY — Decisão reunião Caio+Thaís (19/05/2026)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Motivo: Atendimento automatizado por WhatsApp é arriscado.
A Mari (SDR) faz o primeiro contato manual hoje. O Villa
vai APRENDER com as interações dela (via M14) enquanto
este módulo está paused.

Quando reativado: será direcionado para CLÍNICAS atendendo
pacientes — não WebXP atendendo seus próprios clientes.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Client, Lead, Conversation, ModuleCode, User
from modules.base import BaseModule
from memory.feedback_loop import FeedbackLoop
from memory.knowledge_base import KnowledgeBaseService
from integrations.whatsapp import whatsapp


SYSTEM_PROMPT = """Você é o Villa, módulo de atendimento da WebXP Agency.

Diferente do módulo de qualificação (M3), aqui você atende leads JÁ qualificados e clientes ativos. Seu papel é:
- Responder dúvidas sobre procedimentos, cursos e serviços
- Nutrir o lead com conteúdo relevante até a consulta
- Dar suporte pós-venda (alunos de cursos, pacientes)
- Escalar para humano quando necessário

## Regras
1. Mensagens curtas (1-3 frases), tom acolhedor e profissional
2. Sem ponto final no fim
3. Use a base de conhecimento para responder dúvidas técnicas
4. Se não sabe a resposta com certeza, diga que vai verificar com a equipe
5. Nunca dê diagnóstico ou conselho médico — apenas informações sobre os serviços
6. Handoff para humano: reclamação, pedido de reembolso, questão contratual, urgência
7. Envie conteúdos de nutrição quando apropriado (depoimentos, resultados, materiais)
"""

RESPONSE_PROMPT = """Responda a mensagem do cliente/lead:

CLIENTE WEBXP: {client_name} ({specialty})
LEAD: {lead_name} (status: {lead_status})
CONTEXTO: {lead_context}

HISTÓRICO:
{conversation_history}

MENSAGEM ATUAL: "{current_message}"

INFORMAÇÕES DA BASE DE CONHECIMENTO:
{knowledge_context}

{memory_context}

Responda APENAS com o texto da mensagem WhatsApp.
Se precisar transferir para humano, inclua [TRANSFERIR_HUMANO] no final.
"""


class M06Atendimento(BaseModule):
    """Módulo de atendimento completo via WhatsApp."""

    code = ModuleCode.M06_ATENDIMENTO
    name = "Atendimento WhatsApp"
    description = (
        "Atendimento completo via WhatsApp para leads qualificados e clientes. "
        "FAQ, nutrição, suporte pós-venda, com handoff inteligente."
    )

    # ── STAND_BY (ver docstring do módulo) ──
    STAND_BY = True

    KEYWORDS = [
        "suporte", "dúvida", "duvida",
        "cliente", "paciente",
        "pós-venda", "pos-venda",
        "reclamação", "reclamacao",
        "faq",
    ]

    async def can_handle(self, message: str, context: Optional[dict] = None) -> float:
        if self.STAND_BY:
            return 0.0
        if context and context.get("event_type") == "whatsapp_message":
            # Se o lead já está qualificado, M06 tem prioridade sobre M03
            payload = context.get("payload", {})
            if payload.get("lead_status") in ("qualified", "scheduled", "proposal", "won"):
                return 0.95
            return 0.4  # Baixa prioridade pra leads novos (M03 cuida)
        msg_lower = message.lower()
        matches = sum(1 for kw in self.KEYWORDS if kw in msg_lower)
        if matches >= 2: return 0.8
        if matches >= 1: return 0.5
        return 0.0

    async def execute(
        self,
        message: str,
        db: AsyncSession,
        user: Optional[User] = None,
        client_slug: Optional[str] = None,
        context: Optional[dict] = None,
    ) -> dict:
        if self.STAND_BY:
            return {
                "success": False,
                "message": (
                    "⏸️ M06 Atendimento está em STAND_BY (reunião Caio+Thaís, 19/05/2026). "
                    "A Mari faz o atendimento manual. O Villa aprende com ela via M14. "
                    "Será reativado para clínicas — não para a WebXP diretamente."
                ),
                "actions_taken": ["stand_by_blocked"],
            }
        context = context or {}

        if context.get("event_type") == "whatsapp_message":
            return await self._handle_message(db, payload)

        return {
            "success": True,
            "message": "Módulo de atendimento ativo. Aguardando mensagens via WhatsApp.",
            "actions_taken": ["status_check"],
        }

    async def _handle_message(self, db: AsyncSession, payload: dict) -> dict:
        feedback_loop = FeedbackLoop(db)
        kb = KnowledgeBaseService(db)

        from_number = payload.get("from", "")
        msg_data = payload.get("message", {})
        msg_text = msg_data.get("text", {}).get("body", "") if msg_data.get("type") == "text" else ""
        msg_id = msg_data.get("id", "")

        if not from_number or not msg_text:
            return {"success": False, "error": "missing_data", "actions_taken": []}

        if msg_id:
            try:
                await whatsapp.mark_as_read(msg_id)
            except Exception:
                pass

        # Buscar lead
        result = await db.execute(
            select(Lead).where(Lead.phone == from_number).order_by(Lead.created_at.desc())
        )
        lead = result.scalar_one_or_none()
        if not lead:
            return {"success": False, "error": "lead_not_found", "actions_taken": []}

        # Buscar cliente
        client_q = await db.execute(select(Client).where(Client.id == lead.client_id))
        client = client_q.scalar_one_or_none()
        if not client:
            return {"success": False, "error": "client_not_found", "actions_taken": []}

        # Buscar/criar conversa
        conv_q = await db.execute(
            select(Conversation)
            .where(Conversation.lead_id == lead.id)
            .where(Conversation.module == self.code)
            .where(Conversation.is_active == True)
            .order_by(Conversation.started_at.desc())
        )
        conv = conv_q.scalar_one_or_none()
        if not conv:
            conv = Conversation(
                id=str(uuid4()), lead_id=lead.id, module=self.code,
                messages=[], is_active=True,
            )
            db.add(conv)
            await db.flush()

        conv.messages = conv.messages or []
        conv.messages.append({"role": "lead", "content": msg_text, "timestamp": datetime.utcnow().isoformat()})

        # Buscar contexto na base de conhecimento
        kb_results = await kb.search(msg_text, limit=3, client_slug=client.slug)
        knowledge_text = "\n".join(
            f"- {r['title']}: {r['text'][:200]}" for r in kb_results
        ) if kb_results else "(sem informações na base de conhecimento)"

        # Memória
        memory = await feedback_loop.build_context(
            module=self.code, action="atender_lead", client_slug=client.slug,
        )

        # Gerar resposta
        history = "\n".join(
            f"{'LEAD' if m['role'] == 'lead' else 'VOCÊ'}: {m['content']}"
            for m in conv.messages[-12:]
        )

        response = await self.ask_claude(
            message=RESPONSE_PROMPT.format(
                client_name=client.name,
                specialty=client.specialty or "odontologia",
                lead_name=lead.name or "cliente",
                lead_status=lead.status,
                lead_context=f"Score: {lead.qualification_score}, Fonte: {lead.source}",
                conversation_history=history,
                current_message=msg_text,
                knowledge_context=knowledge_text,
                memory_context=memory["prompt_injection"],
            ),
            db=db,
            system_override=SYSTEM_PROMPT,
            client_slug=client.slug,
        )

        reply = response["text"].strip()
        should_transfer = "[TRANSFERIR_HUMANO]" in reply
        clean_reply = reply.replace("[TRANSFERIR_HUMANO]", "").strip()

        actions = ["message_processed"]

        if clean_reply:
            try:
                await whatsapp.send_text(from_number, clean_reply)
                actions.append("reply_sent")
            except Exception:
                actions.append("reply_failed")

        conv.messages.append({"role": "villa", "content": clean_reply, "timestamp": datetime.utcnow().isoformat()})

        if should_transfer:
            conv.transferred_to_human = True
            conv.transfer_reason = "handoff_atendimento"
            actions.append("transferred_to_human")

        await db.flush()

        await feedback_loop.record_decision(
            module=self.code, action="atender_lead",
            input_data={"message": msg_text[:200]},
            output_data={"reply": clean_reply[:200], "transferred": should_transfer, "kb_used": len(kb_results)},
            client_slug=client.slug,
        )

        return {
            "success": True,
            "message": f"Atendimento {lead.name or from_number[-4:]}: {clean_reply[:100]}",
            "data": {"lead_id": lead.id, "transferred": should_transfer, "kb_results_used": len(kb_results)},
            "actions_taken": actions,
        }
