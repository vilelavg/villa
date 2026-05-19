"""
Villa — Setup do Scheduler
Configura APScheduler com todas as rotinas automáticas.

Tarefas agendadas:
    - Diária: todo dia às 7h (coleta métricas, alertas, lembretes)
    - Semanal: sexta às 8h (relatórios, retroalimentação, custos, LGPD)
    - Monitor: a cada 30 min (SLA, budget pacing, show rate, saúde)

O scheduler roda dentro do mesmo processo do FastAPI.
Todas as tarefas são async e usam sessões de banco independentes.
"""

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from core.config import settings

logger = logging.getLogger("villa.scheduler")

# Instância global do scheduler
scheduler = AsyncIOScheduler(
    timezone="America/Sao_Paulo",
    job_defaults={
        "coalesce": True,          # Se perdeu uma execução, roda 1x só (não acumula)
        "max_instances": 1,         # Nunca roda 2 instâncias da mesma tarefa
        "misfire_grace_time": 3600,  # 1h de tolerância para tarefas atrasadas
    },
)


async def _run_daily():
    """Wrapper para rotina diária com tratamento de erro."""
    try:
        from scheduler.daily_routines import run_daily_routine
        logger.info("🌅 Iniciando rotina diária")
        result = await run_daily_routine()
        tasks_ok = sum(1 for t in result.get("tasks", []) if t.get("success"))
        tasks_total = len(result.get("tasks", []))
        logger.info(f"🌅 Rotina diária concluída: {tasks_ok}/{tasks_total} tarefas OK")
    except Exception as e:
        logger.error(f"❌ Erro na rotina diária: {e}", exc_info=True)


async def _run_weekly():
    """Wrapper para rotina semanal com tratamento de erro."""
    try:
        from scheduler.weekly_routines import run_weekly_routine
        logger.info("📊 Iniciando rotina semanal")
        result = await run_weekly_routine()
        tasks_ok = sum(1 for t in result.get("tasks", []) if t.get("success"))
        tasks_total = len(result.get("tasks", []))
        logger.info(f"📊 Rotina semanal concluída: {tasks_ok}/{tasks_total} tarefas OK")
    except Exception as e:
        logger.error(f"❌ Erro na rotina semanal: {e}", exc_info=True)


async def _run_monitors():
    """Wrapper para monitores contínuos com tratamento de erro."""
    try:
        from scheduler.monitors import run_monitors
        result = await run_monitors()
        monitors = result.get("monitors", [])
        alerts = sum(
            m.get("alerts_created", 0) + m.get("leads_breaching_sla", 0)
            for m in monitors
        )
        if alerts > 0:
            logger.warning(f"🔔 Monitores: {alerts} situações detectadas")
    except Exception as e:
        logger.error(f"❌ Erro nos monitores: {e}", exc_info=True)


def setup_scheduler() -> AsyncIOScheduler:
    """
    Configura e retorna o scheduler com todas as tarefas.
    Chamado no startup do FastAPI (core/main.py).
    """
    # ── Rotina diária ──
    scheduler.add_job(
        _run_daily,
        trigger=CronTrigger(
            hour=settings.daily_routine_hour,
            minute=settings.daily_routine_minute,
        ),
        id="daily_routine",
        name="Rotina Diária (métricas, alertas, lembretes)",
        replace_existing=True,
    )

    # ── Rotina semanal ──
    scheduler.add_job(
        _run_weekly,
        trigger=CronTrigger(
            day_of_week=settings.weekly_report_day,
            hour=settings.weekly_report_hour,
            minute=0,
        ),
        id="weekly_routine",
        name="Rotina Semanal (relatórios, retroalimentação, LGPD)",
        replace_existing=True,
    )

    # ── Monitores contínuos ──
    scheduler.add_job(
        _run_monitors,
        trigger=IntervalTrigger(
            minutes=settings.monitor_interval_minutes,
        ),
        id="continuous_monitors",
        name="Monitores (SLA, budget, show rate, saúde)",
        replace_existing=True,
    )

    logger.info(
        f"⏰ Scheduler configurado: "
        f"diária às {settings.daily_routine_hour}:{settings.daily_routine_minute:02d}, "
        f"semanal {settings.weekly_report_day} às {settings.weekly_report_hour}h, "
        f"monitores a cada {settings.monitor_interval_minutes} min"
    )

    return scheduler


def get_scheduler_status() -> dict:
    """Retorna status do scheduler e suas tarefas."""
    jobs = []
    for job in scheduler.get_jobs():
        next_run = job.next_run_time
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": next_run.isoformat() if next_run else None,
            "trigger": str(job.trigger),
        })

    return {
        "running": scheduler.running,
        "timezone": str(scheduler.timezone),
        "jobs": jobs,
    }
