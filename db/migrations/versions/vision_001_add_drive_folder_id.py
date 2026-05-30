"""add drive_folder_id to clients

Revision ID: vision_001
Revises: autonomy_001
Create Date: 2026-05-29

Adiciona a coluna drive_folder_id na tabela clients. Mapeia cada cliente
para sua pasta de criativos no Google Drive — usado pelo Vision Pipeline
para que o M11 (Hipoteses) veja os criativos antes de propor variacoes.

Coluna nullable, sem default. Cliente sem pasta configurada cai no
fallback de busca por nome (Vision Pipeline trata defensivamente).
"""

import sqlalchemy as sa
from alembic import op

revision = "vision_001"
down_revision = "autonomy_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column("drive_folder_id", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("clients", "drive_folder_id")
