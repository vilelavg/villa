"""baseline — registra estado atual sem aplicar nada

Revision ID: 000_baseline
Revises:
Create Date: 2026-05-26

Esta migration é um "stamp" — quando rodamos `alembic stamp 000_baseline`,
o Alembic registra que este é o ponto zero do versionamento, sem rodar nenhum
SQL. Útil pra bancos que já têm o schema aplicado por outros meios (no nosso
caso, os SQLs 001_initial_schema.sql e 002_stand_by_and_new_modules.sql foram
rodados via docker-entrypoint-initdb.d).

A partir daqui, toda mudança de schema vira uma migration nova com
down_revision apontando pra cá.
"""
from __future__ import annotations

from typing import Sequence, Union


# ── Identificadores ───────────────────────────────────────────────────────────
revision: str = "000_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Nada a fazer — schema já existe no banco. Apenas marca o baseline."""
    pass


def downgrade() -> None:
    """Não há como desfazer o baseline — é o ponto zero."""
    pass
