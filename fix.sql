INSERT INTO module_configs (module, is_active, config)
VALUES ('m14_suporte_mari', TRUE, '{"priority": 2}'::jsonb)
ON CONFLICT (module) DO UPDATE SET is_active = TRUE, updated_at = NOW();

INSERT INTO module_configs (module, is_active, config)
VALUES ('m15_monitor_smooth', FALSE, '{"stand_by": true}'::jsonb)
ON CONFLICT (module) DO UPDATE SET is_active = FALSE, updated_at = NOW();

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS t1 ON sdr_conversations;
CREATE TRIGGER t1 BEFORE UPDATE ON sdr_conversations FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS t2 ON sdr_objections;
CREATE TRIGGER t2 BEFORE UPDATE ON sdr_objections FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS t3 ON smooth_members;
CREATE TRIGGER t3 BEFORE UPDATE ON smooth_members FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();