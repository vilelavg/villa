"""
Villa — Módulo M10: Integração Smooth Dentistry
Integração com a comunidade odontológica (sociedade paralela).
795+ membros, portal Wix + app, R$997/mês.
Suporte automatizado, onboarding de membros, notificações.

Nota: Smooth Dentistry é operação independente da WebXP
com 6 sócios. Este módulo depende da aprovação da governança.
"""

from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import ModuleCode, User
from modules.base import BaseModule
from memory.feedback_loop import FeedbackLoop
from memory.knowledge_base import KnowledgeBaseService
from integrations.whatsapp import whatsapp


SYSTEM_PROMPT = """Você é o Villa, módulo de suporte da comunidade Smooth Dentistry.

A Smooth Dentistry é uma comunidade de dentistas com 795+ membros. Você atua como suporte automatizado:
- Responde dúvidas sobre a comunidade, planos e benefícios
- Orienta novos membros no onboarding
- Notifica sobre novos conteúdos e eventos
- Encaminha questões complexas para os administradores

Tom: acolhedor, profissional, voltado para educação continuada.
Nunca dê conselho médico. Foque nos benefícios da comunidade.
"""

FAQ_PROMPT = """Responda a dúvida deste membro da Smooth Dentistry:

MEMBRO: {member_name}
DÚVIDA: "{question}"

BASE DE CONHECIMENTO:
{knowledge_context}

Responda de forma acolhedora e direta (2-3 frases).
Se não souber, diga que vai encaminhar para a equipe.
Sem ponto final no fim.

Responda APENAS com o texto da mensagem.
"""


class M10Smooth(BaseModule):
    code = ModuleCode.M10_SMOOTH
    name = "Smooth Dentistry"
    description = "Integração com a comunidade Smooth Dentistry: suporte automatizado, onboarding de membros, FAQ, notificações."

    KEYWORDS = ["smooth", "comunidade", "membro", "membros", "dentistry", "portal", "assinatura"]

    async def can_handle(self, message: str, context: Optional[dict] = None) -> float:
        msg_lower = message.lower()
        matches = sum(1 for kw in self.KEYWORDS if kw in msg_lower)
        if matches >= 2: return 0.85
        if matches >= 1: return 0.6
        return 0.0

    async def execute(self, message: str, db: AsyncSession, user: Optional[User] = None, client_slug: Optional[str] = None, context: Optional[dict] = None) -> dict:
        feedback_loop = FeedbackLoop(db)
        kb = KnowledgeBaseService(db)
        context = context or {}

        # Buscar na base de conhecimento da Smooth
        kb_results = await kb.search(message, limit=3, doc_type="faq")
        knowledge_text = "\n".join(f"- {r['title']}: {r['text'][:200]}" for r in kb_results) if kb_results else "(sem dados na base)"

        # Gerar resposta
        member_name = context.get("member_name", "membro")

        response = await self.ask_claude(
            message=FAQ_PROMPT.format(
                member_name=member_name,
                question=message,
                knowledge_context=knowledge_text,
            ),
            db=db,
            system_override=SYSTEM_PROMPT,
        )

        reply = response["text"].strip()
        should_transfer = "[TRANSFERIR_HUMANO]" in reply
        clean_reply = reply.replace("[TRANSFERIR_HUMANO]", "").strip()

        await feedback_loop.record_decision(
            module=self.code, action="suporte_smooth",
            input_data={"question": message[:200]},
            output_data={"reply": clean_reply[:200], "kb_used": len(kb_results), "transferred": should_transfer},
        )

        return {
            "success": True,
            "message": clean_reply,
            "data": {"kb_results_used": len(kb_results), "transferred": should_transfer},
            "actions_taken": ["smooth_support_response"],
        }
