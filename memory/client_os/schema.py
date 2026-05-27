"""
Schema SQLAlchemy do Client OS.

Define as 6 tabelas que compõem o estado narrativo vivo por cliente:

- client_state        : singleton por cliente (versão + summary compilado)
- client_facts        : fatos estáveis (perfil, budget, contexto)
- client_episodes     : eventos temporais (o que aconteceu)
- client_preferences  : padrões observados (como o cliente reage)
- client_pending      : open loops (coisas esperando alguém)
- client_objectives   : metas ativas (base do Proactive Agent)

A FK ondelete=CASCADE garante que ao deletar um cliente todo o estado
narrativo dele é removido junto.

Tipo de `client_id`: UUID (consistente com `clients.id` no schema base
do Villa — ver db/migrations/001_initial_schema.sql).
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

try:
    # Reaproveita o Base do projeto Villa (definido em core/database.py)
    from core.database import Base  # type: ignore
except ImportError:  # pragma: no cover
    # Fallback: permite que o módulo seja importado em isolamento (tests, tooling)
    from sqlalchemy.orm import DeclarativeBase

    class Base(DeclarativeBase):  # type: ignore[no-redef]
        pass


# ---------- client_state (singleton por cliente) ----------

class ClientStateRow(Base):
    """Linha singleton por cliente. Mantém versão e summary narrativo compilado."""

    __tablename__ = "client_state"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary_updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


# ---------- client_facts ----------

class ClientFact(Base):
    """
    Fatos estáveis sobre o cliente.

    Exemplos:
        category="owner_profile" key="risk_tolerance" value="conservative"
        category="budget" key="monthly" value={"amount": 5000, "currency": "BRL"}
        category="specialty_focus" key="primary" value="implantes"
    """

    __tablename__ = "client_facts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    # JSONB aceita qualquer JSON: string, number, bool, dict, list, null
    value: Mapped[Any] = mapped_column(JSONB, nullable=False, default=dict)
    source: Mapped[str] = mapped_column(
        String(64), nullable=False, default="manual", server_default="manual"
    )
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0, server_default="1.0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "client_id", "category", "key", name="uq_client_facts_client_cat_key"
        ),
        Index("ix_client_facts_client_category", "client_id", "category"),
    )


# ---------- client_episodes ----------

class ClientEpisode(Base):
    """
    Eventos temporais — o que aconteceu com o cliente, quando e o resultado.

    Exemplos de episode_type (snake_case curto):
        campaign_launched, campaign_paused, lead_converted, lead_lost,
        creative_approved, report_sent, anomaly_detected, objective_set
    """

    __tablename__ = "client_episodes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    episode_type: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # outcome ∈ {"positive", "negative", "neutral", "pending"}
    outcome: Mapped[str] = mapped_column(
        String(32), nullable=False, default="neutral", server_default="neutral"
    )
    module_source: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # linked_refs: IDs de entidades relacionadas (campaign_id, lead_id, etc)
    # Não usei "references" porque é nome ambíguo com SQL.
    linked_refs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "ix_client_episodes_client_type_occurred",
            "client_id",
            "episode_type",
            "occurred_at",
        ),
        Index(
            "ix_client_episodes_client_occurred",
            "client_id",
            "occurred_at",
        ),
    )


# ---------- client_preferences ----------

class ClientPreference(Base):
    """
    Padrões observados sobre como o cliente se comporta / prefere coisas.

    Exemplos:
        topic="approvals" pattern="aprova criativos só após ver 3 opções"
        topic="copy_style" pattern="responde melhor a copy emocional que racional"
        topic="report_format" pattern="prefere relatórios curtos com bullet points"
    """

    __tablename__ = "client_preferences"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    topic: Mapped[str] = mapped_column(String(64), nullable=False)
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5, server_default="0.5"
    )
    last_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "client_id", "topic", "pattern", name="uq_client_pref_client_topic_pattern"
        ),
        Index("ix_client_preferences_client_topic", "client_id", "topic"),
    )


# ---------- client_pending (open loops) ----------

class ClientPendingItem(Base):
    """
    Open loops — coisas pendentes esperando alguém.

    owner: quem precisa agir (villa | caio | thais | client | <nome>)
    status: open | resolved | abandoned
    """

    __tablename__ = "client_pending"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    owner: Mapped[str] = mapped_column(
        String(64), nullable=False, default="villa", server_default="villa"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="open", server_default="open"
    )
    module_source: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    due_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index(
            "ix_client_pending_client_status_due",
            "client_id",
            "status",
            "due_at",
        ),
    )


# ---------- client_objectives ----------

class ClientObjective(Base):
    """
    Metas ativas por cliente. Base do Proactive Agent (Fase 1).

    Exemplo:
        title="Reduzir CPL em 20%"
        target_metric="cpl" target_value=40.0
        deadline="2026-07-31"
        progress={"current": 50.0, "trend": "improving"}
    """

    __tablename__ = "client_objectives"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    target_metric: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    target_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    deadline: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    progress: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # status ∈ {"active", "paused", "achieved", "abandoned"}
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default="active"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index("ix_client_objectives_client_status", "client_id", "status"),
    )


__all__ = [
    "ClientStateRow",
    "ClientFact",
    "ClientEpisode",
    "ClientPreference",
    "ClientPendingItem",
    "ClientObjective",
]
