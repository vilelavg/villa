"""
Villa — Módulo M15: Monitor Smooth (Inteligência de Comunidade)
Adicionado pós-reunião Caio+Thaís (19/05/2026).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PROPÓSITO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
O Villa entra no grupo WhatsApp da comunidade Smooth
como um participante silencioso. Ele:

• Lê e armazena todas as mensagens
• Identifica membros mais ativos e seu engajamento
• Mapeia dores, necessidades e padrões de comportamento
• Alimenta o banco de dados para campanhas e decisões estratégicas
• NUNCA fala nada no grupo — apenas escuta e registra

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INTEGRAÇÃO TÉCNICA
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
As mensagens chegam via webhook do N8N (que monitora
o WhatsApp da comunidade via Evolution API ou similar).

Endpoint: POST /webhooks com:
  event_type: "smooth_group_message"
  payload: {member_phone, member_name, content, timestamp}

Para importação em massa (histórico):
  POST /commands com:
  mode: "ingest_batch"
  messages: [{member_phone, member_name, content, timestamp}]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STATUS: STAND_BY aguardando configuração do webhook
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
O módulo está construído e pronto. Aguarda:
1. Configuração do N8N para monitorar o grupo
2. Aprovação da governança Smooth (6 sócios)
3. Setup do webhook no Evolution API / WhatsApp Web
"""

import logging
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import (
    ModuleCode,
    SmoothInsight,
    SmoothMember,
    SmoothMessage,
    User,
)
from memory.feedback_loop import FeedbackLoop
from modules.base import BaseModule

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════
# SYSTEM PROMPTS
# ═══════════════════════════════════════════════════

SYSTEM_CLASSIFY = """Você é o Villa, analisando mensagens da comunidade Smooth Dentistry.

Dado o texto de uma mensagem de grupo de dentistas, classifique-a e extraia informações relevantes.

Retorne APENAS um JSON com:
{
  "category": "dor" | "duvida" | "elogio" | "networking" | "conteudo" | "admin" | "outro",
  "sentiment": "positive" | "negative" | "neutral",
  "topics": ["lista", "de", "tópicos", "identificados"],
  "pain_points": ["dores ou frustrações mencionadas (vazio se não houver)"],
  "relevance_score": 0-10
}

Categorias:
- dor: frustração, problema, dificuldade clínica ou de negócio
- duvida: pergunta técnica ou sobre a comunidade
- elogio: feedback positivo, agradecimento
- networking: busca por parceiro, indicação, contato
- conteudo: compartilhamento de artigo, case, aprendizado
- admin: avisos, organização, burocracia
- outro: conversa informal, memes, etc.

Tópicos comuns: implante, ortodontia, prótese, cirurgia, marketing, precificação, equipe, faculdade, atualização, protocolo.
Sem markdown. Apenas JSON.
"""

SYSTEM_INSIGHTS = """Você é o Villa, analista estratégico da WebXP Agency para a comunidade Smooth Dentistry.

Vou te dar dados de mensagens do grupo da comunidade. Gere um relatório de inteligência para Caio.

O relatório deve cobrir:
1. Resumo executivo (3-4 frases sobre o período)
2. Top 5 dores mais mencionadas (com exemplos reais)
3. Top 5 tópicos mais discutidos
4. Membros mais ativos e seu perfil (nome + o que costumam discutir)
5. Oportunidades de conteúdo: temas que a WebXP poderia explorar em cursos/materiais
6. Recomendações de campanha: o que faria sentido anunciar agora para essa audiência?

Tom: direto, estratégico, como um analista de inteligência de mercado apresentando para o C-level.
"""

CLASSIFY_PROMPT = """Classifique a mensagem abaixo:

MEMBRO: {member_name}
MENSAGEM: "{content}"

Retorne APENAS o JSON pedido.
"""

INSIGHTS_PROMPT = """Gere o relatório de inteligência da comunidade Smooth.

PERÍODO ANALISADO: {period_start} a {period_end}
TOTAL DE MENSAGENS: {total_messages}
MEMBROS ATIVOS: {active_members}

TOP DORES IDENTIFICADAS:
{top_pain_points}

TOP TÓPICOS DISCUTIDOS:
{top_topics}

MEMBROS MAIS ENGAJADOS:
{top_members}

EXEMPLOS DE MENSAGENS COM ALTA RELEVÂNCIA:
{high_relevance_messages}
"""


class M15MonitorSmooth(BaseModule):
    """Inteligência silenciosa da comunidade Smooth Dentistry."""

    code = ModuleCode.M15_MONITOR_SMOOTH
    name = "Monitor Smooth"
    description = (
        "Participa silenciosamente da comunidade Smooth Dentistry: "
        "lê mensagens, mapeia dores e comportamentos, identifica membros ativos "
        "e gera insights para campanhas. Nunca envia mensagens."
    )

    KEYWORDS = [
        "smooth",
        "comunidade smooth",
        "monitor smooth",
        "grupo smooth",
        "membros smooth",
        "insights smooth",
        "relatório smooth",
        "monitoramento",
        "inteligência comunidade",
    ]

    async def can_handle(self, message: str, context: dict | None = None) -> float:
        # Webhook de mensagem do grupo
        if context and context.get("event_type") == "smooth_group_message":
            return 0.99

        # Scheduler para insights semanais
        if context and context.get("event_type") == "scheduler_weekly_smooth_insights":
            return 0.99

        msg_lower = message.lower()

        if "monitor smooth" in msg_lower or "smooth" in msg_lower:
            return 0.85

        matches = sum(1 for kw in self.KEYWORDS if kw in msg_lower)
        if matches >= 2:
            return 0.8
        if matches >= 1:
            return 0.5
        return 0.0

    async def execute(
        self,
        message: str,
        db: AsyncSession,
        user: User | None = None,
        client_slug: str | None = None,
        context: dict | None = None,
    ) -> dict:
        context = context or {}
        event_type = context.get("event_type", "")

        # Mensagem individual do webhook
        if event_type == "smooth_group_message":
            return await self._ingest_message(context.get("payload", {}), db)

        # Lote de mensagens (importação histórica)
        if context.get("mode") == "ingest_batch":
            return await self._ingest_batch(context.get("messages", []), db)

        # Insights semanais (scheduler)
        if (
            event_type == "scheduler_weekly_smooth_insights"
            or "insight" in message.lower()
            or "relatório smooth" in message.lower()
        ):
            return await self._generate_insights(db, days=7)

        # Status e estatísticas
        if "status" in message.lower() or "estatísticas" in message.lower():
            return await self._get_stats(db)

        # Default: stats
        return await self._get_stats(db)

    # ═══════════════════════════════════════════════════
    # INGEST — receber mensagem individual
    # ═══════════════════════════════════════════════════

    async def _ingest_message(self, payload: dict, db: AsyncSession) -> dict:
        """
        Processa uma única mensagem recebida via webhook.
        Classifica, armazena e atualiza perfil do membro.
        """
        member_phone = payload.get("member_phone", "")
        member_name = payload.get("member_name", "Desconhecido")
        content = payload.get("content", "").strip()
        raw_timestamp = payload.get("timestamp")

        if not content:
            return {"success": False, "error": "empty_message", "actions_taken": []}

        # Parse timestamp
        message_timestamp = None
        if raw_timestamp:
            try:
                message_timestamp = datetime.fromisoformat(str(raw_timestamp))
            except (ValueError, TypeError):
                message_timestamp = datetime.utcnow()

        # Classificar com Claude
        classification = await self._classify_message(content, member_name, db)

        # Salvar mensagem
        msg = SmoothMessage(
            id=str(uuid4()),
            member_phone=member_phone,
            member_name=member_name,
            content=content,
            media_type=payload.get("media_type", "text"),
            is_reply=payload.get("is_reply", False),
            message_timestamp=message_timestamp or datetime.utcnow(),
            category=classification.get("category", "outro"),
            sentiment=classification.get("sentiment", "neutral"),
            topics=classification.get("topics", []),
            pain_points=classification.get("pain_points", []),
            analyzed=True,
        )
        db.add(msg)
        await db.flush()

        # Atualizar perfil do membro
        await self._update_member_profile(db, member_phone, member_name, classification)

        return {
            "success": True,
            "message": f"Mensagem de {member_name} processada. Categoria: {classification.get('category', 'outro')}.",
            "data": {
                "message_id": msg.id,
                "category": msg.category,
                "sentiment": msg.sentiment,
                "pain_points": msg.pain_points,
            },
            "actions_taken": ["message_stored", "member_updated"],
        }

    # ═══════════════════════════════════════════════════
    # INGEST BATCH — importação de histórico
    # ═══════════════════════════════════════════════════

    async def _ingest_batch(self, messages: list, db: AsyncSession) -> dict:
        """
        Importa lote de mensagens (histórico do grupo).
        Classifica em paralelo usando Haiku para economia.
        """
        stored = 0
        errors = 0

        for msg_data in messages:
            try:
                await self._ingest_message(msg_data, db)
                stored += 1
            except Exception:
                errors += 1

        return {
            "success": True,
            "message": f"Importação concluída: {stored} mensagens processadas ({errors} erros).",
            "data": {"stored": stored, "errors": errors, "total": len(messages)},
            "actions_taken": ["batch_import_complete"],
        }

    # ═══════════════════════════════════════════════════
    # INSIGHTS — relatório de inteligência
    # ═══════════════════════════════════════════════════

    async def _generate_insights(self, db: AsyncSession, days: int = 7) -> dict:
        """Gera relatório de inteligência da comunidade para o período."""
        period_start = datetime.utcnow() - timedelta(days=days)
        period_end = datetime.utcnow()

        # Buscar mensagens do período
        result = await db.execute(
            select(SmoothMessage)
            .where(SmoothMessage.message_timestamp >= period_start)
            .where(SmoothMessage.analyzed == True)
            .order_by(desc(SmoothMessage.message_timestamp))
        )
        messages = result.scalars().all()

        if not messages:
            return {
                "success": True,
                "message": f"Nenhuma mensagem registrada nos últimos {days} dias. Configure o webhook do N8N para iniciar o monitoramento.",
                "actions_taken": ["insights_no_data"],
            }

        # Agregar dados
        all_pain_points: dict = {}
        all_topics: dict = {}

        for msg in messages:
            for pp in msg.pain_points or []:
                all_pain_points[pp] = all_pain_points.get(pp, 0) + 1
            for topic in msg.topics or []:
                all_topics[topic] = all_topics.get(topic, 0) + 1

        top_pain_points = sorted(all_pain_points.items(), key=lambda x: x[1], reverse=True)[:10]
        top_topics = sorted(all_topics.items(), key=lambda x: x[1], reverse=True)[:10]

        # Membros mais ativos
        member_result = await db.execute(
            select(SmoothMember).order_by(desc(SmoothMember.engagement_score)).limit(10)
        )
        top_members = member_result.scalars().all()

        # Mensagens com alta relevância (para exemplos)
        high_rel_result = await db.execute(
            select(SmoothMessage)
            .where(SmoothMessage.message_timestamp >= period_start)
            .where(SmoothMessage.category == "dor")
            .order_by(desc(SmoothMessage.message_timestamp))
            .limit(5)
        )
        high_rel_messages = high_rel_result.scalars().all()

        # Contagem de membros ativos no período
        active_result = await db.execute(
            select(func.count(func.distinct(SmoothMessage.member_phone))).where(
                SmoothMessage.message_timestamp >= period_start
            )
        )
        active_members_count = active_result.scalar_one_or_none() or 0

        # Montar texto para o Claude
        pain_text = "\n".join(f"- {pp} ({count}x)" for pp, count in top_pain_points)
        topics_text = "\n".join(f"- {t} ({count}x)" for t, count in top_topics)
        members_text = "\n".join(
            f"- {m.name or m.phone}: {m.message_count} msgs | score: {m.engagement_score:.0f}"
            + (f" | temas: {', '.join((m.main_topics or [])[:3])}" if m.main_topics else "")
            for m in top_members
        )
        examples_text = "\n".join(
            f'- [{msg.member_name}]: "{msg.content[:150]}"' for msg in high_rel_messages
        )

        response = await self.ask_claude(
            message=INSIGHTS_PROMPT.format(
                period_start=period_start.strftime("%d/%m/%Y"),
                period_end=period_end.strftime("%d/%m/%Y"),
                total_messages=len(messages),
                active_members=active_members_count,
                top_pain_points=pain_text or "(nenhuma dor registrada)",
                top_topics=topics_text or "(nenhum tópico registrado)",
                top_members=members_text or "(nenhum membro registrado)",
                high_relevance_messages=examples_text or "(sem exemplos)",
            ),
            db=db,
            system_override=SYSTEM_INSIGHTS,
        )

        # Salvar insight
        insight = SmoothInsight(
            id=str(uuid4()),
            period_start=period_start,
            period_end=period_end,
            messages_analyzed=len(messages),
            insight_type="weekly_summary",
            title=f"Relatório Smooth — {period_start.strftime('%d/%m')} a {period_end.strftime('%d/%m/%Y')}",
            summary=response["text"],
            top_topics=[{"topic": t, "count": c} for t, c in top_topics[:5]],
            top_pain_points=[{"pain": p, "count": c} for p, c in top_pain_points[:5]],
            top_members=[{"name": m.name, "score": m.engagement_score} for m in top_members[:5]],
        )
        db.add(insight)
        await db.flush()

        feedback_loop = FeedbackLoop(db)
        await feedback_loop.record_decision(
            module=self.code,
            action="generate_smooth_insights",
            input_data={"messages_analyzed": len(messages), "days": days},
            output_data={"insight_id": insight.id, "report_length": len(response["text"])},
        )

        return {
            "success": True,
            "message": response["text"],
            "data": {
                "insight_id": insight.id,
                "messages_analyzed": len(messages),
                "active_members": active_members_count,
                "top_pain_points": [p for p, _ in top_pain_points[:5]],
                "top_topics": [t for t, _ in top_topics[:5]],
            },
            "actions_taken": ["insights_generated", "insight_saved"],
            "tokens_used": response.get("tokens_input", 0) + response.get("tokens_output", 0),
        }

    # ═══════════════════════════════════════════════════
    # STATS — resumo rápido
    # ═══════════════════════════════════════════════════

    async def _get_stats(self, db: AsyncSession) -> dict:
        """Retorna estatísticas básicas do monitoramento."""
        total_msgs = await db.execute(select(func.count(SmoothMessage.id)))
        total_members = await db.execute(select(func.count(SmoothMember.id)))
        total_insights = await db.execute(select(func.count(SmoothInsight.id)))

        last_msg = await db.execute(
            select(SmoothMessage.message_timestamp)
            .order_by(desc(SmoothMessage.message_timestamp))
            .limit(1)
        )
        last_timestamp = last_msg.scalar_one_or_none()

        return {
            "success": True,
            "message": (
                f"📊 *Monitor Smooth — Status*\n\n"
                f"📨 Mensagens registradas: {total_msgs.scalar_one_or_none() or 0}\n"
                f"👥 Membros mapeados: {total_members.scalar_one_or_none() or 0}\n"
                f"💡 Insights gerados: {total_insights.scalar_one_or_none() or 0}\n"
                f"🕐 Última mensagem: {last_timestamp.strftime('%d/%m/%Y %H:%M') if last_timestamp else 'Nenhuma ainda'}\n\n"
                f"_Webhook: POST /webhooks com event_type='smooth_group_message'_"
            ),
            "actions_taken": ["stats_returned"],
        }

    # ═══════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════

    async def _classify_message(self, content: str, member_name: str, db: AsyncSession) -> dict:
        """Classifica uma mensagem usando Claude Haiku."""
        import json

        response = await self.ask_claude(
            message=CLASSIFY_PROMPT.format(
                member_name=member_name,
                content=content[:500],
            ),
            db=db,
            system_override=SYSTEM_CLASSIFY,
            model="fast",  # Haiku — classificação simples
        )
        raw = response["text"].strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        try:
            return json.loads(raw.strip())
        except json.JSONDecodeError:
            return {
                "category": "outro",
                "sentiment": "neutral",
                "topics": [],
                "pain_points": [],
                "relevance_score": 0,
            }

    async def _update_member_profile(
        self,
        db: AsyncSession,
        phone: str,
        name: str,
        classification: dict,
    ) -> None:
        """Atualiza ou cria perfil do membro com a nova mensagem."""
        result = await db.execute(select(SmoothMember).where(SmoothMember.phone == phone))
        member = result.scalar_one_or_none()
        now = datetime.utcnow()

        if not member:
            member = SmoothMember(
                id=str(uuid4()),
                phone=phone,
                name=name,
                message_count=0,
                first_message_at=now,
                engagement_score=0.0,
                main_topics=[],
                main_pain_points=[],
            )
            db.add(member)
            await db.flush()

        # Atualizar contagem
        member.message_count += 1
        member.last_message_at = now
        if member.name == "Desconhecido" and name != "Desconhecido":
            member.name = name

        # Atualizar tópicos (rolling top-10)
        topics = list(member.main_topics or [])
        for t in classification.get("topics", []):
            if t not in topics:
                topics.append(t)
        member.main_topics = topics[-10:]

        # Atualizar dores
        pains = list(member.main_pain_points or [])
        for pp in classification.get("pain_points", []):
            if pp not in pains:
                pains.append(pp)
        member.main_pain_points = pains[-10:]

        # Engagement score: quanto mais mensagens e dores/tópicos identificados, maior
        relevance = classification.get("relevance_score", 0)
        member.engagement_score = min(100.0, member.engagement_score * 0.95 + relevance * 5)

        if member.engagement_score > 60:
            member.is_high_value = True

        await db.flush()
