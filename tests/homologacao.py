"""
Villa — Suite de Homologação
Roda ANTES de marcar qualquer módulo como concluído.
Todos os testes precisam passar para o módulo ir para produção.

Uso:
    python tests/homologacao.py              # todos os módulos
    python tests/homologacao.py m01          # módulo específico
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import datetime


# ═══════════════════════════════════════════════════════
# NÍVEL 1 — INFRAESTRUTURA (banco, extensões, tabelas)
# ═══════════════════════════════════════════════════════

async def check_infra(db_url: str) -> list[dict]:
    """Verifica se toda a infraestrutura necessária está em ordem."""
    import asyncpg
    results = []

    conn = await asyncpg.connect(db_url)
    try:
        # 1. pgvector habilitado
        row = await conn.fetchrow(
            "SELECT COUNT(*) FROM pg_extension WHERE extname = 'vector'"
        )
        results.append({
            "check": "pgvector extension",
            "ok": row["count"] > 0,
            "fix": "CREATE EXTENSION IF NOT EXISTS vector;",
        })

        # 2. Tabelas obrigatórias existem
        required_tables = [
            "users", "clients", "leads", "appointments", "campaigns",
            "roteiros", "reports", "module_configs", "audit_logs",
            "decision_logs", "knowledge_documents", "knowledge_embeddings",
            "sdr_conversations", "sdr_objections",
            "smooth_messages", "smooth_members", "smooth_insights",
        ]
        existing = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
        existing_names = {r["tablename"] for r in existing}
        for table in required_tables:
            ok = table in existing_names
            results.append({
                "check": f"tabela {table}",
                "ok": ok,
                "fix": f"Rodar migration correspondente" if not ok else None,
            })

        # 3. Enums corretos no banco
        enums_expected = {
            "userrole":      ["admin", "operator", "sdr", "readonly"],
            "module_code":   ["m01_roteiros", "m02_relatorios", "m14_suporte_mari"],
            "client_status": ["active", "onboarding", "paused", "churned"],
        }
        for enum_name, expected_vals in enums_expected.items():
            rows = await conn.fetch(
                "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON e.enumtypid = t.oid "
                "WHERE t.typname = $1", enum_name
            )
            actual = {r["enumlabel"] for r in rows}
            for val in expected_vals:
                ok = val in actual
                results.append({
                    "check": f"enum {enum_name}.{val}",
                    "ok": ok,
                    "fix": f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{val}';" if not ok else None,
                })

        # 4. Módulos ativos no banco
        active_modules = await conn.fetch(
            "SELECT module FROM module_configs WHERE is_active = true"
        )
        active = {r["module"] for r in active_modules}
        required_active = [
            "m01_roteiros", "m02_relatorios", "m04_campanhas",
            "m07_retroalimentacao", "m08_onboarding", "m09_arquivos",
            "m10_smooth", "m11_hipoteses", "m12_alertas", "m14_suporte_mari",
        ]
        for mod in required_active:
            ok = mod in active
            results.append({
                "check": f"module_config ativo: {mod}",
                "ok": ok,
                "fix": f"INSERT INTO module_configs (module, is_active, config) VALUES ('{mod}', TRUE, '{{}}') ON CONFLICT (module) DO UPDATE SET is_active = TRUE;" if not ok else None,
            })

        # 5. Ao menos 1 cliente cadastrado
        client_count = await conn.fetchrow("SELECT COUNT(*) FROM clients")
        results.append({
            "check": "ao menos 1 cliente cadastrado",
            "ok": client_count["count"] > 0,
            "fix": "POST /clients com dados do primeiro cliente",
        })

    finally:
        await conn.close()

    return results


# ═══════════════════════════════════════════════════════
# NÍVEL 2 — MÓDULOS (sintaxe, imports, instanciação)
# ═══════════════════════════════════════════════════════

async def check_modules() -> list[dict]:
    """Verifica se todos os módulos instanciam sem erro."""
    import ast
    results = []

    modules = [
        ("M01Roteiros",        "modules.m01_roteiros.agent"),
        ("M02Relatorios",      "modules.m02_relatorios.agent"),
        ("M03Qualificacao",    "modules.m03_qualificacao.agent"),
        ("M04Campanhas",       "modules.m04_campanhas.agent"),
        ("M05Agendamento",     "modules.m05_agendamento.agent"),
        ("M06Atendimento",     "modules.m06_atendimento.agent"),
        ("M07Retroalimentacao","modules.m07_retroalimentacao.agent"),
        ("M08Onboarding",      "modules.m08_onboarding.agent"),
        ("M09Arquivos",        "modules.m09_arquivos.agent"),
        ("M10Smooth",          "modules.m10_smooth.agent"),
        ("M11Hipoteses",       "modules.m11_hipoteses.agent"),
        ("M12Alertas",         "modules.m12_alertas.agent"),
        ("M14SuporteMari",     "modules.m14_suporte_mari.agent"),
        ("M15MonitorSmooth",   "modules.m15_monitor_smooth.agent"),
    ]

    for cls_name, mod_path in modules:
        # Sintaxe
        try:
            file_path = mod_path.replace(".", "/") + ".py"
            ast.parse(open(file_path).read())
            syntax_ok = True
        except SyntaxError as e:
            results.append({"check": f"{cls_name} sintaxe", "ok": False, "fix": str(e)})
            continue

        # Import + instanciação
        try:
            mod = __import__(mod_path, fromlist=[cls_name])
            cls = getattr(mod, cls_name)
            instance = cls()

            # can_handle com mensagem vazia não pode lançar exceção
            score = await instance.can_handle("teste")
            assert isinstance(score, float), "can_handle deve retornar float"

            results.append({"check": f"{cls_name} instanciação + can_handle", "ok": True})
        except Exception as e:
            results.append({
                "check": f"{cls_name} instanciação",
                "ok": False,
                "fix": str(e)[:100],
            })

    return results


# ═══════════════════════════════════════════════════════
# NÍVEL 3 — ROTEAMENTO (módulos corretos ativados)
# ═══════════════════════════════════════════════════════

async def check_routing() -> list[dict]:
    """Verifica se os comandos mais comuns roteiam para o módulo correto."""
    results = []

    routing_cases = [
        ("gera roteiro de implante", "M01Roteiros", "modules.m01_roteiros.agent", 0.7),
        ("relatorio semanal webxp", "M02Relatorios", "modules.m02_relatorios.agent", 0.6),
        ("qualificar lead instagram", "M03Qualificacao", "modules.m03_qualificacao.agent", 0.7),
        ("agendar consulta amanha", "M05Agendamento", "modules.m05_agendamento.agent", 0.5),
        ("suporte mari lead disse nao tenho tempo", "M14SuporteMari", "modules.m14_suporte_mari.agent", 0.8),
    ]

    for msg, cls_name, mod_path, min_score in routing_cases:
        try:
            mod = __import__(mod_path, fromlist=[cls_name])
            instance = getattr(mod, cls_name)()
            score = await instance.can_handle(msg)
            ok = score >= min_score
            results.append({
                "check": f'routing: "{msg[:35]}" → {cls_name}',
                "ok": ok,
                "fix": f"Score {score:.2f} < mínimo {min_score}. Revisar KEYWORDS." if not ok else None,
            })
        except Exception as e:
            results.append({"check": f"routing {cls_name}", "ok": False, "fix": str(e)[:100]})

    # Stand-by não intercepta roteamento
    stanby_cases = [
        ("M03Qualificacao", "modules.m03_qualificacao.agent"),
        ("M05Agendamento",  "modules.m05_agendamento.agent"),
        ("M06Atendimento",  "modules.m06_atendimento.agent"),
    ]
    for cls_name, mod_path in stanby_cases:
        mod = __import__(mod_path, fromlist=[cls_name])
        instance = getattr(mod, cls_name)()
        result = await instance.execute("teste", db=None)
        ok = result.get("success") == False and "STAND_BY" in result.get("message", "").upper() or "stand_by" in str(result.get("actions_taken", []))
        results.append({
            "check": f"{cls_name} stand-by bloqueia execute",
            "ok": ok,
            "fix": "Verificar guard STAND_BY no execute()" if not ok else None,
        })

    return results


# ═══════════════════════════════════════════════════════
# RUNNER PRINCIPAL
# ═══════════════════════════════════════════════════════

async def run(db_url: str = None):
    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║     VILLA — SUITE DE HOMOLOGAÇÃO                ║")
    print(f"║     {datetime.now().strftime('%d/%m/%Y %H:%M')}                            ║")
    print("╚══════════════════════════════════════════════════╝")
    print()

    all_results = []
    total_ok = 0
    total_fail = 0

    sections = [
        ("NÍVEL 2 — MÓDULOS",    check_modules,  None),
        ("NÍVEL 3 — ROTEAMENTO", check_routing,  None),
    ]

    if db_url:
        sections.insert(0, ("NÍVEL 1 — INFRAESTRUTURA", check_infra, db_url))

    for section_name, fn, arg in sections:
        print(f"── {section_name} ──")
        try:
            results = await fn(arg) if arg else await fn()
        except Exception as e:
            print(f"  ❌ ERRO AO EXECUTAR SEÇÃO: {e}")
            continue

        for r in results:
            status = "✅" if r["ok"] else "❌"
            print(f"  {status} {r['check']}")
            if not r["ok"] and r.get("fix"):
                print(f"     FIX: {r['fix']}")
            if r["ok"]:
                total_ok += 1
            else:
                total_fail += 1
            all_results.append(r)
        print()

    print("═══════════════════════════════════════════════════")
    print(f"  Resultado: {total_ok} OK  |  {total_fail} FALHAS")
    if total_fail == 0:
        print("  ✅ HOMOLOGAÇÃO APROVADA — módulo pronto para produção")
    else:
        print("  ❌ HOMOLOGAÇÃO REPROVADA — corrigir falhas antes de prosseguir")
    print("═══════════════════════════════════════════════════")
    print()

    return total_fail == 0


if __name__ == "__main__":
    db_url = os.getenv("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
    ok = asyncio.run(run(db_url if db_url else None))
    sys.exit(0 if ok else 1)
