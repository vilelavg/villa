"""Adiciona tabelas do Client OS

Revision ID: client_os_001
Revises: <AJUSTAR_AQUI>
Create Date: 2026-05-24

NOTAS DE INTEGRAÃ‡ÃƒO:

1. Antes de rodar, ajuste `down_revision` abaixo para o ID da Ãºltima
   migration do seu projeto. VocÃª pode descobrir com:
       alembic heads
   ou olhando o arquivo mais recente em db/migrations/versions/.

2. Esta migration assume que a tabela `clients` JÃ EXISTE com PK `id`
   do tipo UUID (default Villa, conforme db/migrations/001_initial_schema.sql).
   Todas as 6 FKs client_id usam postgresql.UUID(as_uuid=True).

3. Requer PostgreSQL com JSONB (vem por padrÃ£o a partir do 9.4) â€”
   o Villa jÃ¡ usa pgvector, entÃ£o isso estÃ¡ garantido.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers
revision = "client_os_001"
down_revision = "000_baseline"  # <-- AJUSTAR: setar para o head atual antes de rodar
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------- client_state ----------
    op.create_table(
        "client_state",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("summary_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", name="uq_client_state_client_id"),
    )
    op.create_index("ix_client_state_client_id", "client_state", ["client_id"])

    # ---------- client_facts ----------
    op.create_table(
        "client_facts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column(
            "value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "source",
            sa.String(length=64),
            nullable=False,
            server_default="manual",
        ),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "client_id", "category", "key", name="uq_client_facts_client_cat_key"
        ),
    )
    op.create_index("ix_client_facts_client_id", "client_facts", ["client_id"])
    op.create_index(
        "ix_client_facts_client_category",
        "client_facts",
        ["client_id", "category"],
    )

    # ---------- client_episodes ----------
    op.create_table(
        "client_episodes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("episode_type", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "outcome",
            sa.String(length=32),
            nullable=False,
            server_default="neutral",
        ),
        sa.Column("module_source", sa.String(length=16), nullable=True),
        sa.Column(
            "linked_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_client_episodes_client_id", "client_episodes", ["client_id"])
    op.create_index(
        "ix_client_episodes_client_type_occurred",
        "client_episodes",
        ["client_id", "episode_type", "occurred_at"],
    )
    op.create_index(
        "ix_client_episodes_client_occurred",
        "client_episodes",
        ["client_id", "occurred_at"],
    )

    # ---------- client_preferences ----------
    op.create_table(
        "client_preferences",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic", sa.String(length=64), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column(
            "evidence_count", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column(
            "last_observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "client_id",
            "topic",
            "pattern",
            name="uq_client_pref_client_topic_pattern",
        ),
    )
    op.create_index(
        "ix_client_preferences_client_id", "client_preferences", ["client_id"]
    )
    op.create_index(
        "ix_client_preferences_client_topic",
        "client_preferences",
        ["client_id", "topic"],
    )

    # ---------- client_pending ----------
    op.create_table(
        "client_pending",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "owner",
            sa.String(length=64),
            nullable=False,
            server_default="villa",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="open",
        ),
        sa.Column("module_source", sa.String(length=16), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_client_pending_client_id", "client_pending", ["client_id"])
    op.create_index(
        "ix_client_pending_client_status_due",
        "client_pending",
        ["client_id", "status", "due_at"],
    )

    # ---------- client_objectives ----------
    op.create_table(
        "client_objectives",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("target_metric", sa.String(length=64), nullable=True),
        sa.Column("target_value", sa.Float(), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "progress",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_client_objectives_client_id", "client_objectives", ["client_id"]
    )
    op.create_index(
        "ix_client_objectives_client_status",
        "client_objectives",
        ["client_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_client_objectives_client_status", table_name="client_objectives"
    )
    op.drop_index(
        "ix_client_objectives_client_id", table_name="client_objectives"
    )
    op.drop_table("client_objectives")

    op.drop_index(
        "ix_client_pending_client_status_due", table_name="client_pending"
    )
    op.drop_index("ix_client_pending_client_id", table_name="client_pending")
    op.drop_table("client_pending")

    op.drop_index(
        "ix_client_preferences_client_topic", table_name="client_preferences"
    )
    op.drop_index(
        "ix_client_preferences_client_id", table_name="client_preferences"
    )
    op.drop_table("client_preferences")

    op.drop_index(
        "ix_client_episodes_client_occurred", table_name="client_episodes"
    )
    op.drop_index(
        "ix_client_episodes_client_type_occurred", table_name="client_episodes"
    )
    op.drop_index("ix_client_episodes_client_id", table_name="client_episodes")
    op.drop_table("client_episodes")

    op.drop_index("ix_client_facts_client_category", table_name="client_facts")
    op.drop_index("ix_client_facts_client_id", table_name="client_facts")
    op.drop_table("client_facts")

    op.drop_index("ix_client_state_client_id", table_name="client_state")
    op.drop_table("client_state")

