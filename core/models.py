"""
Villa — Modelos do Banco de Dados
SQLAlchemy models + Pydantic schemas para validação.
Cada tabela representa uma entidade central do sistema.
"""

from datetime import datetime, date
from enum import Enum as PyEnum
from typing import Optional
from uuid import uuid4

from sqlalchemy import (
    Column, String, Text, Integer, Float, Boolean, DateTime, Date,
    ForeignKey, Enum, JSON, Index, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import relationship, Mapped, mapped_column
from pgvector.sqlalchemy import Vector
from pydantic import BaseModel, Field

from core.database import Base


# ═══════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════

class UserRole(str, PyEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    SDR = "sdr"
    READONLY = "readonly"


class ClientStatus(str, PyEnum):
    ACTIVE = "active"
    ONBOARDING = "onboarding"
    PAUSED = "paused"
    CHURNED = "churned"


class LeadStatus(str, PyEnum):
    NEW = "new"
    CONTACTED = "contacted"
    QUALIFYING = "qualifying"
    QUALIFIED = "qualified"
    SCHEDULED = "scheduled"
    PROPOSAL = "proposal"
    WON = "won"
    LOST = "lost"
    DISQUALIFIED = "disqualified"


class RoteiroStatus(str, PyEnum):
    DRAFT = "draft"
    VALIDATING = "validating"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"


class ActionRisk(str, PyEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ModuleCode(str, PyEnum):
    M01_ROTEIROS = "m01_roteiros"
    M02_RELATORIOS = "m02_relatorios"
    M03_QUALIFICACAO = "m03_qualificacao"
    M04_CAMPANHAS = "m04_campanhas"
    M05_AGENDAMENTO = "m05_agendamento"
    M06_ATENDIMENTO = "m06_atendimento"
    M07_RETROALIMENTACAO = "m07_retroalimentacao"
    M08_ONBOARDING = "m08_onboarding"
    M09_ARQUIVOS = "m09_arquivos"
    M10_SMOOTH = "m10_smooth"
    M11_HIPOTESES = "m11_hipoteses"
    M12_ALERTAS = "m12_alertas"
    M13_CONHECIMENTO = "m13_conhecimento"
    # ── Adicionados pós-reunião Caio+Thaís (19/05/2026) ──
    M14_SUPORTE_MARI = "m14_suporte_mari"   # SDR assistant: monitoramento + sugestões em tempo real
    M15_MONITOR_SMOOTH = "m15_monitor_smooth"  # Inteligência da comunidade Smooth (modo silencioso)


# ═══════════════════════════════════════════════════════════════
# MODELOS SQLALCHEMY
# ═══════════════════════════════════════════════════════════════

class User(Base):
    """Usuários do sistema Villa (Caio, Thaís, Ana Lívia, Mariana, Jasmyne)."""
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(Enum(UserRole), nullable=False, default=UserRole.READONLY)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    audit_logs = relationship("AuditLog", back_populates="user")


class Client(Base):
    """Clientes da WebXP (dentistas, clínicas, professores). Atualmente 17."""
    __tablename__ = "clients"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(Enum(ClientStatus), default=ClientStatus.ACTIVE)

    # Dados do cliente
    specialty: Mapped[Optional[str]] = mapped_column(String(200))        # Ex: implantes, lentes, ortodontia
    client_type: Mapped[Optional[str]] = mapped_column(String(50))       # professor | clinica | autonomo
    contact_name: Mapped[Optional[str]] = mapped_column(String(200))
    contact_phone: Mapped[Optional[str]] = mapped_column(String(20))
    contact_email: Mapped[Optional[str]] = mapped_column(String(255))

    # IDs externos
    kommo_pipeline_id: Mapped[Optional[int]] = mapped_column(Integer)
    meta_ad_account_id: Mapped[Optional[str]] = mapped_column(String(50))
    google_ads_id: Mapped[Optional[str]] = mapped_column(String(50))
    inlead_form_id: Mapped[Optional[str]] = mapped_column(String(100))
    whatsapp_number: Mapped[Optional[str]] = mapped_column(String(20))

    # Configurações específicas
    config: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict)  # Thresholds, tom de voz, etc.
    inlead_field_mapping: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict)  # Mapeamento campos InLead

    # Contrato
    contract_value: Mapped[Optional[float]] = mapped_column(Float)
    contract_start: Mapped[Optional[date]] = mapped_column(Date)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    leads = relationship("Lead", back_populates="client")
    roteiros = relationship("Roteiro", back_populates="client")
    reports = relationship("Report", back_populates="client")
    campaigns = relationship("Campaign", back_populates="client")


class Lead(Base):
    """Leads captados para os clientes da WebXP."""
    __tablename__ = "leads"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    client_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("clients.id"), nullable=False)
    status: Mapped[str] = mapped_column(Enum(LeadStatus), default=LeadStatus.NEW)

    # Dados do lead
    name: Mapped[Optional[str]] = mapped_column(String(200))
    phone: Mapped[Optional[str]] = mapped_column(String(20))
    email: Mapped[Optional[str]] = mapped_column(String(255))

    # Rastreamento
    source: Mapped[Optional[str]] = mapped_column(String(50))           # meta | google | organic | referral
    utm_source: Mapped[Optional[str]] = mapped_column(String(100))
    utm_medium: Mapped[Optional[str]] = mapped_column(String(100))
    utm_campaign: Mapped[Optional[str]] = mapped_column(String(200))
    utm_content: Mapped[Optional[str]] = mapped_column(String(200))
    fbclid: Mapped[Optional[str]] = mapped_column(String(255))
    gclid: Mapped[Optional[str]] = mapped_column(String(255))

    # Qualificação
    qualification_score: Mapped[Optional[float]] = mapped_column(Float)  # 0-100
    qualification_notes: Mapped[Optional[str]] = mapped_column(Text)
    qualified_by: Mapped[Optional[str]] = mapped_column(String(50))      # villa | human | chatbot
    disqualification_reason: Mapped[Optional[str]] = mapped_column(Text)

    # IDs externos
    kommo_lead_id: Mapped[Optional[int]] = mapped_column(Integer)
    inlead_submission_id: Mapped[Optional[str]] = mapped_column(String(100))

    # Dados brutos do InLead (campos aleatórios)
    raw_data: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict)

    # Valor (se convertido)
    deal_value: Mapped[Optional[float]] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    converted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Relationships
    client = relationship("Client", back_populates="leads")
    conversations = relationship("Conversation", back_populates="lead")

    __table_args__ = (
        Index("ix_leads_client_status", "client_id", "status"),
        Index("ix_leads_created", "created_at"),
    )


class Conversation(Base):
    """Histórico de conversas do Villa com leads via WhatsApp."""
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    lead_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("leads.id"), nullable=False)
    module: Mapped[str] = mapped_column(Enum(ModuleCode), nullable=False)

    # Mensagens (array de objetos {role, content, timestamp})
    messages: Mapped[list] = mapped_column(JSONB, default=list)
    summary: Mapped[Optional[str]] = mapped_column(Text)

    # Controle
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    transferred_to_human: Mapped[bool] = mapped_column(Boolean, default=False)
    transfer_reason: Mapped[Optional[str]] = mapped_column(Text)

    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Relationships
    lead = relationship("Lead", back_populates="conversations")


class Roteiro(Base):
    """Roteiros gerados pelo módulo M1 (gancho + corpo + CTA)."""
    __tablename__ = "roteiros"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    client_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("clients.id"), nullable=False)
    status: Mapped[str] = mapped_column(Enum(RoteiroStatus), default=RoteiroStatus.DRAFT)

    # Conteúdo
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    hook: Mapped[str] = mapped_column(Text, nullable=False)              # Gancho
    body: Mapped[str] = mapped_column(Text, nullable=False)              # Corpo
    cta: Mapped[str] = mapped_column(Text, nullable=False)               # Call to action
    full_script: Mapped[str] = mapped_column(Text, nullable=False)       # Roteiro completo

    # Validação automática (tripla)
    hook_score: Mapped[Optional[float]] = mapped_column(Float)           # 0-10
    hook_feedback: Mapped[Optional[str]] = mapped_column(Text)
    body_score: Mapped[Optional[float]] = mapped_column(Float)           # 0-10
    body_feedback: Mapped[Optional[str]] = mapped_column(Text)
    cta_score: Mapped[Optional[float]] = mapped_column(Float)            # 0-10
    cta_feedback: Mapped[Optional[str]] = mapped_column(Text)
    overall_score: Mapped[Optional[float]] = mapped_column(Float)        # Média

    # Variações
    hook_variations: Mapped[Optional[list]] = mapped_column(JSONB)       # Variações A/B de gancho

    # Contexto de geração
    briefing: Mapped[Optional[dict]] = mapped_column(JSONB)              # Briefing usado para gerar
    generation_params: Mapped[Optional[dict]] = mapped_column(JSONB)     # Modelo, temperatura, etc.

    # Feedback humano (retroalimentação)
    human_approved: Mapped[Optional[bool]] = mapped_column(Boolean)
    human_feedback: Mapped[Optional[str]] = mapped_column(Text)

    # Performance (preenchido depois via M4)
    performance_data: Mapped[Optional[dict]] = mapped_column(JSONB)      # CTR, views, engagement

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    client = relationship("Client", back_populates="roteiros")


class Campaign(Base):
    """Campanhas de anúncio dos clientes (Meta Ads / Google Ads)."""
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    client_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("clients.id"), nullable=False)

    # Identificação
    platform: Mapped[str] = mapped_column(String(20), nullable=False)    # meta | google
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="active")    # active | paused | completed

    # Métricas (atualizadas periodicamente pelo M2/M4)
    metrics: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict)
    # Estrutura: {spend, impressions, clicks, ctr, cpl, cpa, roas, leads, conversions, frequency}

    # Histórico de métricas diárias
    daily_metrics: Mapped[Optional[list]] = mapped_column(JSONB, default=list)
    # Estrutura: [{date, spend, impressions, clicks, leads, conversions}]

    # Análise do Villa (M4)
    villa_analysis: Mapped[Optional[str]] = mapped_column(Text)
    villa_recommendations: Mapped[Optional[list]] = mapped_column(JSONB)
    health_score: Mapped[Optional[float]] = mapped_column(Float)         # 0-100

    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    client = relationship("Client", back_populates="campaigns")

    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_campaign_platform_external"),
        Index("ix_campaigns_client", "client_id"),
    )


class Report(Base):
    """Relatórios gerados pelo módulo M2."""
    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    client_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("clients.id"), nullable=False)

    report_type: Mapped[str] = mapped_column(String(20), nullable=False)  # daily | weekly | monthly
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    # Conteúdo
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)            # Dados consolidados
    analysis: Mapped[Optional[str]] = mapped_column(Text)                # Análise do Villa
    summary_whatsapp: Mapped[Optional[str]] = mapped_column(Text)        # Versão curta para WhatsApp
    summary_pdf_url: Mapped[Optional[str]] = mapped_column(String(500))  # URL do PDF no Drive

    # Status de envio
    sent: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    sent_via: Mapped[Optional[str]] = mapped_column(String(20))          # whatsapp | email | drive

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relationships
    client = relationship("Client", back_populates="reports")

    __table_args__ = (
        Index("ix_reports_client_type_period", "client_id", "report_type", "period_start"),
    )


class Appointment(Base):
    """Agendamentos feitos pelo módulo M5."""
    __tablename__ = "appointments"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    lead_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("leads.id"), nullable=False)
    client_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("clients.id"), nullable=False)

    # Dados do agendamento
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30)
    google_event_id: Mapped[Optional[str]] = mapped_column(String(200))

    # Status
    status: Mapped[str] = mapped_column(String(20), default="scheduled")  # scheduled | confirmed | completed | no_show | cancelled
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    capi_event_sent: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class DecisionLog(Base):
    """
    Log de todas as decisões do Villa + resultado.
    Alimenta o feedback loop — o Villa consulta decisões passadas
    para melhorar decisões futuras.
    """
    __tablename__ = "decision_logs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    module: Mapped[str] = mapped_column(Enum(ModuleCode), nullable=False)
    client_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("clients.id"))

    # O que o Villa decidiu
    action: Mapped[str] = mapped_column(String(200), nullable=False)     # Ex: "gerar_roteiro", "qualificar_lead"
    input_data: Mapped[Optional[dict]] = mapped_column(JSONB)            # Dados de entrada
    output_data: Mapped[Optional[dict]] = mapped_column(JSONB)           # Resultado gerado
    reasoning: Mapped[Optional[str]] = mapped_column(Text)               # Por que tomou essa decisão

    # Resultado (preenchido depois)
    outcome: Mapped[Optional[str]] = mapped_column(String(50))           # success | failure | partial | pending
    outcome_details: Mapped[Optional[dict]] = mapped_column(JSONB)       # Métricas de resultado
    human_feedback: Mapped[Optional[str]] = mapped_column(Text)          # Feedback de Caio/Thaís

    # Tokens consumidos
    tokens_input: Mapped[Optional[int]] = mapped_column(Integer)
    tokens_output: Mapped[Optional[int]] = mapped_column(Integer)
    model_used: Mapped[Optional[str]] = mapped_column(String(50))
    cost_usd: Mapped[Optional[float]] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    evaluated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    __table_args__ = (
        Index("ix_decision_module_action", "module", "action"),
        Index("ix_decision_client", "client_id"),
        Index("ix_decision_created", "created_at"),
    )


class AuditLog(Base):
    """Log imutável de auditoria. Toda ação do Villa é registrada aqui."""
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"))
    module: Mapped[Optional[str]] = mapped_column(Enum(ModuleCode))

    # Ação
    action: Mapped[str] = mapped_column(String(200), nullable=False)
    risk_level: Mapped[str] = mapped_column(Enum(ActionRisk), default=ActionRisk.LOW)
    resource_type: Mapped[Optional[str]] = mapped_column(String(50))     # lead | campaign | roteiro | report
    resource_id: Mapped[Optional[str]] = mapped_column(String(100))

    # Detalhes
    details: Mapped[Optional[dict]] = mapped_column(JSONB)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))

    # Status
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    # Confirmação humana (para ações de alto risco)
    required_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_by: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False))
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    # Relationships
    user = relationship("User", back_populates="audit_logs")

    __table_args__ = (
        Index("ix_audit_created", "created_at"),
        Index("ix_audit_module_action", "module", "action"),
    )


class KnowledgeDocument(Base):
    """
    Documentos indexados na base de conhecimento (M13).
    Cada documento é vetorizado para busca semântica (RAG).
    """
    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    client_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("clients.id"))

    # Metadata
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(50), nullable=False)    # roteiro | relatorio | transcricao | briefing | faq
    source: Mapped[Optional[str]] = mapped_column(String(200))           # Ex: "tactiq", "drive", "manual"
    source_url: Mapped[Optional[str]] = mapped_column(String(500))

    # Conteúdo
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Chunks para RAG (cada chunk tem seu embedding)
    chunks: Mapped[Optional[list]] = mapped_column(JSONB)                # [{text, index}]

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class KnowledgeEmbedding(Base):
    """Embeddings vetoriais dos chunks de documentos para busca RAG."""
    __tablename__ = "knowledge_embeddings"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    document_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = Column(Vector(1536))  # Dimensão do embedding (ajustar conforme modelo)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_embedding_document", "document_id"),
    )


class ModuleConfig(Base):
    """Configuração por módulo — prompts, thresholds, flags de ativação."""
    __tablename__ = "module_configs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    module: Mapped[str] = mapped_column(Enum(ModuleCode), unique=True, nullable=False)

    # Estado
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)

    # Configurações
    config: Mapped[dict] = mapped_column(JSONB, default=dict)            # Thresholds, parâmetros específicos
    system_prompt: Mapped[Optional[str]] = mapped_column(Text)           # System prompt override
    training_data: Mapped[Optional[dict]] = mapped_column(JSONB)         # Exemplos, templates, referências

    # Controle
    last_executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    execution_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Alert(Base):
    """Alertas gerados pelo módulo M12."""
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    client_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("clients.id"))
    module: Mapped[str] = mapped_column(Enum(ModuleCode), nullable=False)

    # Alerta
    alert_type: Mapped[str] = mapped_column(String(100), nullable=False)  # cpl_high | frequency_high | show_rate_low
    severity: Mapped[str] = mapped_column(String(20), nullable=False)     # info | warning | critical
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_action: Mapped[Optional[str]] = mapped_column(Text)

    # Dados do alerta
    metric_name: Mapped[Optional[str]] = mapped_column(String(50))
    metric_value: Mapped[Optional[float]] = mapped_column(Float)
    threshold_value: Mapped[Optional[float]] = mapped_column(Float)

    # Status
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledged_by: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False))
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)

    # Envio
    sent_whatsapp: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_alerts_client_type", "client_id", "alert_type"),
        Index("ix_alerts_created", "created_at"),
    )


# ═══════════════════════════════════════════════════════════════
# M14 — SUPORTE MARI (SDR ASSISTANT)
# ═══════════════════════════════════════════════════════════════

class SDRConversation(Base):
    """
    Conversa da Mari com um lead.
    O Villa monitora, extrai padrões e sugere respostas.
    Alimentado por: importação manual, webhook ou paste direto.
    """
    __tablename__ = "sdr_conversations"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    client_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("clients.id"))

    # Identificação do lead e contexto
    lead_name: Mapped[Optional[str]] = mapped_column(String(200))
    course_name: Mapped[Optional[str]] = mapped_column(String(300))   # Curso sobre o qual a conversa é
    lead_source: Mapped[Optional[str]] = mapped_column(String(100))   # "instagram", "whatsapp", "email"

    # Conversa em formato estruturado
    messages: Mapped[list] = mapped_column(JSONB, default=list)       # [{role, content, timestamp}]
    raw_text: Mapped[Optional[str]] = mapped_column(Text)             # Texto bruto colado pela Mari

    # Resultado da conversa
    outcome: Mapped[Optional[str]] = mapped_column(String(50))        # "won" | "lost" | "pending" | "no_show"
    main_objection: Mapped[Optional[str]] = mapped_column(String(500))

    # Análise do Villa
    objections_extracted: Mapped[Optional[list]] = mapped_column(JSONB)   # Objeções identificadas
    patterns_extracted: Mapped[Optional[dict]] = mapped_column(JSONB)     # Padrões comportamentais
    analyzed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_sdr_conv_client", "client_id"),
        Index("ix_sdr_conv_course", "course_name"),
        Index("ix_sdr_conv_outcome", "outcome"),
    )


class SDRObjection(Base):
    """
    Objeção mapeada com suas melhores respostas validadas.
    Construída pelo Villa a partir das conversas da Mari.
    """
    __tablename__ = "sdr_objections"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    client_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False), ForeignKey("clients.id"))

    # Classificação
    course_name: Mapped[Optional[str]] = mapped_column(String(300))   # Curso ao qual a objeção pertence (None = geral)
    category: Mapped[str] = mapped_column(String(100), nullable=False) # "preco" | "tempo" | "credibilidade" | "tecnica" | "urgencia"
    objection_text: Mapped[str] = mapped_column(Text, nullable=False)  # Objeção canônica

    # Variações identificadas
    variations: Mapped[Optional[list]] = mapped_column(JSONB)          # Formas diferentes de dizer a mesma coisa

    # Respostas
    best_responses: Mapped[Optional[list]] = mapped_column(JSONB)      # [{text, won_rate, times_used}]
    response_in_progress: Mapped[Optional[str]] = mapped_column(Text)  # Resposta ainda sendo validada

    # Estatísticas
    frequency: Mapped[int] = mapped_column(Integer, default=1)         # Quantas vezes apareceu
    won_with_this_objection: Mapped[int] = mapped_column(Integer, default=0)
    lost_with_this_objection: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_sdr_obj_client_category", "client_id", "category"),
        Index("ix_sdr_obj_course", "course_name"),
    )


# ═══════════════════════════════════════════════════════════════
# M15 — MONITOR SMOOTH (INTELIGÊNCIA DE COMUNIDADE)
# ═══════════════════════════════════════════════════════════════

class SmoothMessage(Base):
    """
    Mensagem capturada do grupo WhatsApp da comunidade Smooth.
    O Villa lê, armazena e analisa — nunca responde.
    """
    __tablename__ = "smooth_messages"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))

    # Origem
    member_phone: Mapped[Optional[str]] = mapped_column(String(30))
    member_name: Mapped[Optional[str]] = mapped_column(String(200))
    group_name: Mapped[Optional[str]] = mapped_column(String(200), default="Smooth Dentistry")
    message_timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Conteúdo
    content: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[Optional[str]] = mapped_column(String(30))     # "text" | "audio" | "image" | "video"
    is_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    reply_to_id: Mapped[Optional[str]] = mapped_column(UUID(as_uuid=False))

    # Classificação do Villa
    category: Mapped[Optional[str]] = mapped_column(String(100))      # "dor" | "duvida" | "elogio" | "networking" | "conteudo"
    sentiment: Mapped[Optional[str]] = mapped_column(String(20))       # "positive" | "negative" | "neutral"
    topics: Mapped[Optional[list]] = mapped_column(JSONB)              # Tópicos identificados
    pain_points: Mapped[Optional[list]] = mapped_column(JSONB)         # Dores identificadas
    analyzed: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_smooth_msg_member", "member_phone"),
        Index("ix_smooth_msg_timestamp", "message_timestamp"),
        Index("ix_smooth_msg_category", "category"),
    )


class SmoothMember(Base):
    """
    Perfil de membro da comunidade Smooth construído pelo Villa.
    Atualizado a cada mensagem processada.
    """
    __tablename__ = "smooth_members"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))

    phone: Mapped[Optional[str]] = mapped_column(String(30), unique=True)
    name: Mapped[Optional[str]] = mapped_column(String(200))
    inferred_specialty: Mapped[Optional[str]] = mapped_column(String(200))  # Especialidade inferida

    # Atividade
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    first_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    engagement_score: Mapped[float] = mapped_column(Float, default=0.0)     # 0–100

    # Perfil inferido
    main_topics: Mapped[Optional[list]] = mapped_column(JSONB)              # Temas que mais fala
    main_pain_points: Mapped[Optional[list]] = mapped_column(JSONB)         # Dores mais frequentes
    content_preferences: Mapped[Optional[dict]] = mapped_column(JSONB)      # Tipo de conteúdo que mais engaja

    # Flag para ações de marketing
    is_high_value: Mapped[bool] = mapped_column(Boolean, default=False)     # Membro muito ativo
    campaign_eligible: Mapped[bool] = mapped_column(Boolean, default=True)

    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_smooth_member_engagement", "engagement_score"),
    )


class SmoothInsight(Base):
    """
    Insight consolidado gerado pelo Villa a partir das mensagens do grupo.
    Alimenta decisões de campanha e conteúdo.
    """
    __tablename__ = "smooth_insights"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))

    # Período analisado
    period_start: Mapped[Optional[datetime]] = mapped_column(DateTime)
    period_end: Mapped[Optional[datetime]] = mapped_column(DateTime)
    messages_analyzed: Mapped[int] = mapped_column(Integer, default=0)

    # Resultados
    insight_type: Mapped[str] = mapped_column(String(100), nullable=False)   # "weekly_summary" | "pain_trends" | "member_activity"
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[Optional[dict]] = mapped_column(JSONB)                      # Dados estruturados do insight

    # Top dados do período
    top_topics: Mapped[Optional[list]] = mapped_column(JSONB)
    top_pain_points: Mapped[Optional[list]] = mapped_column(JSONB)
    top_members: Mapped[Optional[list]] = mapped_column(JSONB)

    # Para uso em campanhas
    campaign_recommendations: Mapped[Optional[list]] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_smooth_insight_type", "insight_type"),
        Index("ix_smooth_insight_created", "created_at"),
    )


# ═══════════════════════════════════════════════════════════════
# SCHEMAS PYDANTIC (validação de entrada/saída da API)
# ═══════════════════════════════════════════════════════════════

class CommandRequest(BaseModel):
    """Comando enviado ao Villa via POST /command."""
    message: str = Field(..., description="Comando em linguagem natural")
    client_slug: str | None = Field(None, description="Slug do cliente (se específico)")
    module: ModuleCode | None = Field(None, description="Módulo específico (se conhecido)")
    urgent: bool = Field(False, description="Se deve priorizar processamento")


class CommandResponse(BaseModel):
    """Resposta do Villa a um comando."""
    success: bool
    message: str
    module_used: str | None = None
    data: dict | None = None
    actions_taken: list[str] = []
    tokens_used: int | None = None


class HealthResponse(BaseModel):
    """Resposta do healthcheck."""
    status: str  # healthy | degraded | unhealthy
    version: str
    environment: str
    database: bool
    redis: bool
    modules_active: int
    uptime_seconds: float


class WebhookPayload(BaseModel):
    """Payload genérico de webhook (InLead, Kommo, N8N)."""
    source: str
    event_type: str
    data: dict
    timestamp: datetime | None = None
