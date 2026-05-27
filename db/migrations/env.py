"""
Villa — Alembic Environment
Configuração do contexto de migrations. Suporta modo offline (geração de SQL)
e online (aplicação direta ao banco) com SQLAlchemy 2.0 async.

Carrega DATABASE_URL de core.config.settings (que lê do .env) — fonte única
da verdade de credenciais, sem duplicação no alembic.ini.
"""
from __future__ import annotations

import asyncio
import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# ── Config base do Alembic ─────────────────────────────────────────────────────
config = context.config

# Logging (se o alembic.ini tem [loggers], aplica)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

logger = logging.getLogger("alembic.env")


# ── Garantir que a raiz do projeto está no sys.path ────────────────────────────
# Alembic carrega este arquivo via importlib (não como módulo do pacote do projeto),
# então 'from core.config' falha sem este patch.
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Injetar URL do .env via core.config ────────────────────────────────────────
# Importante: usar sync_database_url (não async). Alembic Async usa async_engine,
# mas o DRIVER é o mesmo asyncpg. O método sync_database_url existe em core.config
# como fallback síncrono (postgresql://). Para Alembic Async usamos a URL async.
from core.config import settings  # noqa: E402

config.set_main_option("sqlalchemy.url", settings.async_database_url)


# ── target_metadata: o Base do projeto Villa ──────────────────────────────────
# Imports importam todos os modelos pra Base.metadata ficar populado.
# Isso é o que permite alembic --autogenerate (futuro) detectar drift.
from core.database import Base  # noqa: E402, F401
import core.models  # noqa: E402, F401  — registra todos os modelos no Base

# Os modelos do Client OS também precisam estar registrados pra Alembic vê-los
try:
    import memory.client_os.schema  # noqa: E402, F401
except ImportError:
    # Permite o env.py rodar mesmo se o módulo não estiver disponível
    # (ex: durante baseline antes de Client OS estar instalado)
    logger.warning("memory.client_os.schema não pôde ser importado — ignorando")

target_metadata = Base.metadata


# ══════════════════════════════════════════════════════════════════════════════
# Modo OFFLINE
# ══════════════════════════════════════════════════════════════════════════════
def run_migrations_offline() -> None:
    """
    Roda migrations em modo offline (gera SQL ao invés de aplicar ao banco).

    Útil pra revisar SQL antes de aplicar em produção. Usado via:
        alembic upgrade head --sql > migration.sql
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,         # detecta mudanças de tipo de coluna
        compare_server_default=True,  # detecta mudanças de default
    )

    with context.begin_transaction():
        context.run_migrations()


# ══════════════════════════════════════════════════════════════════════════════
# Modo ONLINE (async — usa engine assíncrono do projeto)
# ══════════════════════════════════════════════════════════════════════════════
def do_run_migrations(connection: Connection) -> None:
    """Executa as migrations dentro de uma conexão sync (interno do Alembic)."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    Cria um engine async, conecta, e roda as migrations dentro de uma
    transação async via run_sync.
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Modo online: aplica migrations diretamente ao banco."""
    asyncio.run(run_async_migrations())


# ══════════════════════════════════════════════════════════════════════════════
# Dispatch
# ══════════════════════════════════════════════════════════════════════════════
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
