"""
Villa — Audit Log
Log imutável de todas as ações do sistema.
Garante rastreabilidade para LGPD e auditoria.
Toda ação do Villa passa por aqui.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import ActionRisk, AuditLog, ModuleCode


class AuditService:
    """
    Registra toda ação do Villa no banco de dados.
    Log imutável — nunca é editado ou deletado.

    Uso:
        audit = AuditService(db_session)
        await audit.log(
            action="gerar_roteiro",
            module=ModuleCode.M01_ROTEIROS,
            resource_type="roteiro",
            resource_id="uuid-do-roteiro",
            details={"client": "ottoboni", "hook_score": 8.5},
        )
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def log(
        self,
        action: str,
        module: ModuleCode | None = None,
        user_id: str | None = None,
        risk_level: ActionRisk = ActionRisk.LOW,
        resource_type: str | None = None,
        resource_id: str | None = None,
        details: dict | None = None,
        ip_address: str | None = None,
        success: bool = True,
        error_message: str | None = None,
        required_confirmation: bool = False,
    ) -> AuditLog:
        """
        Registra uma ação no audit log.

        Args:
            action: Descrição da ação (ex: "gerar_roteiro", "mover_card_kommo")
            module: Módulo que executou a ação
            user_id: ID do usuário que disparou (None se foi o scheduler)
            risk_level: low (auto), medium (log+notifica), high (precisa confirmação)
            resource_type: Tipo do recurso afetado (lead, campaign, roteiro)
            resource_id: ID do recurso afetado
            details: Dados adicionais em JSON
            ip_address: IP de origem (se via API)
            success: Se a ação foi bem-sucedida
            error_message: Mensagem de erro (se falhou)
            required_confirmation: Se essa ação exigiu confirmação humana

        Returns:
            O registro de audit log criado
        """
        entry = AuditLog(
            id=str(uuid4()),
            user_id=user_id,
            module=module,
            action=action,
            risk_level=risk_level,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            ip_address=ip_address,
            success=success,
            error_message=error_message,
            required_confirmation=required_confirmation,
        )

        self.db.add(entry)
        await self.db.flush()  # Grava imediatamente sem esperar o commit
        return entry

    async def log_high_risk(
        self,
        action: str,
        module: ModuleCode,
        resource_type: str,
        resource_id: str,
        details: dict | None = None,
        user_id: str | None = None,
    ) -> AuditLog:
        """
        Atalho para ações de alto risco.
        Automaticamente marca como required_confirmation=True.
        """
        return await self.log(
            action=action,
            module=module,
            user_id=user_id,
            risk_level=ActionRisk.HIGH,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            required_confirmation=True,
        )

    async def confirm_action(
        self,
        audit_id: str,
        confirmed_by: str,
    ) -> AuditLog | None:
        """
        Registra a confirmação humana de uma ação de alto risco.

        Args:
            audit_id: ID do registro no audit log
            confirmed_by: ID do usuário que confirmou
        """
        result = await self.db.execute(select(AuditLog).where(AuditLog.id == audit_id))
        entry = result.scalar_one_or_none()

        if entry and entry.required_confirmation:
            entry.confirmed_by = confirmed_by
            entry.confirmed_at = datetime.utcnow()
            await self.db.flush()

        return entry

    async def get_recent(
        self,
        limit: int = 50,
        module: ModuleCode | None = None,
        risk_level: ActionRisk | None = None,
    ) -> list[AuditLog]:
        """Busca registros recentes do audit log com filtros opcionais."""
        query = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)

        if module:
            query = query.where(AuditLog.module == module)
        if risk_level:
            query = query.where(AuditLog.risk_level == risk_level)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_by_resource(
        self,
        resource_type: str,
        resource_id: str,
    ) -> list[AuditLog]:
        """Busca todo o histórico de ações sobre um recurso específico."""
        result = await self.db.execute(
            select(AuditLog)
            .where(AuditLog.resource_type == resource_type)
            .where(AuditLog.resource_id == resource_id)
            .order_by(AuditLog.created_at.desc())
        )
        return list(result.scalars().all())

    async def count_errors(
        self,
        module: ModuleCode | None = None,
        hours: int = 24,
    ) -> int:
        """Conta erros nas últimas N horas. Útil para monitoramento."""
        from datetime import timedelta

        cutoff = datetime.utcnow() - timedelta(hours=hours)

        query = (
            select(AuditLog).where(AuditLog.success == False).where(AuditLog.created_at >= cutoff)
        )
        if module:
            query = query.where(AuditLog.module == module)

        result = await self.db.execute(query)
        return len(result.scalars().all())
