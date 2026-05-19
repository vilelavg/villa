-- ═══════════════════════════════════════════════════════════════
-- VILLA — Migration 001: Schema Inicial
-- Cria todas as tabelas, extensões e índices
-- Executado automaticamente no primeiro start do PostgreSQL
-- ═══════════════════════════════════════════════════════════════

-- Extensões
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- ── Enums ──
CREATE TYPE user_role AS ENUM ('admin', 'operator', 'sdr', 'readonly');
CREATE TYPE client_status AS ENUM ('active', 'onboarding', 'paused', 'churned');
CREATE TYPE lead_status AS ENUM ('new', 'contacted', 'qualifying', 'qualified', 'scheduled', 'proposal', 'won', 'lost', 'disqualified');
CREATE TYPE roteiro_status AS ENUM ('draft', 'validating', 'approved', 'rejected', 'published');
CREATE TYPE action_risk AS ENUM ('low', 'medium', 'high');
CREATE TYPE module_code AS ENUM (
    'm01_roteiros', 'm02_relatorios', 'm03_qualificacao', 'm04_campanhas',
    'm05_agendamento', 'm06_atendimento', 'm07_retroalimentacao', 'm08_onboarding',
    'm09_arquivos', 'm10_smooth', 'm11_hipoteses', 'm12_alertas', 'm13_conhecimento'
);

-- ═══════════════════════════════════════════════════════════════
-- TABELAS
-- ═══════════════════════════════════════════════════════════════

-- ── Usuários ──
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(100) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role user_role NOT NULL DEFAULT 'readonly',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ── Clientes WebXP ──
CREATE TABLE clients (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(200) NOT NULL,
    slug VARCHAR(100) UNIQUE NOT NULL,
    status client_status DEFAULT 'active',

    -- Dados do cliente
    specialty VARCHAR(200),
    client_type VARCHAR(50),
    contact_name VARCHAR(200),
    contact_phone VARCHAR(20),
    contact_email VARCHAR(255),

    -- IDs externos
    kommo_pipeline_id INTEGER,
    meta_ad_account_id VARCHAR(50),
    google_ads_id VARCHAR(50),
    inlead_form_id VARCHAR(100),
    whatsapp_number VARCHAR(20),

    -- Configurações
    config JSONB DEFAULT '{}',
    inlead_field_mapping JSONB DEFAULT '{}',

    -- Contrato
    contract_value FLOAT,
    contract_start DATE,

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ── Leads ──
CREATE TABLE leads (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id UUID NOT NULL REFERENCES clients(id),
    status lead_status DEFAULT 'new',

    name VARCHAR(200),
    phone VARCHAR(20),
    email VARCHAR(255),

    source VARCHAR(50),
    utm_source VARCHAR(100),
    utm_medium VARCHAR(100),
    utm_campaign VARCHAR(200),
    utm_content VARCHAR(200),
    fbclid VARCHAR(255),
    gclid VARCHAR(255),

    qualification_score FLOAT,
    qualification_notes TEXT,
    qualified_by VARCHAR(50),
    disqualification_reason TEXT,

    kommo_lead_id INTEGER,
    inlead_submission_id VARCHAR(100),
    raw_data JSONB DEFAULT '{}',

    deal_value FLOAT,

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    converted_at TIMESTAMP
);

CREATE INDEX ix_leads_client_status ON leads(client_id, status);
CREATE INDEX ix_leads_created ON leads(created_at);

-- ── Conversas ──
CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lead_id UUID NOT NULL REFERENCES leads(id),
    module module_code NOT NULL,

    messages JSONB DEFAULT '[]',
    summary TEXT,

    is_active BOOLEAN DEFAULT TRUE,
    transferred_to_human BOOLEAN DEFAULT FALSE,
    transfer_reason TEXT,

    started_at TIMESTAMP DEFAULT NOW(),
    ended_at TIMESTAMP
);

-- ── Roteiros ──
CREATE TABLE roteiros (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id UUID NOT NULL REFERENCES clients(id),
    status roteiro_status DEFAULT 'draft',

    title VARCHAR(300) NOT NULL,
    hook TEXT NOT NULL,
    body TEXT NOT NULL,
    cta TEXT NOT NULL,
    full_script TEXT NOT NULL,

    hook_score FLOAT,
    hook_feedback TEXT,
    body_score FLOAT,
    body_feedback TEXT,
    cta_score FLOAT,
    cta_feedback TEXT,
    overall_score FLOAT,

    hook_variations JSONB,
    briefing JSONB,
    generation_params JSONB,

    human_approved BOOLEAN,
    human_feedback TEXT,
    performance_data JSONB,

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ── Campanhas ──
CREATE TABLE campaigns (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id UUID NOT NULL REFERENCES clients(id),

    platform VARCHAR(20) NOT NULL,
    external_id VARCHAR(100) NOT NULL,
    name VARCHAR(300) NOT NULL,
    status VARCHAR(50) DEFAULT 'active',

    metrics JSONB DEFAULT '{}',
    daily_metrics JSONB DEFAULT '[]',

    villa_analysis TEXT,
    villa_recommendations JSONB,
    health_score FLOAT,

    last_synced_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),

    UNIQUE(platform, external_id)
);

CREATE INDEX ix_campaigns_client ON campaigns(client_id);

-- ── Relatórios ──
CREATE TABLE reports (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id UUID NOT NULL REFERENCES clients(id),

    report_type VARCHAR(20) NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,

    data JSONB NOT NULL,
    analysis TEXT,
    summary_whatsapp TEXT,
    summary_pdf_url VARCHAR(500),

    sent BOOLEAN DEFAULT FALSE,
    sent_at TIMESTAMP,
    sent_via VARCHAR(20),

    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX ix_reports_client_type_period ON reports(client_id, report_type, period_start);

-- ── Agendamentos ──
CREATE TABLE appointments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lead_id UUID NOT NULL REFERENCES leads(id),
    client_id UUID NOT NULL REFERENCES clients(id),

    scheduled_at TIMESTAMP NOT NULL,
    duration_minutes INTEGER DEFAULT 30,
    google_event_id VARCHAR(200),

    status VARCHAR(20) DEFAULT 'scheduled',
    confirmed_at TIMESTAMP,
    reminder_sent BOOLEAN DEFAULT FALSE,
    capi_event_sent BOOLEAN DEFAULT FALSE,

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ── Decision Log (feedback loop) ──
CREATE TABLE decision_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    module module_code NOT NULL,
    client_id UUID REFERENCES clients(id),

    action VARCHAR(200) NOT NULL,
    input_data JSONB,
    output_data JSONB,
    reasoning TEXT,

    outcome VARCHAR(50),
    outcome_details JSONB,
    human_feedback TEXT,

    tokens_input INTEGER,
    tokens_output INTEGER,
    model_used VARCHAR(50),
    cost_usd FLOAT,

    created_at TIMESTAMP DEFAULT NOW(),
    evaluated_at TIMESTAMP
);

CREATE INDEX ix_decision_module_action ON decision_logs(module, action);
CREATE INDEX ix_decision_client ON decision_logs(client_id);
CREATE INDEX ix_decision_created ON decision_logs(created_at);

-- ── Audit Log ──
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id),
    module module_code,

    action VARCHAR(200) NOT NULL,
    risk_level action_risk DEFAULT 'low',
    resource_type VARCHAR(50),
    resource_id VARCHAR(100),

    details JSONB,
    ip_address VARCHAR(45),

    success BOOLEAN DEFAULT TRUE,
    error_message TEXT,

    required_confirmation BOOLEAN DEFAULT FALSE,
    confirmed_by UUID,
    confirmed_at TIMESTAMP,

    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX ix_audit_created ON audit_logs(created_at);
CREATE INDEX ix_audit_module_action ON audit_logs(module, action);

-- ── Knowledge Base ──
CREATE TABLE knowledge_documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id UUID REFERENCES clients(id),

    title VARCHAR(500) NOT NULL,
    doc_type VARCHAR(50) NOT NULL,
    source VARCHAR(200),
    source_url VARCHAR(500),

    content TEXT NOT NULL,
    chunks JSONB,

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE knowledge_embeddings (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    document_id UUID NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector(1536),

    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX ix_embedding_document ON knowledge_embeddings(document_id);

-- ── Module Configs ──
CREATE TABLE module_configs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    module module_code UNIQUE NOT NULL,

    is_active BOOLEAN DEFAULT FALSE,

    config JSONB DEFAULT '{}',
    system_prompt TEXT,
    training_data JSONB,

    last_executed_at TIMESTAMP,
    execution_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- ── Alertas ──
CREATE TABLE alerts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    client_id UUID REFERENCES clients(id),
    module module_code NOT NULL,

    alert_type VARCHAR(100) NOT NULL,
    severity VARCHAR(20) NOT NULL,
    title VARCHAR(300) NOT NULL,
    message TEXT NOT NULL,
    suggested_action TEXT,

    metric_name VARCHAR(50),
    metric_value FLOAT,
    threshold_value FLOAT,

    acknowledged BOOLEAN DEFAULT FALSE,
    acknowledged_by UUID,
    acknowledged_at TIMESTAMP,
    resolved BOOLEAN DEFAULT FALSE,

    sent_whatsapp BOOLEAN DEFAULT FALSE,

    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX ix_alerts_client_type ON alerts(client_id, alert_type);
CREATE INDEX ix_alerts_created ON alerts(created_at);

-- ═══════════════════════════════════════════════════════════════
-- SEED: Configuração inicial dos módulos
-- ═══════════════════════════════════════════════════════════════

INSERT INTO module_configs (module, is_active, config) VALUES
    ('m01_roteiros', FALSE, '{"min_hook_score": 7.0, "min_body_score": 7.0, "min_cta_score": 7.0}'),
    ('m02_relatorios', FALSE, '{"daily_enabled": true, "weekly_enabled": true, "monthly_enabled": true}'),
    ('m03_qualificacao', FALSE, '{"min_score_to_qualify": 60, "max_messages_before_handoff": 10}'),
    ('m04_campanhas', FALSE, '{"sync_interval_hours": 6, "cpl_alert_threshold": 1.5}'),
    ('m05_agendamento', FALSE, '{"min_hours_ahead": 2, "max_slots_per_day": 8, "reminder_hours_before": 24}'),
    ('m06_atendimento', FALSE, '{"max_response_time_seconds": 30, "handoff_keywords": ["falar com humano", "atendente"]}'),
    ('m07_retroalimentacao', FALSE, '{"analysis_frequency": "weekly"}'),
    ('m08_onboarding', FALSE, '{"checklist_steps": 10}'),
    ('m09_arquivos', FALSE, '{"drive_root_folder": ""}'),
    ('m10_smooth', FALSE, '{"integration_enabled": false}'),
    ('m11_hipoteses', FALSE, '{"min_data_points": 5, "variations_per_creative": 3}'),
    ('m12_alertas', FALSE, '{"check_interval_minutes": 30}'),
    ('m13_conhecimento', FALSE, '{"embedding_model": "text-embedding-3-small", "chunk_size": 500, "chunk_overlap": 50}');

-- ═══════════════════════════════════════════════════════════════
-- DONE
-- ═══════════════════════════════════════════════════════════════
