"""
Villa — Rotinas Semanais
Executadas toda sexta-feira pela manhã.

Sequência:
    1. Gerar relatórios semanais para todos os clientes ativos
    2. Análise de retroalimentação comercial ↔ marketing
    3. Resumo de performance dos módulos (auto-diagnóstico)
    4. Limpeza de dados (política de retenção LGPD)
    5. Resumo de custos de API da semana
"""

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db_session
from core.models import Client, ClientStatus, ModuleCode, Report
from memory.decision_log import DecisionLogService
from security.audit_log import AuditService


async def run_weekly_routine() -> dict:
    """
    Rotina semanal do Villa.
    Executada pelo scheduler toda sexta no horário configurado.
    """
    report = {
        "started_at": datetime.utcnow().isoformat(),
        "week_ending": date.today().isoformat(),
        "tasks": [],
    }

    async with get_db_session() as db:
        audit = AuditService(db)

        await audit.log(
            action="weekly_routine_started",
            module=ModuleCode.M02_RELATORIOS,
            details={"week_ending": date.today().isoformat()},
        )

        # ── 1. Gerar relatórios semanais ──
        try:
            reports_result = await _generate_weekly_reports(db)
            report["tasks"].append({"task": "weekly_reports", **reports_result})
        except Exception as e:
            report["tasks"].append({"task": "weekly_reports", "success": False, "error": str(e)})

        # ── 2. Análise de retroalimentação ──
        try:
            retro_result = await _retroalimentacao_analysis(db)
            report["tasks"].append({"task": "retroalimentacao", **retro_result})
        except Exception as e:
            report["tasks"].append({"task": "retroalimentacao", "success": False, "error": str(e)})

        # ── 3. Auto-diagnóstico dos módulos ──
        try:
            diag_result = await _module_diagnostics(db)
            report["tasks"].append({"task": "module_diagnostics", **diag_result})
        except Exception as e:
            report["tasks"].append(
                {"task": "module_diagnostics", "success": False, "error": str(e)}
            )

        # ── 4. Política de retenção (LGPD) ──
        try:
            from security.data_retention import enforce_retention

            retention_result = await enforce_retention()
            report["tasks"].append({"task": "data_retention", "success": True, **retention_result})
        except Exception as e:
            report["tasks"].append({"task": "data_retention", "success": False, "error": str(e)})

        # ── 5. Resumo de custos ──
        try:
            cost_result = await _cost_summary(db)
            report["tasks"].append({"task": "cost_summary", **cost_result})
        except Exception as e:
            report["tasks"].append({"task": "cost_summary", "success": False, "error": str(e)})

        report["completed_at"] = datetime.utcnow().isoformat()

        await audit.log(
            action="weekly_routine_completed",
            module=ModuleCode.M02_RELATORIOS,
            details={"tasks": len(report["tasks"])},
        )

    return report


async def _generate_weekly_reports(db: AsyncSession) -> dict:
    """Gera relatórios semanais para todos os clientes ativos."""
    result = await db.execute(select(Client).where(Client.status == ClientStatus.ACTIVE))
    clients = result.scalars().all()

    generated = 0
    week_end = date.today()
    week_start = week_end - timedelta(days=7)

    for client in clients:
        try:
            # TODO: Quando M2 estiver ativo, chamar:
            # from modules.m02_relatorios.agent import M02Relatorios
            # await m02.generate_report(client, "weekly", week_start, week_end)

            # Por ora, criar placeholder de relatório
            from uuid import uuid4

            report_entry = Report(
                id=str(uuid4()),
                client_id=client.id,
                report_type="weekly",
                period_start=week_start,
                period_end=week_end,
                data={"status": "pending_module_m02"},
                analysis=None,
            )
            db.add(report_entry)
            generated += 1
        except Exception:
            pass

    await db.flush()

    return {
        "success": True,
        "clients_active": len(clients),
        "reports_generated": generated,
        "period": f"{week_start} a {week_end}",
    }


async def _retroalimentacao_analysis(db: AsyncSession) -> dict:
    """
    Análise semanal de retroalimentação comercial ↔ marketing.
    Cruza dados de conversão do Kommo com performance de campanhas.

    Perguntas que responde:
        - Leads qualificados estão convertendo?
        - Leads não qualificados estão sendo gerados por qual campanha?
        - Taxa de show está caindo? Em quais clientes?
        - Quais objeções comerciais podem melhorar o marketing?
    """
    from core.models import Lead, LeadStatus

    week_start = datetime.utcnow() - timedelta(days=7)

    # Leads da semana por status
    result = await db.execute(select(Lead).where(Lead.created_at >= week_start))
    week_leads = result.scalars().all()

    total = len(week_leads)
    by_status = {}
    for lead in week_leads:
        status = lead.status
        by_status[status] = by_status.get(status, 0) + 1

    qualified = by_status.get(LeadStatus.QUALIFIED, 0)
    won = by_status.get(LeadStatus.WON, 0)
    lost = by_status.get(LeadStatus.LOST, 0)
    disqualified = by_status.get(LeadStatus.DISQUALIFIED, 0)

    return {
        "success": True,
        "total_leads_week": total,
        "by_status": {k: v for k, v in by_status.items()},
        "qualification_rate": round(qualified / total * 100, 1) if total else 0,
        "conversion_rate": round(won / (qualified or 1) * 100, 1),
        "disqualification_rate": round(disqualified / total * 100, 1) if total else 0,
    }


async def _module_diagnostics(db: AsyncSession) -> dict:
    """
    Auto-diagnóstico semanal dos módulos.
    Verifica taxa de sucesso, erros, e eficiência de cada módulo.
    """
    decisions = DecisionLogService(db)

    diagnostics = {}
    for module_code in ModuleCode:
        stats = await decisions.get_success_rate(module_code, days=7)
        if stats["total"] > 0:
            diagnostics[module_code.value] = stats

    # Identificar módulos com problemas
    problem_modules = [
        name
        for name, stats in diagnostics.items()
        if stats.get("success_rate", 100) < 70 and stats["total"] >= 3
    ]

    return {
        "success": True,
        "modules_with_activity": len(diagnostics),
        "diagnostics": diagnostics,
        "problem_modules": problem_modules,
    }


async def _cost_summary(db: AsyncSession) -> dict:
    """Resumo de custos de API da semana."""
    decisions = DecisionLogService(db)
    return await decisions.get_cost_summary(days=7, by_module=True)
