"""
Villa — Health Check
Endpoint para monitoramento do status do sistema.
Verifica: banco de dados, Redis, módulos ativos.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis

from core.config import settings
from core.database import get_db, check_db_health
from core.models import ModuleConfig, HealthResponse

router = APIRouter()


async def _check_redis() -> bool:
    """Verifica se o Redis está respondendo."""
    try:
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        await r.ping()
        await r.close()
        return True
    except Exception:
        return False


async def _count_active_modules(db: AsyncSession) -> int:
    """Conta módulos ativos no banco."""
    try:
        result = await db.execute(
            select(func.count()).select_from(ModuleConfig).where(ModuleConfig.is_active == True)
        )
        return result.scalar() or 0
    except Exception:
        return 0


@router.get("/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_db)):
    """
    Health check completo do Villa.
    Retorna status de cada componente.
    
    Usado por:
    - Docker HEALTHCHECK
    - Monitoramento externo (UptimeRobot, etc.)
    - Dashboard interno
    """
    from core.main import get_uptime

    db_ok = await check_db_health()
    redis_ok = await _check_redis()
    modules_active = await _count_active_modules(db)

    # Determinar status geral
    if db_ok and redis_ok:
        status = "healthy"
    elif db_ok:
        status = "degraded"  # Funciona sem Redis (perde cache/rate limit)
    else:
        status = "unhealthy"  # Sem banco não funciona

    return HealthResponse(
        status=status,
        version="0.1.0",
        environment=settings.environment,
        database=db_ok,
        redis=redis_ok,
        modules_active=modules_active,
        uptime_seconds=get_uptime(),
    )


@router.get("/health/simple")
async def simple_health():
    """Health check simples (só verifica se o app está rodando)."""
    return {"status": "ok"}


@router.get("/health/scheduler")
async def scheduler_status():
    """
    Status do scheduler — tarefas agendadas e próximas execuções.

    Exemplo de resposta:
        GET /health/scheduler
        {"running": true, "jobs_count": 3, "jobs": [...]}
    """
    from scheduler.setup import get_scheduler_status
    return get_scheduler_status()


@router.post("/health/scheduler/trigger/{job_id}")
async def trigger_scheduler_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    # Apenas admin pode disparar manualmente
):
    """
    Dispara uma tarefa do scheduler manualmente (para testes).

    job_id opções:
        - daily_routine    → rotina diária (relatórios, alertas, métricas)
        - weekly_routine   → rotina semanal (relatórios semanais, retroalimentação)
        - continuous_monitors → monitores de SLA e budget

    Exemplo:
        POST /health/scheduler/trigger/daily_routine
    """
    valid_jobs = {
        "daily_routine": "scheduler.daily_routines.run_daily_routine",
        "weekly_routine": "scheduler.weekly_routines.run_weekly_routine",
        "continuous_monitors": "scheduler.monitors.run_monitors",
    }

    if job_id not in valid_jobs:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400,
            detail=f"job_id inválido. Opções: {list(valid_jobs.keys())}"
        )

    try:
        module_path, func_name = valid_jobs[job_id].rsplit(".", 1)
        mod = __import__(module_path, fromlist=[func_name])
        fn = getattr(mod, func_name)
        result = await fn()
        return {
            "status": "executed",
            "job_id": job_id,
            "result": result,
        }
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=f"Erro ao executar {job_id}: {str(e)}")
