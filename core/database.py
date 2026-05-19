"""
Villa — Conexão com PostgreSQL
Engine async, session factory, e dependency injection para FastAPI.
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text

from core.config import settings


# ── Engine async ──
engine: AsyncEngine = create_async_engine(
    settings.async_database_url,
    echo=settings.debug,                # Loga queries em dev
    pool_size=10,                        # Conexões simultâneas
    max_overflow=20,                     # Conexões extras sob carga
    pool_pre_ping=True,                  # Verifica conexão antes de usar
    pool_recycle=3600,                   # Recicla conexões a cada 1h
)

# ── Session factory ──
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,              # Objetos acessíveis após commit
)


# ── Base declarativa para modelos ──
class Base(DeclarativeBase):
    """Classe base para todos os modelos SQLAlchemy do Villa."""
    pass


# ── Dependency injection para FastAPI ──
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency do FastAPI que fornece uma sessão do banco.
    Uso em routes:
        @router.get("/exemplo")
        async def exemplo(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Context manager para uso fora do FastAPI ──
@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager para usar o banco fora de routes (scheduler, modules).
    Uso:
        async with get_db_session() as db:
            result = await db.execute(...)
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Funções de ciclo de vida ──
async def init_db() -> None:
    """
    Inicializa o banco: cria tabelas e extensões.
    Chamado no startup do FastAPI.
    """
    async with engine.begin() as conn:
        # Habilitar pgvector
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        # Criar todas as tabelas definidas nos modelos
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Fecha o engine e todas as conexões. Chamado no shutdown do FastAPI."""
    await engine.dispose()


async def check_db_health() -> bool:
    """Verifica se o banco está respondendo. Usado no healthcheck."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
