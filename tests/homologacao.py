"""
Villa — Suite de Homologação
Roda ANTES de marcar qualquer módulo como concluído.
Todos os testes precisam passar para o módulo ir para produção.

Uso:
    python tests/homologacao.py              # todos os níveis
    python tests/homologacao.py --skip-db    # pula conexão ao banco (offline)
"""

import asyncio
import os
import re
import sys
import urllib.parse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════


def build_safe_db_url(db_url: str) -> str:
    """Constrói URL segura para asyncpg — trata senhas com @ e # corretamente."""
    m = re.match(r"postgresql(?:\+asyncpg)?://([^:]+):(.+)@([^:@]+):?(\d+)?/(.+)", db_url)
    if not m:
        return db_url.replace("postgresql+asyncpg://", "postgresql://")
    user, password, host, port, db = m.groups()
    port = port or "5432"
    safe_password = urllib.parse.quote(password, safe="")
    return f"postgresql://{user}:{safe_password}@{host}:{port}/{db}"


def result(check: str, ok: bool, fix: str = None) -> dict:
    return {"check": check, "ok": ok, "fix": fix}


# ═══════════════════════════════════════════════════════
# NÍVEL 1 — INFRAESTRUTURA DO BANCO
# ═══════════════════════════════════════════════════════


async def check_infra(db_url: str) -> list[dict]:
    """Verifica banco, extensões, tabelas, enums e módulos ativos."""
    import asyncpg

    results = []

    safe_url = build_safe_db_url(db_url)

    try:
        conn = await asyncpg.connect(safe_url)
    except Exception as e:
        return [result("conexão ao banco", False, f"Verificar DATABASE_URL. Erro: {e}")]

    try:
        # ── Extensões ──
        exts = {r["extname"] for r in await conn.fetch("SELECT extname FROM pg_extension")}

        results.append(
            result(
                "extensão vector (pgvector)",
                "vector" in exts,
                "CREATE EXTENSION IF NOT EXISTS vector;",
            )
        )
        results.append(
            result(
                "extensão uuid-ossp",
                "uuid-ossp" in exts,
                'CREATE EXTENSION IF NOT EXISTS "uuid-ossp";',
            )
        )

        # ── Tabelas ──
        existing = {
            r["tablename"]
            for r in await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        }
        required_tables = [
            "users",
            "clients",
            "leads",
            "appointments",
            "campaigns",
            "roteiros",
            "reports",
            "module_configs",
            "audit_logs",
            "decision_logs",
            "knowledge_documents",
            "knowledge_embeddings",
            "sdr_conversations",
            "sdr_objections",
            "smooth_messages",
            "smooth_members",
            "smooth_insights",
        ]
        for table in required_tables:
            results.append(
                result(
                    f"tabela {table}",
                    table in existing,
                    "Rodar migration correspondente" if table not in existing else None,
                )
            )

        # ── Enums ──
        enum_checks = {
            "userrole": ["admin", "operator", "sdr", "readonly"],
            "module_code": [
                "m01_roteiros",
                "m02_relatorios",
                "m14_suporte_mari",
                "m15_monitor_smooth",
            ],
            "client_status": ["active", "onboarding", "paused", "churned"],
            "action_risk": ["low", "medium", "high"],
        }
        for enum_name, expected in enum_checks.items():
            rows = await conn.fetch(
                "SELECT enumlabel FROM pg_enum e "
                "JOIN pg_type t ON e.enumtypid = t.oid WHERE t.typname = $1",
                enum_name,
            )
            actual = {r["enumlabel"] for r in rows}
            for val in expected:
                results.append(
                    result(
                        f"enum {enum_name} → '{val}'",
                        val in actual,
                        f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{val}';"
                        if val not in actual
                        else None,
                    )
                )

        # ── Module configs ativos ──
        active = {
            r["module"]
            for r in await conn.fetch("SELECT module FROM module_configs WHERE is_active = true")
        }
        required_active = [
            "m01_roteiros",
            "m02_relatorios",
            "m04_campanhas",
            "m07_retroalimentacao",
            "m08_onboarding",
            "m09_arquivos",
            "m10_smooth",
            "m11_hipoteses",
            "m12_alertas",
            "m14_suporte_mari",
        ]
        for mod in required_active:
            results.append(
                result(
                    f"module_config ativo: {mod}",
                    mod in active,
                    f"INSERT INTO module_configs (module, is_active, config) "
                    f"VALUES ('{mod}', TRUE, '{{}}') "
                    f"ON CONFLICT (module) DO UPDATE SET is_active = TRUE;"
                    if mod not in active
                    else None,
                )
            )

        # ── Ao menos 1 cliente ──
        count = await conn.fetchval("SELECT COUNT(*) FROM clients")
        results.append(
            result(
                "ao menos 1 cliente cadastrado",
                count > 0,
                "POST /clients com dados do primeiro cliente",
            )
        )

        # ── pgvector funciona na prática ──
        try:
            await conn.execute("SELECT '[1,2,3]'::vector <=> '[1,2,3]'::vector")
            results.append(result("pgvector operador <=> funciona", True))
        except Exception as e:
            results.append(
                result(
                    "pgvector operador <=> funciona",
                    False,
                    f"CREATE EXTENSION IF NOT EXISTS vector; Erro: {e}",
                )
            )

    finally:
        await conn.close()

    return results


# ═══════════════════════════════════════════════════════
# NÍVEL 2 — MÓDULOS (sintaxe, imports, instanciação)
# ═══════════════════════════════════════════════════════


async def check_modules() -> list[dict]:
    """Verifica se todos os módulos instanciam e respondem corretamente."""
    import ast

    results = []

    modules = [
        ("M01Roteiros", "modules.m01_roteiros.agent"),
        ("M02Relatorios", "modules.m02_relatorios.agent"),
        ("M03Qualificacao", "modules.m03_qualificacao.agent"),
        ("M04Campanhas", "modules.m04_campanhas.agent"),
        ("M05Agendamento", "modules.m05_agendamento.agent"),
        ("M06Atendimento", "modules.m06_atendimento.agent"),
        ("M07Retroalimentacao", "modules.m07_retroalimentacao.agent"),
        ("M08Onboarding", "modules.m08_onboarding.agent"),
        ("M09Arquivos", "modules.m09_arquivos.agent"),
        ("M10Smooth", "modules.m10_smooth.agent"),
        ("M11Hipoteses", "modules.m11_hipoteses.agent"),
        ("M12Alertas", "modules.m12_alertas.agent"),
        ("M14SuporteMari", "modules.m14_suporte_mari.agent"),
        ("M15MonitorSmooth", "modules.m15_monitor_smooth.agent"),
    ]

    for cls_name, mod_path in modules:
        # Sintaxe
        file_path = mod_path.replace(".", "/") + ".py"
        try:
            ast.parse(open(file_path).read())
        except SyntaxError as e:
            results.append(result(f"{cls_name} sintaxe", False, str(e)))
            continue

        # Import + instanciação + can_handle
        try:
            mod = __import__(mod_path, fromlist=[cls_name])
            cls = getattr(mod, cls_name)
            instance = cls()
            score = await instance.can_handle("teste")
            assert isinstance(score, float), "can_handle deve retornar float"
            results.append(result(f"{cls_name} instanciação + can_handle", True))
        except Exception as e:
            results.append(result(f"{cls_name} instanciação", False, str(e)[:120]))

    # ── Arquivos críticos de memória ──
    memory_files = [
        ("memory/embeddings.py", "CAST(:query_vec AS vector)", "Fix: usar CAST na query pgvector"),
        ("memory/feedback_loop.py", "begin_nested", "Fix: usar savepoint no knowledge base"),
    ]
    for filepath, expected_token, fix_msg in memory_files:
        try:
            content = open(filepath).read()
            ok = expected_token in content
            results.append(
                result(f"{filepath} — '{expected_token}'", ok, fix_msg if not ok else None)
            )
        except FileNotFoundError:
            results.append(result(f"{filepath} existe", False, "Arquivo não encontrado"))

    return results


# ═══════════════════════════════════════════════════════
# NÍVEL 3 — ROTEAMENTO
# ═══════════════════════════════════════════════════════


async def check_routing() -> list[dict]:
    """Verifica se comandos comuns roteiam para o módulo correto."""
    results = []

    routing_cases = [
        ("gera roteiro de implante", "M01Roteiros", "modules.m01_roteiros.agent", 0.7),
        ("relatorio semanal webxp", "M02Relatorios", "modules.m02_relatorios.agent", 0.6),
        ("qualificar lead instagram", "M03Qualificacao", "modules.m03_qualificacao.agent", 0.7),
        ("agendar consulta amanha", "M05Agendamento", "modules.m05_agendamento.agent", 0.5),
        (
            "suporte mari lead disse nao tenho tempo",
            "M14SuporteMari",
            "modules.m14_suporte_mari.agent",
            0.8,
        ),
    ]

    for msg, cls_name, mod_path, min_score in routing_cases:
        try:
            mod = __import__(mod_path, fromlist=[cls_name])
            instance = getattr(mod, cls_name)()
            score = await instance.can_handle(msg)
            ok = score >= min_score
            results.append(
                result(
                    f'routing: "{msg[:35]}" → {cls_name}',
                    ok,
                    f"Score {score:.2f} < mínimo {min_score}. Revisar KEYWORDS."
                    if not ok
                    else None,
                )
            )
        except Exception as e:
            results.append(result(f"routing {cls_name}", False, str(e)[:100]))

    # Stand-by: execute retorna mensagem explicativa
    for cls_name, mod_path in [
        ("M03Qualificacao", "modules.m03_qualificacao.agent"),
        ("M05Agendamento", "modules.m05_agendamento.agent"),
        ("M06Atendimento", "modules.m06_atendimento.agent"),
    ]:
        try:
            mod = __import__(mod_path, fromlist=[cls_name])
            instance = getattr(mod, cls_name)()
            r = await instance.execute("teste", db=None)
            ok = r.get("success") == False and (
                "stand_by" in str(r.get("actions_taken", []))
                or "STAND_BY" in r.get("message", "").upper()
            )
            results.append(
                result(
                    f"{cls_name} stand-by bloqueia execute",
                    ok,
                    "Verificar guard STAND_BY no execute()" if not ok else None,
                )
            )
        except Exception as e:
            results.append(result(f"{cls_name} stand-by", False, str(e)[:100]))

    return results


# ═══════════════════════════════════════════════════════
# RUNNER PRINCIPAL
# ═══════════════════════════════════════════════════════


async def run(skip_db: bool = False):
    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║     VILLA — SUITE DE HOMOLOGAÇÃO                ║")
    print(f"║     {datetime.now().strftime('%d/%m/%Y %H:%M')}                            ║")
    print("╚══════════════════════════════════════════════════╝")
    print()

    total_ok = 0
    total_fail = 0

    db_url = os.getenv("DATABASE_URL", "")

    sections = []
    if not skip_db and db_url:
        sections.append(("NÍVEL 1 — INFRAESTRUTURA (banco)", check_infra, db_url))
    elif not skip_db and not db_url:
        print("⚠️  DATABASE_URL não definida — pulando Nível 1")
        print("   Para rodar com banco: DATABASE_URL=... python tests/homologacao.py")
        print()

    sections += [
        ("NÍVEL 2 — MÓDULOS", check_modules, None),
        ("NÍVEL 3 — ROTEAMENTO", check_routing, None),
    ]

    for section_name, fn, arg in sections:
        print(f"── {section_name} ──")
        try:
            results = await fn(arg) if arg else await fn()
        except Exception as e:
            print(f"  ❌ ERRO AO EXECUTAR SEÇÃO: {e}")
            total_fail += 1
            print()
            continue

        for r in results:
            status = "✅" if r["ok"] else "❌"
            print(f"  {status} {r['check']}")
            if not r["ok"] and r.get("fix"):
                print(f"     → FIX: {r['fix']}")
            if r["ok"]:
                total_ok += 1
            else:
                total_fail += 1
        print()

    print("═══════════════════════════════════════════════════")
    print(f"  Resultado: {total_ok} OK  |  {total_fail} FALHAS")
    if total_fail == 0:
        print("  ✅ HOMOLOGAÇÃO APROVADA — pronto para produção")
    else:
        print("  ❌ HOMOLOGAÇÃO REPROVADA — corrigir falhas antes de prosseguir")
    print("═══════════════════════════════════════════════════")
    print()

    return total_fail == 0


if __name__ == "__main__":
    skip_db = "--skip-db" in sys.argv
    ok = asyncio.run(run(skip_db=skip_db))
    sys.exit(0 if ok else 1)
