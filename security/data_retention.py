"""
Villa — Política de Retenção de Dados
Aplica regras de retenção automática conforme LGPD.
Executado pelo scheduler semanalmente.

Regras:
    - Leads não convertidos: 12 meses
    - Dados de campanha: 24 meses
    - Transcrições: 6 meses
    - Audit logs: 36 meses (obrigação legal)
    - Conversas encerradas: 6 meses
"""

from datetime import datetime, timedelta

from sqlalchemy import select

from core.database import get_db_session
from core.models import Conversation, KnowledgeDocument, Lead, LeadStatus, ModuleCode
from security.audit_log import AuditService

# Períodos de retenção em dias
RETENTION_DAYS = {
    "leads_not_converted": 365,         # 12 meses
    "conversations_closed": 180,        # 6 meses
    "transcriptions": 180,              # 6 meses
    "campaign_data": 730,               # 24 meses
    "audit_logs": 1095,                 # 36 meses — NÃO deletar antes
}


async def enforce_retention() -> dict:
    """
    Aplica políticas de retenção de dados.
    Retorna relatório do que foi limpo.
    
    Chamado pelo scheduler semanalmente.
    """
    report = {}

    async with get_db_session() as db:
        audit = AuditService(db)

        # ── Leads não convertidos (12 meses) ──
        cutoff_leads = datetime.utcnow() - timedelta(days=RETENTION_DAYS["leads_not_converted"])
        non_converted_statuses = [
            LeadStatus.NEW,
            LeadStatus.CONTACTED,
            LeadStatus.QUALIFYING,
            LeadStatus.DISQUALIFIED,
            LeadStatus.LOST,
        ]

        result = await db.execute(
            select(Lead)
            .where(Lead.status.in_(non_converted_statuses))
            .where(Lead.created_at < cutoff_leads)
        )
        old_leads = result.scalars().all()

        if old_leads:
            for lead in old_leads:
                # Anonimizar em vez de deletar (LGPD permite anonimização)
                lead.name = "ANONIMIZADO"
                lead.phone = None
                lead.email = None
                lead.raw_data = {}

            report["leads_anonymized"] = len(old_leads)
            await audit.log(
                action="data_retention_leads",
                module=ModuleCode.M09_ARQUIVOS,
                details={"count": len(old_leads), "cutoff": cutoff_leads.isoformat()},
            )

        # ── Conversas encerradas (6 meses) ──
        cutoff_convs = datetime.utcnow() - timedelta(days=RETENTION_DAYS["conversations_closed"])

        result = await db.execute(
            select(Conversation)
            .where(Conversation.is_active == False)
            .where(Conversation.ended_at < cutoff_convs)
        )
        old_convs = result.scalars().all()

        if old_convs:
            for conv in old_convs:
                conv.messages = []  # Limpa mensagens, mantém metadata
                conv.summary = "[Dados removidos por política de retenção]"

            report["conversations_cleaned"] = len(old_convs)
            await audit.log(
                action="data_retention_conversations",
                module=ModuleCode.M09_ARQUIVOS,
                details={"count": len(old_convs), "cutoff": cutoff_convs.isoformat()},
            )

        # ── Transcrições antigas (6 meses) ──
        cutoff_transcriptions = datetime.utcnow() - timedelta(days=RETENTION_DAYS["transcriptions"])

        result = await db.execute(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.doc_type == "transcricao")
            .where(KnowledgeDocument.created_at < cutoff_transcriptions)
        )
        old_transcriptions = result.scalars().all()

        if old_transcriptions:
            for doc in old_transcriptions:
                doc.content = "[Conteúdo removido por política de retenção]"
                doc.chunks = []

            report["transcriptions_cleaned"] = len(old_transcriptions)

        report["executed_at"] = datetime.utcnow().isoformat()
        return report
