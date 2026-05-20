-- ══════════════════════════════════════════════════════════════════
-- Migration 002 — Stand-by + Módulos M14 e M15
-- Data: 19/05/2026
-- CORRIGIDO: ALTER TYPE fora de transaction, triggers sem IF NOT EXISTS
-- ══════════════════════════════════════════════════════════════════

-- ALTER TYPE ADD VALUE não pode rodar dentro de BEGIN/COMMIT
-- Precisa rodar fora de qualquer bloco de transação
ALTER TYPE modulecode ADD VALUE IF NOT EXISTS 'm14_suporte_mari';
ALTER TYPE modulecode ADD VALUE IF NOT EXISTS 'm15_monitor_smooth';

-- Agora sim abrimos a transação
BEGIN;

-- ──────────────────────────────────────────────────────────────────
-- Tabelas para M14 — Suporte Mari
-- ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sdr_conversations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID REFERENCES clients(id) ON DELETE SET NULL,
    lead_name       VARCHAR(200),
    course_name     VARCHAR(300),
    lead_source     VARCHAR(100) DEFAULT 'whatsapp',
    messages        JSONB DEFAULT '[]'::jsonb,
    raw_text        TEXT,
    outcome         VARCHAR(50) DEFAULT 'pending',
    main_objection  VARCHAR(500),
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
    course_name     VARCHAR(300),
    category        VARCHAR(100) NOT NULL,
    objection_text  TEXT NOT NULL,
    variations      JSONB,
    best_responses          JSONB,
    response_in_progress    TEXT,
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
-- Tabelas para M15 — Monitor Smooth
-- ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS smooth_messages (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    member_phone        VARCHAR(30),
    member_name         VARCHAR(200),
    group_name          VARCHAR(200) DEFAULT 'Smooth Dentistry',
    message_timestamp   TIMESTAMP,
    content             TEXT NOT NULL,
    media_type          VARCHAR(30) DEFAULT 'text',
    is_reply            BOOLEAN DEFAULT FALSE,
    reply_to_id         UUID,
    category            VARCHAR(100),
    sentiment           VARCHAR(20),
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
    message_count       INTEGER DEFAULT 0,
    first_message_at    TIMESTAMP,
    last_message_at     TIMESTAMP,
    engagement_score    FLOAT DEFAULT 0.0,
    main_topics         JSONB,
    main_pain_points    JSONB,
    content_preferences JSONB,
    is_high_value       BOOLEAN DEFAULT FALSE,
    campaign_eligible   BOOLEAN DEFAULT TRUE,
    updated_at  TIMESTAMP DEFAULT NOW(),
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_smooth_member_engagement  ON smooth_members (engagement_score DESC);
CREATE INDEX IF NOT EXISTS ix_smooth_member_high_value  ON smooth_members (is_high_value);


CREATE TABLE IF NOT EXISTS smooth_insights (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    period_start        TIMESTAMP,
    period_end          TIMESTAMP,
    messages_analyzed   INTEGER DEFAULT 0,
    insight_type        VARCHAR(100) NOT NULL,
    title               VARCHAR(500) NOT NULL,
    summary             TEXT NOT NULL,
    data                JSONB,
    top_topics          JSONB,
    top_pain_points     JSONB,
    top_members         JSONB,
    campaign_recommendations    JSONB,
    created_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_smooth_insight_type       ON smooth_insights (insight_type);
CREATE INDEX IF NOT EXISTS ix_smooth_insight_created    ON smooth_insights (created_at DESC);


-- ──────────────────────────────────────────────────────────────────
-- Configurações dos módulos
-- ──────────────────────────────────────────────────────────────────

INSERT INTO module_configs (module, is_active, config)
VALUES ('m03_qualificacao', FALSE, '{
    "stand_by": true,
    "stand_by_reason": "WhatsApp outbound paused — decisão reunião Caio+Thaís 19/05/2026.",
    "stand_by_date": "2026-05-19"
}'::jsonb)
ON CONFLICT (module) DO UPDATE
SET is_active = FALSE,
    config = module_configs.config || '{"stand_by": true, "stand_by_date": "2026-05-19"}'::jsonb,
    updated_at = NOW();

INSERT INTO module_configs (module, is_active, config)
VALUES ('m05_agendamento', FALSE, '{
    "stand_by": true,
    "stand_by_reason": "Envio WhatsApp pausado.",
    "stand_by_date": "2026-05-19"
}'::jsonb)
ON CONFLICT (module) DO UPDATE
SET is_active = FALSE,
    config = module_configs.config || '{"stand_by": true, "stand_by_date": "2026-05-19"}'::jsonb,
    updated_at = NOW();

INSERT INTO module_configs (module, is_active, config)
VALUES ('m06_atendimento', FALSE, '{
    "stand_by": true,
    "stand_by_reason": "Atendimento manual pela Mari. Villa aprende via M14.",
    "stand_by_date": "2026-05-19"
}'::jsonb)
ON CONFLICT (module) DO UPDATE
SET is_active = FALSE,
    config = module_configs.config || '{"stand_by": true, "stand_by_date": "2026-05-19"}'::jsonb,
    updated_at = NOW();

INSERT INTO module_configs (module, is_active, config)
VALUES ('m14_suporte_mari', TRUE, '{
    "priority": 2,
    "description": "SDR assistant para a Mari.",
    "added_date": "2026-05-19"
}'::jsonb)
ON CONFLICT (module) DO UPDATE
SET is_active = TRUE,
    config = EXCLUDED.config,
    updated_at = NOW();

INSERT INTO module_configs (module, is_active, config)
VALUES ('m15_monitor_smooth', FALSE, '{
    "stand_by": true,
    "stand_by_reason": "Aguardando webhook N8N + aprovação governança Smooth.",
    "stand_by_date": "2026-05-19"
}'::jsonb)
ON CONFLICT (module) DO UPDATE
SET is_active = FALSE,
    config = EXCLUDED.config,
    updated_at = NOW();


-- ──────────────────────────────────────────────────────────────────
-- Triggers de updated_at
-- ──────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

DROP TRIGGER IF EXISTS update_sdr_conversations_updated_at ON sdr_conversations;
CREATE TRIGGER update_sdr_conversations_updated_at
    BEFORE UPDATE ON sdr_conversations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_sdr_objections_updated_at ON sdr_objections;
CREATE TRIGGER update_sdr_objections_updated_at
    BEFORE UPDATE ON sdr_objections
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_smooth_members_updated_at ON smooth_members;
CREATE TRIGGER update_smooth_members_updated_at
    BEFORE UPDATE ON smooth_members
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

COMMIT;
