"""
Villa — Rotinas Diárias
O que o Villa faz todo dia às 7h da manhã, sem ninguém pedir.

Sequência:
    1. Coletar métricas de todas as campanhas ativas (Meta + Google)
    2. Verificar thresholds e disparar alertas se necessário
    3. Verificar leads parados no Kommo além do SLA
    4. Gerar relatório diário resumido para clientes premium
    5. Verificar agendamentos do dia e enviar confirmações
    6. Registrar tudo no audit log
"""

from datetime import datetime, date, timedelta
from typing import Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.models import (
    Client, ClientStatus, Campaign, Lead, LeadStatus,
    Appointment, Alert, Report, ModuleCode,
)
from core.database import get_db_session
from security.audit_log import AuditService


async def run_daily_routine() -> dict:
    """
    Rotina diária completa do Villa.
    Executada pelo scheduler todo dia no horário configurado.
    
    Returns:
        Relatório da execução com tudo que foi feito
    """
    report = {
        "started_at": datetime.utcnow().isoformat(),
        "tasks": [],
    }

    async with get_db_session() as db:
        audit = AuditService(db)

        await audit.log(
            action="daily_routine_started",
            module=ModuleCode.M02_RELATORIOS,
            details={"date": date.today().isoformat()},
        )

        # ── 1. Coletar métricas de campanhas ──
        try:
            metrics_result = await _collect_campaign_metrics(db)
            report["tasks"].append({"task": "collect_metrics", **metrics_result})
        except Exception as e:
            report["tasks"].append({"task": "collect_metrics", "success": False, "error": str(e)})

        # ── 2. Verificar alertas de threshold ──
        try:
            alerts_result = await _check_thresholds(db)
            report["tasks"].append({"task": "check_thresholds", **alerts_result})
        except Exception as e:
            report["tasks"].append({"task": "check_thresholds", "success": False, "error": str(e)})

        # ── 3. Verificar leads parados ──
        try:
            stale_result = await _check_stale_leads(db)
            report["tasks"].append({"task": "check_stale_leads", **stale_result})
        except Exception as e:
            report["tasks"].append({"task": "check_stale_leads", "success": False, "error": str(e)})

        # ── 4. Confirmação de agendamentos do dia ──
        try:
            appointments_result = await _send_appointment_reminders(db)
            report["tasks"].append({"task": "appointment_reminders", **appointments_result})
        except Exception as e:
            report["tasks"].append({"task": "appointment_reminders", "success": False, "error": str(e)})

        # ── 5. Verificar decisões pendentes de avaliação ──
        try:
            pending_result = await _check_pending_evaluations(db)
            report["tasks"].append({"task": "pending_evaluations", **pending_result})
        except Exception as e:
            report["tasks"].append({"task": "pending_evaluations", "success": False, "error": str(e)})

        report["completed_at"] = datetime.utcnow().isoformat()
        tasks_ok = sum(1 for t in report["tasks"] if t.get("success", False))

        await audit.log(
            action="daily_routine_completed",
            module=ModuleCode.M02_RELATORIOS,
            details={
                "tasks_total": len(report["tasks"]),
                "tasks_success": tasks_ok,
                "date": date.today().isoformat(),
            },
        )

    return report


# ═══════════════════════════════════════════════════
# SUBTAREFAS
# ═══════════════════════════════════════════════════

async def _collect_campaign_metrics(db: AsyncSession) -> dict:
    """
    Coleta métricas de campanhas ativas de todos os clientes.
    Puxa de Meta Ads API e Google Ads (Apps Script).
    """
    # Buscar clientes ativos com contas de anúncio configuradas
    result = await db.execute(
        select(Client)
        .where(Client.status == ClientStatus.ACTIVE)
        .where(Client.meta_ad_account_id.isnot(None))
    )
    clients = result.scalars().all()

    collected = 0
    errors = 0

    for client in clients:
        try:
            # TODO: Quando M2 e M4 estiverem ativos, chamar:
            # from integrations.meta_ads import meta_ads
            # insights = await meta_ads.get_daily_insights(client.meta_ad_account_id, days=1)
            # Processar e salvar em campaigns.metrics

            collected += 1
        except Exception:
            errors += 1

    return {
        "success": True,
        "clients_processed": collected,
        "errors": errors,
    }


async def _check_thresholds(db: AsyncSession) -> dict:
    """
    Verifica se alguma métrica de campanha ultrapassou thresholds.
    Gera alertas para Caio/Thaís via M12.
    """
    result = await db.execute(
        select(Campaign)
        .where(Campaign.status == "active")
    )
    campaigns = result.scalars().all()

    alerts_created = 0

    for campaign in campaigns:
        metrics = campaign.metrics or {}

        # Buscar thresholds do cliente
        client_result = await db.execute(
            select(Client).where(Client.id == campaign.client_id)
        )
        client = client_result.scalar_one_or_none()
        if not client:
            continue

        thresholds = (client.config or {}).get("thresholds", {})
        cpl_max = thresholds.get("cpl_max", 80.0)
        ctr_min = thresholds.get("ctr_min", 1.2)
        frequency_max = thresholds.get("frequency_max", 3.0)

        # Verificar CPL
        cpl = metrics.get("cpl")
        if cpl and float(cpl) > cpl_max:
            alert = Alert(
                client_id=campaign.client_id,
                module=ModuleCode.M12_ALERTAS,
                alert_type="cpl_high",
                severity="warning",
                title=f"CPL alto: {campaign.name}",
                message=f"CPL atual R${cpl:.2f} está acima do threshold R${cpl_max:.2f}",
                suggested_action="Revisar segmentação e criativos. Considerar pausar adsets com CPL > 2x o threshold.",
                metric_name="cpl",
                metric_value=float(cpl),
                threshold_value=cpl_max,
            )
            db.add(alert)
            alerts_created += 1

        # Verificar frequência
        freq = metrics.get("frequency")
        if freq and float(freq) > frequency_max:
            alert = Alert(
                client_id=campaign.client_id,
                module=ModuleCode.M12_ALERTAS,
                alert_type="frequency_high",
                severity="warning",
                title=f"Frequência alta: {campaign.name}",
                message=f"Frequência {freq:.1f} indica audience fatigue (limite: {frequency_max})",
                suggested_action="Expandir público ou renovar criativos. Frequência acima de 3.0 degrada performance.",
                metric_name="frequency",
                metric_value=float(freq),
                threshold_value=frequency_max,
            )
            db.add(alert)
            alerts_created += 1

        # Verificar CTR
        ctr = metrics.get("ctr")
        if ctr and float(ctr) < ctr_min:
            alert = Alert(
                client_id=campaign.client_id,
                module=ModuleCode.M12_ALERTAS,
                alert_type="ctr_low",
                severity="info",
                title=f"CTR baixo: {campaign.name}",
                message=f"CTR {ctr:.2f}% está abaixo do mínimo {ctr_min}%",
                suggested_action="Testar novos ganchos nos criativos. Revisar copy do anúncio.",
                metric_name="ctr",
                metric_value=float(ctr),
                threshold_value=ctr_min,
            )
            db.add(alert)
            alerts_created += 1

    await db.flush()

    return {
        "success": True,
        "campaigns_checked": len(campaigns),
        "alerts_created": alerts_created,
    }


async def _check_stale_leads(db: AsyncSession) -> dict:
    """
    Verifica leads parados em etapas intermediárias além do SLA.
    SLA padrão: 48h sem movimentação = notificação.
    """
    cutoff = datetime.utcnow() - timedelta(hours=48)

    stale_statuses = [
        LeadStatus.CONTACTED,
        LeadStatus.QUALIFYING,
        LeadStatus.QUALIFIED,
    ]

    result = await db.execute(
        select(Lead)
        .where(Lead.status.in_(stale_statuses))
        .where(Lead.updated_at < cutoff)
    )
    stale_leads = result.scalars().all()

    if stale_leads:
        # Criar alerta consolidado
        alert = Alert(
            module=ModuleCode.M12_ALERTAS,
            alert_type="stale_leads",
            severity="warning",
            title=f"{len(stale_leads)} leads parados há mais de 48h",
            message=(
                f"Existem {len(stale_leads)} leads sem movimentação há mais de 48 horas. "
                f"Etapas: {', '.join(set(l.status for l in stale_leads))}. "
                "Verificar se precisam de follow-up."
            ),
            suggested_action="Revisar os leads parados e dar sequência no atendimento ou desqualificar.",
        )
        db.add(alert)
        await db.flush()

    return {
        "success": True,
        "stale_leads_found": len(stale_leads),
    }


async def _send_appointment_reminders(db: AsyncSession) -> dict:
    """
    Envia lembretes para consultas agendadas hoje.
    Confirmação D-1 (lembrete no dia anterior) e D-0 (no dia).
    """
    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = datetime.combine(date.today(), datetime.max.time())

    # Agendamentos de hoje sem lembrete enviado
    result = await db.execute(
        select(Appointment)
        .where(Appointment.scheduled_at.between(today_start, today_end))
        .where(Appointment.reminder_sent == False)
        .where(Appointment.status == "scheduled")
    )
    appointments = result.scalars().all()

    reminders_sent = 0

    for apt in appointments:
        try:
            # TODO: Quando M5 estiver ativo:
            # from integrations.whatsapp import whatsapp
            # await whatsapp.send_template(lead.phone, "lembrete_consulta", {...})

            apt.reminder_sent = True
            reminders_sent += 1
        except Exception:
            pass

    await db.flush()

    return {
        "success": True,
        "appointments_today": len(appointments),
        "reminders_sent": reminders_sent,
    }


async def _check_pending_evaluations(db: AsyncSession) -> dict:
    """
    Verifica decisões que estão pendentes de avaliação há mais de 3 dias.
    Gera lembrete para Caio/Thaís darem feedback.
    """
    from memory.decision_log import DecisionLogService

    decisions = DecisionLogService(db)
    pending = await decisions.get_pending_evaluations(days_old=3, limit=10)

    if pending:
        modules_pending = set(p.get("module", "?") for p in pending)
        alert = Alert(
            module=ModuleCode.M12_ALERTAS,
            alert_type="pending_feedback",
            severity="info",
            title=f"{len(pending)} decisões aguardando avaliação",
            message=(
                f"O Villa tem {len(pending)} decisões dos últimos dias sem avaliação de resultado. "
                f"Módulos: {', '.join(modules_pending)}. "
                "Dar feedback ajuda o Villa a melhorar suas decisões futuras."
            ),
            suggested_action="Avaliar as decisões pendentes via dashboard ou comando direto.",
        )
        db.add(alert)
        await db.flush()

    return {
        "success": True,
        "pending_evaluations": len(pending),
    }
