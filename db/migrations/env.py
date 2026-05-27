"""
Villa — Alembic Environment
Configuração do contexto de migrations. Suporta modo offline (geração de SQL)
e online (aplicação direta ao banco) com SQLAlchemy 2.0 async.

Carrega DATABASE_URL de core.config.settings (que lê do .env) — fonte única
da verdade de credenciais, sem duplicação no alembic.ini.

REGRA: respeita URL injetada externamente (ex: conftest.py apontando para
villa_test_db). Só usa settings.async_database_url como fallback.
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
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Injetar URL do .env via core.config — APENAS se não foi injetada externamente ──
# CORREÇÃO CRÍTICA: o env.py anterior sobrescrevia incondicionalmente a URL,
# fazendo com que alembic_cfg.set_main_option(..., TEST_DATABASE_URL) no conftest.py
# fosse silenciosamente ignorado. Resultado: todo upgrade/downgrade de teste
# executava contra villa_db (banco principal), corrompendo-o.
#
# Agora: se a URL já foi definida por quem chamou (conftest, CLI com -x, etc),
# respeitamos. Só usamos settings como fallback.
from core.config import settings  # noqa: E402

_url_already_set = config.get_main_option("sqlalchemy.url")
if not _url_already_set:
    config.set_main_option("sqlalchemy.url", settings.async_database_url)
    logger.info("alembic env.py: URL carregada de settings (%s:.../%s)",
                settings.postgres_host, settings.postgres_db)
else:
    logger.info("alembic env.py: URL injetada externamente — mantida (%s)",
                _url_already_set.split("@")[-1] if "@" in _url_already_set else _url_already_set)


# ── target_metadata: o Base do projeto Villa ──────────────────────────────────
from core.database import Base  # noqa: E402, F401
import core.models  # noqa: E402, F401

try:
    import memory.client_os.schema  # noqa: E402, F401
except ImportError:
    logger.warning("memory.client_os.schema não pôde ser importado — ignorando")

target_metadata = Base.metadata


# ══════════════════════════════════════════════════════════════════════════════
# Modo OFFLINE
# ══════════════════════════════════════════════════════════════════════════════
def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ══════════════════════════════════════════════════════════════════════════════
# Modo ONLINE (async)
# ══════════════════════════════════════════════════════════════════════════════
def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


# ══════════════════════════════════════════════════════════════════════════════
# Dispatch
# ══════════════════════════════════════════════════════════════════════════════
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
