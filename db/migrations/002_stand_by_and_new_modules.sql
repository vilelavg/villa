-- ══════════════════════════════════════════════════════════════════
-- Migration 002 — Stand-by + Módulos M14 e M15
-- Data: 19/05/2026
-- Contexto: Decisões da reunião com Caio e Thaís (WebXP)
-- ══════════════════════════════════════════════════════════════════

BEGIN;

-- ──────────────────────────────────────────────────────────────────
-- 1. Atualizar ModuleCode enum para incluir M14 e M15
-- ──────────────────────────────────────────────────────────────────
-- PostgreSQL requer DROP e recriar o tipo enum com novos valores
-- Usando ALTER TYPE que funciona no PG 9.1+

ALTER TYPE modulecode ADD VALUE IF NOT EXISTS 'm14_suporte_mari';
ALTER TYPE modulecode ADD VALUE IF NOT EXISTS 'm15_monitor_smooth';


-- ──────────────────────────────────────────────────────────────────
-- 2. Tabelas para M14 — Suporte Mari
-- ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sdr_conversations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID REFERENCES clients(id) ON DELETE SET NULL,

    -- Identificação
    lead_name       VARCHAR(200),
    course_name     VARCHAR(300),
    lead_source     VARCHAR(100) DEFAULT 'whatsapp',

    -- Conteúdo
    messages        JSONB DEFAULT '[]'::jsonb,
    raw_text        TEXT,

    -- Resultado
    outcome         VARCHAR(50) DEFAULT 'pending',    -- won | lost | pending | no_show
    main_objection  VARCHAR(500),

    -- Análise do Villa
    objections_extracted    JSONB,
    patterns_extracted      JSONB,
    analyzed_at             TIMESTAMP,

    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_sdr_conv_client   ON sdr_conversations (client_id);
CREATE INDEX IF NOT EXISTS ix_sdr_conv_course   ON sdr_conversations (course_name);
CREATE INDEX IF NOT EXISTS ix_sdr_conv_outcome  ON sdr_conversations (outcome);
CREATE INDEX IF NOT EXISTS ix_sdr_conv_created  ON sdr_conversations (created_at);


CREATE TABLE IF NOT EXISTS sdr_objections (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID REFERENCES clients(id) ON DELETE SET NULL,

    -- Classificação
    course_name     VARCHAR(300),
    category        VARCHAR(100) NOT NULL,              -- preco | tempo | credibilidade | tecnica | urgencia | outro
    objection_text  TEXT NOT NULL,

    -- Variações
    variations      JSONB,

    -- Respostas
    best_responses          JSONB,
    response_in_progress    TEXT,

    -- Estatísticas
    frequency                       INTEGER DEFAULT 1,
    won_with_this_objection         INTEGER DEFAULT 0,
    lost_with_this_objection        INTEGER DEFAULT 0,

    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_sdr_obj_client_cat    ON sdr_objections (client_id, category);
CREATE INDEX IF NOT EXISTS ix_sdr_obj_course        ON sdr_objections (course_name);
CREATE INDEX IF NOT EXISTS ix_sdr_obj_frequency     ON sdr_objections (frequency DESC);


-- ──────────────────────────────────────────────────────────────────
-- 3. Tabelas para M15 — Monitor Smooth
-- ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS smooth_messages (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Origem
    member_phone        VARCHAR(30),
    member_name         VARCHAR(200),
    group_name          VARCHAR(200) DEFAULT 'Smooth Dentistry',
    message_timestamp   TIMESTAMP,

    -- Conteúdo
    content             TEXT NOT NULL,
    media_type          VARCHAR(30) DEFAULT 'text',     -- text | audio | image | video
    is_reply            BOOLEAN DEFAULT FALSE,
    reply_to_id         UUID,

    -- Classificação
    category            VARCHAR(100),   -- dor | duvida | elogio | networking | conteudo | admin | outro
    sentiment           VARCHAR(20),    -- positive | negative | neutral
    topics              JSONB,
    pain_points         JSONB,
    analyzed            BOOLEAN DEFAULT FALSE,

    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_smooth_msg_member     ON smooth_messages (member_phone);
CREATE INDEX IF NOT EXISTS ix_smooth_msg_timestamp  ON smooth_messages (message_timestamp DESC);
CREATE INDEX IF NOT EXISTS ix_smooth_msg_category   ON smooth_messages (category);
CREATE INDEX IF NOT EXISTS ix_smooth_msg_analyzed   ON smooth_messages (analyzed);


CREATE TABLE IF NOT EXISTS smooth_members (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    phone               VARCHAR(30) UNIQUE,
    name                VARCHAR(200),
    inferred_specialty  VARCHAR(200),

    -- Atividade
    message_count       INTEGER DEFAULT 0,
    first_message_at    TIMESTAMP,
    last_message_at     TIMESTAMP,
    engagement_score    FLOAT DEFAULT 0.0,

    -- Perfil inferido
    main_topics         JSONB,
    main_pain_points    JSONB,
    content_preferences JSONB,

    -- Flags
    is_high_value       BOOLEAN DEFAULT FALSE,
    campaign_eligible   BOOLEAN DEFAULT TRUE,

    updated_at  TIMESTAMP DEFAULT NOW(),
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_smooth_member_engagement  ON smooth_members (engagement_score DESC);
CREATE INDEX IF NOT EXISTS ix_smooth_member_high_value  ON smooth_members (is_high_value);


CREATE TABLE IF NOT EXISTS smooth_insights (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Período
    period_start        TIMESTAMP,
    period_end          TIMESTAMP,
    messages_analyzed   INTEGER DEFAULT 0,

    -- Resultado
    insight_type        VARCHAR(100) NOT NULL,   -- weekly_summary | pain_trends | member_activity
    title               VARCHAR(500) NOT NULL,
    summary             TEXT NOT NULL,
    data                JSONB,

    -- Top dados
    top_topics          JSONB,
    top_pain_points     JSONB,
    top_members         JSONB,

    -- Campanhas
    campaign_recommendations    JSONB,

    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_smooth_insight_type       ON smooth_insights (insight_type);
CREATE INDEX IF NOT EXISTS ix_smooth_insight_created    ON smooth_insights (created_at DESC);


-- ──────────────────────────────────────────────────────────────────
-- 4. Configurações dos módulos no module_configs
-- ──────────────────────────────────────────────────────────────────

-- M03 — STAND_BY (WhatsApp lead qualification paused)
INSERT INTO module_configs (module, is_active, config)
VALUES ('m03_qualificacao', FALSE, '{
    "stand_by": true,
    "stand_by_reason": "WhatsApp outbound paused — decisão reunião Caio+Thaís 19/05/2026. Risco de ban na BM com 45-50 contas.",
    "stand_by_date": "2026-05-19",
    "reactivation_condition": "Quando confirmado que uso de templates aprovados é seguro para a BM da Thaís."
}'::jsonb)
ON CONFLICT (module) DO UPDATE
SET is_active = FALSE,
    config = module_configs.config || '{
        "stand_by": true,
        "stand_by_reason": "WhatsApp outbound paused — decisão reunião Caio+Thaís 19/05/2026.",
        "stand_by_date": "2026-05-19"
    }'::jsonb,
    updated_at = NOW();


-- M05 — STAND_BY (depende de WhatsApp para envio)
INSERT INTO module_configs (module, is_active, config)
VALUES ('m05_agendamento', FALSE, '{
    "stand_by": true,
    "stand_by_reason": "Envio de horários via WhatsApp pausado — aguarda liberação do canal.",
    "stand_by_date": "2026-05-19",
    "google_calendar_integration": "active"
}'::jsonb)
ON CONFLICT (module) DO UPDATE
SET is_active = FALSE,
    config = module_configs.config || '{
        "stand_by": true,
        "stand_by_reason": "WhatsApp paused",
        "stand_by_date": "2026-05-19"
    }'::jsonb,
    updated_at = NOW();


-- M06 — STAND_BY (atendimento manual pela Mari)
INSERT INTO module_configs (module, is_active, config)
VALUES ('m06_atendimento', FALSE, '{
    "stand_by": true,
    "stand_by_reason": "Atendimento manual pela Mari. Villa aprende via M14. Reativar para clínicas, não WebXP.",
    "stand_by_date": "2026-05-19"
}'::jsonb)
ON CONFLICT (module) DO UPDATE
SET is_active = FALSE,
    config = module_configs.config || '{
        "stand_by": true,
        "stand_by_reason": "Atendimento manual pela Mari — decisão reunião 19/05/2026",
        "stand_by_date": "2026-05-19"
    }'::jsonb,
    updated_at = NOW();


-- M14 — Suporte Mari (ATIVO — prioridade imediata)
INSERT INTO module_configs (module, is_active, config)
VALUES ('m14_suporte_mari', TRUE, '{
    "priority": 2,
    "description": "SDR assistant para a Mari. Sugestões em tempo real + banco de objeções.",
    "modes": ["suggest", "ingest", "analyze", "report"],
    "scheduler_analyze": "weekly",
    "scheduler_report": "weekly",
    "added_date": "2026-05-19"
}'::jsonb)
ON CONFLICT (module) DO UPDATE
SET is_active = TRUE,
    config = EXCLUDED.config,
    updated_at = NOW();


-- M15 — Monitor Smooth (STAND_BY aguardando webhook)
INSERT INTO module_configs (module, is_active, config)
VALUES ('m15_monitor_smooth', FALSE, '{
    "stand_by": true,
    "stand_by_reason": "Aguardando: (1) configuração do webhook N8N, (2) aprovação governança Smooth (6 sócios), (3) setup Evolution API.",
    "stand_by_date": "2026-05-19",
    "priority": 3,
    "description": "Monitor silencioso da comunidade Smooth — nunca envia mensagens.",
    "webhook_event": "smooth_group_message",
    "reactivation_condition": "Webhook configurado + aprovação dos 6 sócios Smooth."
}'::jsonb)
ON CONFLICT (module) DO UPDATE
SET is_active = FALSE,
    config = EXCLUDED.config,
    updated_at = NOW();


-- ──────────────────────────────────────────────────────────────────
-- 5. Função utilitária: updated_at automático
-- ──────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER IF NOT EXISTS update_sdr_conversations_updated_at
    BEFORE UPDATE ON sdr_conversations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER IF NOT EXISTS update_sdr_objections_updated_at
    BEFORE UPDATE ON sdr_objections
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER IF NOT EXISTS update_smooth_members_updated_at
    BEFORE UPDATE ON smooth_members
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();


COMMIT;

-- ══════════════════════════════════════════════════════════════════
-- Rollback (se necessário):
-- ══════════════════════════════════════════════════════════════════
-- DROP TABLE IF EXISTS smooth_insights, smooth_members, smooth_messages, sdr_objections, sdr_conversations;
-- UPDATE module_configs SET is_active = FALSE WHERE module IN ('m14_suporte_mari', 'm15_monitor_smooth');
-- ══════════════════════════════════════════════════════════════════
