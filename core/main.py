"""
Villa — Entry Point
FastAPI application com lifecycle (startup/shutdown),
CORS, registro de rotas e middleware.
"""

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routes.clients import router as clients_router
from api.routes.commands import router as commands_router

# Rotas
from api.routes.health import router as health_router
from api.routes.reports import router as reports_router
from api.routes.webhooks import router as webhooks_router
from core.config import settings
from core.database import close_db, init_db
from core.orchestrator import setup_orchestrator

# ── Variável global de uptime ──
_start_time: float = 0.0


def get_uptime() -> float:
    """Retorna tempo de atividade em segundos."""
    return time.time() - _start_time


# ── Lifecycle ──
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup e shutdown do Villa."""
    global _start_time
    _start_time = time.time()

    # ── STARTUP ──
    print("🏠 Villa — Iniciando...")
    print(f"   Ambiente: {settings.environment}")
    print(f"   Debug: {settings.debug}")

    # Inicializar banco de dados
    await init_db()
    print("   ✅ PostgreSQL conectado")

    # Inicializar orquestrador com módulos e event routes
    setup_orchestrator()
    print("   ✅ Orquestrador configurado")

    # Inicializar scheduler (rotinas automáticas)
    from scheduler.setup import scheduler, setup_scheduler

    setup_scheduler()
    scheduler.start()
    print("   ✅ Scheduler iniciado (diária, semanal, monitores)")

    print("🟢 Villa — Online")
    print(f"   API: http://{settings.app_host}:{settings.app_port}")
    print(f"   Docs: http://{settings.app_host}:{settings.app_port}/docs")

    yield  # App rodando

    # ── SHUTDOWN ──
    print("🔴 Villa — Desligando...")
    scheduler.shutdown(wait=False)
    print("   ✅ Scheduler parado")
    await close_db()
    print("   ✅ Conexões fechadas")


# ── App ──
app = FastAPI(
    title="Villa",
    description="Agente SaaS multi-modular — WebXP Agency",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.is_development else None,
    redoc_url="/redoc" if settings.is_development else None,
)


# ── CORS ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Middleware: tempo de resposta ──
@app.middleware("http")
async def add_timing_header(request: Request, call_next):
    """Adiciona header X-Process-Time em todas as respostas."""
    start = time.time()
    response = await call_next(request)
    process_time = time.time() - start
    response.headers["X-Process-Time"] = f"{process_time:.4f}"
    return response


# ── Middleware: tratamento global de erros ──
@app.exception_handler(PermissionError)
async def permission_error_handler(request: Request, exc: PermissionError):
    return JSONResponse(
        status_code=403,
        content={"detail": str(exc)},
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=422,
        content={"detail": str(exc)},
    )


# ── Registrar rotas ──
app.include_router(health_router, tags=["Health"])
app.include_router(webhooks_router, prefix="/webhook", tags=["Webhooks"])
app.include_router(commands_router, prefix="/command", tags=["Commands"])
app.include_router(reports_router, prefix="/reports", tags=["Reports"])
app.include_router(clients_router, prefix="/clients", tags=["Clients"])


# ── Rota raiz ──
@app.get("/", include_in_schema=False)
async def root():
    return {
        "name": "Villa",
        "version": "0.1.0",
        "status": "online",
        "docs": "/docs" if settings.is_development else None,
    }
