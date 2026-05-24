"""
Villa — Monitores Contínuos
Loops que rodam a cada N minutos verificando métricas em tempo real.

Diferente das rotinas diárias/semanais, os monitores rodam com
frequência alta (padrão: a cada 30 min) e buscam anomalias
que precisam de ação imediata.

Monitores ativos:
    - Budget: campanha gastando mais rápido que o esperado
    - CPL spike: CPL subiu repentinamente em relação à média
    - Show rate: taxa de comparecimento caindo
    - SLA de resposta: leads sem resposta além do tempo limite
    - Saúde do sistema: módulos com muitos erros
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db_session
from core.models import (
    Alert,
    Appointment,
    Campaign,
    Client,
    Lead,
    LeadStatus,
    ModuleCode,
)
from security.audit_log import AuditService


async def run_monitors() -> dict:
    """
    Executa todos os monitores.
    Chamado pelo scheduler a cada monitor_interval_minutes.
    """
    report = {
        "timestamp": datetime.utcnow().isoformat(),
        "monitors": [],
    }

    async with get_db_session() as db:
        # ── Monitor 1: SLA de resposta a leads ──
        try:
            sla_result = await _monitor_response_sla(db)
            report["monitors"].append({"monitor": "response_sla", **sla_result})
        except Exception as e:
            report["monitors"].append({"monitor": "response_sla", "success": False, "error": str(e)})

        # ── Monitor 2: Budget pacing ──
        try:
            budget_result = await _monitor_budget_pacing(db)
            report["monitors"].append({"monitor": "budget_pacing", **budget_result})
        except Exception as e:
            report["monitors"].append({"monitor": "budget_pacing", "success": False, "error": str(e)})

        # ── Monitor 3: Taxa de show (comparecimento) ──
        try:
            show_result = await _monitor_show_rate(db)
            report["monitors"].append({"monitor": "show_rate", **show_result})
        except Exception as e:
            report["monitors"].append({"monitor": "show_rate", "success": False, "error": str(e)})

        # ── Monitor 4: Saúde dos módulos ──
        try:
            health_result = await _monitor_module_health(db)
            report["monitors"].append({"monitor": "module_health", **health_result})
        except Exception as e:
            report["monitors"].append({"monitor": "module_health", "success": False, "error": str(e)})

    return report


async def _monitor_response_sla(db: AsyncSession) -> dict:
    """
    Verifica leads que não receberam resposta dentro do SLA.
    SLA padrão: 15 minutos para primeiro contato.
    
    Se o lead chegou há mais de 15 min e ainda está como NEW,
    ninguém (nem o Villa nem a SDR) respondeu.
    """
    sla_minutes = 15
    cutoff = datetime.utcnow() - timedelta(minutes=sla_minutes)

    result = await db.execute(
        select(Lead)
        .where(Lead.status == LeadStatus.NEW)
        .where(Lead.created_at < cutoff)
    )
    breached_leads = result.scalars().all()

    if breached_leads:
        # Agrupar por cliente
        by_client = {}
        for lead in breached_leads:
            by_client.setdefault(lead.client_id, []).append(lead)

        for client_id, leads in by_client.items():
            # Verificar se já existe alerta recente (evitar spam)
            recent_alert = await db.execute(
                select(Alert)
                .where(Alert.client_id == client_id)
                .where(Alert.alert_type == "sla_breach")
                .where(Alert.created_at > datetime.utcnow() - timedelta(hours=1))
            )
            if recent_alert.scalar_one_or_none():
                continue

            oldest = min(l.created_at for l in leads if l.created_at)
            minutes_waiting = int((datetime.utcnow() - oldest).total_seconds() / 60)

            alert = Alert(
                client_id=client_id,
                module=ModuleCode.M12_ALERTAS,
                alert_type="sla_breach",
                severity="critical",
                title=f"{len(leads)} lead(s) sem resposta há {minutes_waiting} min",
                message=(
                    f"Existem {len(leads)} leads aguardando primeiro contato "
                    f"há mais de {sla_minutes} minutos. O lead mais antigo está "
                    f"esperando há {minutes_waiting} minutos."
                ),
                suggested_action=(
                    "Verificar imediatamente. Se a qualificação automática (M3) está ativa, "
                    "pode haver um erro. Se está desativada, a SDR precisa atender."
                ),
            )
            db.add(alert)

        await db.flush()

    return {
        "success": True,
        "leads_breaching_sla": len(breached_leads),
        "sla_minutes": sla_minutes,
    }


async def _monitor_budget_pacing(db: AsyncSession) -> dict:
    """
    Verifica se campanhas estão gastando mais rápido que o esperado.
    
    Lógica: se gastou mais de 60% do budget diário antes das 14h,
    o ritmo está acelerado e pode estourar.
    """
    result = await db.execute(
        select(Campaign).where(Campaign.status == "active")
    )
    campaigns = result.scalars().all()

    alerts_created = 0
    now = datetime.utcnow()
    hour = now.hour

    # Só faz sentido verificar pacing durante o dia
    if hour < 8 or hour > 22:
        return {"success": True, "skipped": True, "reason": "outside_business_hours"}

    expected_pacing = hour / 24  # Fração do dia que passou

    for campaign in campaigns:
        metrics = campaign.metrics or {}
        daily_budget = metrics.get("daily_budget")
        spend_today = metrics.get("spend_today") or metrics.get("spend")

        if not daily_budget or not spend_today:
            continue

        actual_pacing = float(spend_today) / float(daily_budget)

        # Se gastou mais de 150% do esperado pro horário
        if actual_pacing > expected_pacing * 1.5 and actual_pacing > 0.5:
            client_result = await db.execute(
                select(Client).where(Client.id == campaign.client_id)
            )
            client = client_result.scalar_one_or_none()

            alert = Alert(
                client_id=campaign.client_id,
                module=ModuleCode.M12_ALERTAS,
                alert_type="budget_pacing_fast",
                severity="warning",
                title=f"Budget acelerado: {campaign.name}",
                message=(
                    f"Campanha gastou {actual_pacing:.0%} do budget diário, "
                    f"mas apenas {expected_pacing:.0%} do dia passou. "
                    f"Projeção: vai estourar o budget em {int((1-actual_pacing)/(actual_pacing/hour))}h."
                ),
                suggested_action="Verificar se há um pico de impressões incomum. Considerar ajustar bid ou pausar temporariamente.",
                metric_name="budget_pacing",
                metric_value=actual_pacing,
                threshold_value=expected_pacing * 1.5,
            )
            db.add(alert)
            alerts_created += 1

    await db.flush()

    return {
        "success": True,
        "campaigns_checked": len(campaigns),
        "alerts_created": alerts_created,
    }


async def _monitor_show_rate(db: AsyncSession) -> dict:
    """
    Monitora taxa de comparecimento em consultas.
    Se a taxa de no-show está acima de 40%, alerta.
    """
    week_start = datetime.utcnow() - timedelta(days=7)

    result = await db.execute(
        select(Appointment)
        .where(Appointment.scheduled_at >= week_start)
        .where(Appointment.scheduled_at < datetime.utcnow())
    )
    past_appointments = result.scalars().all()

    if len(past_appointments) < 3:
        return {"success": True, "skipped": True, "reason": "insufficient_data"}

    no_shows = sum(1 for a in past_appointments if a.status == "no_show")
    total = len(past_appointments)
    no_show_rate = no_shows / total

    if no_show_rate > 0.4:
        # Identificar clientes com mais no-shows
        by_client = {}
        for apt in past_appointments:
            if apt.status == "no_show":
                by_client.setdefault(apt.client_id, 0)
                by_client[apt.client_id] += 1

        alert = Alert(
            module=ModuleCode.M12_ALERTAS,
            alert_type="show_rate_low",
            severity="warning",
            title=f"Taxa de no-show alta: {no_show_rate:.0%}",
            message=(
                f"Nos últimos 7 dias, {no_shows} de {total} consultas "
                f"resultaram em no-show ({no_show_rate:.0%}). "
                f"Clientes afetados: {len(by_client)}"
            ),
            suggested_action=(
                "Revisar processo de confirmação D-1. Considerar adicionar lembrete D-0. "
                "Verificar se mensagem de confirmação está sendo enviada corretamente."
            ),
            metric_name="no_show_rate",
            metric_value=no_show_rate,
            threshold_value=0.4,
        )
        db.add(alert)
        await db.flush()

    return {
        "success": True,
        "appointments_checked": total,
        "no_shows": no_shows,
        "no_show_rate": round(no_show_rate * 100, 1) if total else 0,
    }


async def _monitor_module_health(db: AsyncSession) -> dict:
    """
    Verifica saúde dos módulos.
    Módulo com mais de 5 erros na última hora = alerta.
    """
    audit = AuditService(db)
    problems = []

    for module_code in ModuleCode:
        error_count = await audit.count_errors(module=module_code, hours=1)
        if error_count >= 5:
            problems.append({
                "module": module_code.value,
                "errors_last_hour": error_count,
            })

    if problems:
        modules_text = ", ".join(f"{p['module']} ({p['errors_last_hour']} erros)" for p in problems)
        alert = Alert(
            module=ModuleCode.M12_ALERTAS,
            alert_type="module_errors",
            severity="critical",
            title="Módulos com erros frequentes",
            message=f"Módulos com mais de 5 erros na última hora: {modules_text}",
            suggested_action="Verificar logs de erro dos módulos afetados. Possível problema de integração ou API fora do ar.",
        )
        db.add(alert)
        await db.flush()

    return {
        "success": True,
        "problem_modules": problems,
    }
