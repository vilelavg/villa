-- ═══════════════════════════════════════════════════════════════
-- VILLA — Seed: Usuários iniciais e clientes de exemplo
-- Rodar após a migration 001
-- ═══════════════════════════════════════════════════════════════

-- ── Usuários da WebXP ──
-- Senhas são hash bcrypt de "villa2026" (trocar em produção!)
-- Gerar novos hashes: python -c "from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt']).hash('SUA_SENHA'))"

INSERT INTO users (name, email, password_hash, role, is_active) VALUES
    ('Caio Frison', 'caio@webxp.com.br', '$2b$12$LJ3m4ys5Dv5Gw2GhKxAYruV8oZf5QxGZS5LZx9G8HJ.N5vZ5XKm2C', 'admin', TRUE),
    ('Thaís Marangon', 'thais@webxp.com.br', '$2b$12$LJ3m4ys5Dv5Gw2GhKxAYruV8oZf5QxGZS5LZx9G8HJ.N5vZ5XKm2C', 'admin', TRUE),
    ('Ana Lívia', 'analivia@webxp.com.br', '$2b$12$LJ3m4ys5Dv5Gw2GhKxAYruV8oZf5QxGZS5LZx9G8HJ.N5vZ5XKm2C', 'operator', TRUE),
    ('Mariana Oliveira', 'mariana@webxp.com.br', '$2b$12$LJ3m4ys5Dv5Gw2GhKxAYruV8oZf5QxGZS5LZx9G8HJ.N5vZ5XKm2C', 'operator', TRUE),
    ('Jasmyne', 'jasmyne@webxp.com.br', '$2b$12$LJ3m4ys5Dv5Gw2GhKxAYruV8oZf5QxGZS5LZx9G8HJ.N5vZ5XKm2C', 'sdr', TRUE)
ON CONFLICT (email) DO NOTHING;

-- ── Clientes de exemplo (substituir com dados reais do Caio) ──
-- Os slugs são usados como identificadores em comandos e configs

INSERT INTO clients (name, slug, status, specialty, client_type, config) VALUES
    ('Ottoboni', 'ottoboni', 'active', 'Implantes', 'professor',
     '{"tom_voz": "profissional-autoridade", "thresholds": {"cpl_max": 50.0, "ctr_min": 1.5}}'),
    ('Linardi', 'linardi', 'active', 'Lentes de Contato', 'professor',
     '{"tom_voz": "sofisticado-premium", "thresholds": {"cpl_max": 45.0, "ctr_min": 1.8}}'),
    ('Elite Motors', 'elite-motors', 'active', 'Motores Elétricos', 'empresa',
     '{"tom_voz": "tecnico-premium", "thresholds": {"cpl_max": 80.0, "ctr_min": 1.2}}')
ON CONFLICT (slug) DO NOTHING;

-- NOTA: Caio tem 17 clientes ativos. Os demais devem ser adicionados
-- quando ele fornecer a lista completa. Usar o template:
--
-- INSERT INTO clients (name, slug, status, specialty, client_type, config) VALUES
--     ('Nome do Cliente', 'slug-do-cliente', 'active', 'Especialidade', 'professor',
--      '{"tom_voz": "...", "thresholds": {"cpl_max": 0, "ctr_min": 0}}');
