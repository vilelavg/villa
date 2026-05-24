# ═══════════════════════════════════════════════════════════════
# VILLA — tests/unit/test_m01_roteiros.py
#
# Testes unitários do M01Roteiros baseados no código real.
#
# Interface real confirmada:
#   Classe: M01Roteiros (modules/m01_roteiros/agent.py)
#   Método: execute(message, db, user, client_slug, context) -> dict
#   Retorno: {"success": bool, "message": str, "data": dict, "actions_taken": list, "tokens_used": int}
#
# O que está sendo testado:
#   - Happy path completo (geração + tripla validação + salvar)
#   - Resolução de cliente (por slug e por nome no texto)
#   - Falha se cliente não encontrado
#   - Tripla validação: roteiro que não passa é refinado (até 2x)
#   - Roteiro com gancho/corpo/CTA ausente retorna success=False
#   - Falha do Claude retorna success=False sem lançar exceção
#   - Tokens e custo registrados no resultado
#   - client_slug registrado nos dados de saída
# ═══════════════════════════════════════════════════════════════

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ── Factories ────────────────────────────────────────────────────────────────

def make_client(slug: str = "clinica-demo") -> MagicMock:
    """Cria um objeto Client fake com os atributos mínimos necessários."""
    client = MagicMock()
    client.id = "client-uuid-123"
    client.slug = slug
    client.name = "Clínica Demo"
    client.specialty = "implante"
    client.config = {}
    return client


def make_db(client: MagicMock | None = None) -> AsyncMock:
    """Cria um db (AsyncSession) fake que retorna o client quando executado."""
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = client
    mock_result.scalars.return_value.all.return_value = [client] if client else []
    db.execute.return_value = mock_result
    db.flush = AsyncMock()
    db.add = MagicMock()
    return db


def make_claude_response(text: str) -> dict:
    """Resposta real do AnthropicClient.ask() — retorna dict."""
    return {
        "text": text,
        "tokens_input": 250,
        "tokens_output": 180,
        "model": "claude-sonnet-4-20250514",
        "cost_usd": 0.003,
        "stop_reason": "end_turn",
    }


def make_validation(all_passed: bool = True, score: float = 8.5) -> dict:
    """Resultado fake da RoteiroValidator.validate_full()."""
    return {
        "all_passed": all_passed,
        "overall_score": score,
        "feedback_summary": "Roteiro excelente." if all_passed else "Gancho fraco.",
        "validations": {
            "hook": {
                "passed": all_passed,
                "score": score,
                "feedback": "Ótimo gancho" if all_passed else "Gancho sem impacto",
                "suggestion": None if all_passed else "Use uma pergunta provocativa.",
            },
            "body": {
                "passed": True,
                "score": 8.0,
                "feedback": "Corpo coerente com a especialidade",
                "suggestion": None,
            },
            "cta": {
                "passed": True,
                "score": 9.0,
                "feedback": "CTA direto e acionável",
                "suggestion": None,
            },
        },
        "total_tokens": 120,
    }


ROTEIRO_VALIDO = """
TÍTULO: Implante All-on-4 — Sorria Novamente

GANCHO:
Você sabia que 70% das pessoas com dentes faltando evitam sorrir em fotos?
O implante All-on-4 resolve isso em uma única cirurgia — permanente, firme, natural.

CORPO:
Diferente da prótese removível, o All-on-4 usa apenas 4 implantes para suportar
uma arcada completa. Sem colar, sem dor ao comer, sem embaraço. O resultado
parece e funciona como dente natural — porque tecnicamente é.

CTA:
Clique no link e agende sua avaliação gratuita hoje.
Vagas limitadas — apenas 20 por mês.

ROTEIRO COMPLETO:
[Câmera no dentista] Você sabia que 70% das pessoas com dentes faltando evitam sorrir?
...
""".strip()

BRIEFING_RESPONSE = {
    "data": {
        "topic": "implante All-on-4",
        "format": "Reels (30-60s)",
        "audience": "adultos 45-65 anos",
        "objective": "gerar leads qualificados",
    }
}

FEEDBACK_CONTEXT = {
    "prompt_injection": "",
    "reasoning_context": "Módulo de roteiros executando pela primeira vez neste contexto.",
    "sources": [],
}


# ── Fixture de setup global ───────────────────────────────────────────────────

@pytest.fixture
def setup_m01(request):
    """
    Fixture que monta todo o ambiente de mocks para M01Roteiros.
    Retorna tupla (module, db, patches_dict).
    """
    client = make_client(getattr(request, "param", "clinica-demo"))
    db = make_db(client)

    patches = {}

    # Mock FeedbackLoop completo
    fl_mock = AsyncMock()
    fl_mock.build_context.return_value = FEEDBACK_CONTEXT
    fl_mock.record_decision.return_value = "decision-uuid-456"
    patches["feedback_loop"] = patch(
        "modules.m01_roteiros.agent.FeedbackLoop", return_value=fl_mock
    )

    return client, db, patches


# ── Testes de geração básica (happy path) ─────────────────────────────────────

class TestGerarRoteiroHappyPath:

    async def test_retorna_success_true_com_roteiro_valido(self, setup_m01):
        client, db, patches = setup_m01

        with patches["feedback_loop"]:
            from modules.m01_roteiros.agent import M01Roteiros

            module = M01Roteiros()

            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch.object(module.claude, "extract_json", new_callable=AsyncMock) as mock_json, \
                 patch("modules.m01_roteiros.agent.RoteiroValidator") as mock_validator:

                mock_ask.return_value = make_claude_response(ROTEIRO_VALIDO)
                mock_json.return_value = BRIEFING_RESPONSE
                mock_validator.return_value.validate_full = AsyncMock(return_value=make_validation(True))
                mock_validator.return_value.validate_full.return_value = make_validation(True)
                # hook variations
                module.claude.extract_json.return_value = {"data": {"variations": []}}

                result = await module.execute(
                    message="Gera roteiro de implante para a Clínica Demo",
                    db=db,
                    client_slug="clinica-demo",
                )

        assert result["success"] is True

    async def test_resultado_contem_campos_obrigatorios(self, setup_m01):
        client, db, patches = setup_m01

        with patches["feedback_loop"]:
            from modules.m01_roteiros.agent import M01Roteiros

            module = M01Roteiros()
            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch.object(module.claude, "extract_json", new_callable=AsyncMock) as mock_json, \
                 patch("modules.m01_roteiros.agent.RoteiroValidator") as mock_validator:

                mock_ask.return_value = make_claude_response(ROTEIRO_VALIDO)
                mock_json.return_value = BRIEFING_RESPONSE
                mock_validator.return_value.validate_full = AsyncMock(return_value=make_validation(True))

                result = await module.execute(
                    message="Roteiro de implante",
                    db=db,
                    client_slug="clinica-demo",
                )

        for campo in ("success", "message", "data", "actions_taken"):
            assert campo in result, f"Campo obrigatório '{campo}' ausente no resultado"

    async def test_data_contem_roteiro_id_e_client_slug(self, setup_m01):
        client, db, patches = setup_m01

        with patches["feedback_loop"]:
            from modules.m01_roteiros.agent import M01Roteiros

            module = M01Roteiros()
            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch.object(module.claude, "extract_json", new_callable=AsyncMock) as mock_json, \
                 patch("modules.m01_roteiros.agent.RoteiroValidator") as mock_validator:

                mock_ask.return_value = make_claude_response(ROTEIRO_VALIDO)
                mock_json.return_value = BRIEFING_RESPONSE
                mock_validator.return_value.validate_full = AsyncMock(return_value=make_validation(True))

                result = await module.execute(
                    message="Roteiro de implante",
                    db=db,
                    client_slug="clinica-demo",
                )

        assert result["success"] is True
        assert "roteiro_id" in result["data"]
        assert result["data"]["client"] == "clinica-demo"

    async def test_actions_taken_inclui_geração_e_validacao(self, setup_m01):
        client, db, patches = setup_m01

        with patches["feedback_loop"]:
            from modules.m01_roteiros.agent import M01Roteiros

            module = M01Roteiros()
            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch.object(module.claude, "extract_json", new_callable=AsyncMock) as mock_json, \
                 patch("modules.m01_roteiros.agent.RoteiroValidator") as mock_validator:

                mock_ask.return_value = make_claude_response(ROTEIRO_VALIDO)
                mock_json.return_value = BRIEFING_RESPONSE
                mock_validator.return_value.validate_full = AsyncMock(return_value=make_validation(True))

                result = await module.execute(
                    message="Roteiro de implante",
                    db=db,
                    client_slug="clinica-demo",
                )

        actions = result.get("actions_taken", [])
        assert "roteiro_generated" in actions
        assert "triple_validation" in actions


# ── Testes de resolução de cliente ────────────────────────────────────────────

class TestResolucaoCliente:

    async def test_cliente_nao_encontrado_retorna_success_false(self):
        """Se o cliente não existir no banco, retorna success=False sem travar."""
        db = make_db(client=None)  # banco não encontra nenhum cliente

        with patch("modules.m01_roteiros.agent.FeedbackLoop"):
            from modules.m01_roteiros.agent import M01Roteiros

            module = M01Roteiros()
            result = await module.execute(
                message="Roteiro de implante",
                db=db,
                client_slug="cliente-inexistente",
            )

        assert result["success"] is False
        assert "client_not_found" in result.get("actions_taken", [])

    async def test_client_slug_none_sem_nome_no_texto_retorna_false(self):
        """Sem slug e sem nome reconhecível no texto, deve falhar."""
        db = make_db(client=None)

        with patch("modules.m01_roteiros.agent.FeedbackLoop"):
            from modules.m01_roteiros.agent import M01Roteiros

            module = M01Roteiros()
            result = await module.execute(
                message="Gera um roteiro",
                db=db,
                client_slug=None,
            )

        assert result["success"] is False


# ── Testes da tripla validação ────────────────────────────────────────────────

class TestTriplaValidacao:

    async def test_roteiro_aprovado_quando_validacao_passa(self, setup_m01):
        client, db, patches = setup_m01

        with patches["feedback_loop"]:
            from modules.m01_roteiros.agent import M01Roteiros

            module = M01Roteiros()
            # Patch direto na instância (não na classe) — o __init__ já criou
            # self.validator antes do patch de classe entrar em vigor.
            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch.object(module.claude, "extract_json", new_callable=AsyncMock) as mock_json, \
                 patch.object(module.validator, "validate_full", new_callable=AsyncMock) as mock_validate:

                mock_ask.return_value = make_claude_response(ROTEIRO_VALIDO)
                mock_json.return_value = BRIEFING_RESPONSE
                mock_validate.return_value = make_validation(True, 8.5)

                result = await module.execute(
                    message="Roteiro de implante",
                    db=db,
                    client_slug="clinica-demo",
                )

        assert result["success"] is True
        assert result["data"]["all_passed"] is True
        assert result["data"]["overall_score"] == 8.5

    async def test_roteiro_refinado_quando_validacao_falha(self, setup_m01):
        """
        Quando a validação falha, o módulo deve refinar automaticamente.
        Confirma que attempts > 0 no resultado.
        """
        client, db, patches = setup_m01

        with patches["feedback_loop"]:
            from modules.m01_roteiros.agent import M01Roteiros

            module = M01Roteiros()
            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch.object(module.claude, "extract_json", new_callable=AsyncMock) as mock_json, \
                 patch("modules.m01_roteiros.agent.RoteiroValidator") as mock_validator:

                mock_ask.return_value = make_claude_response(ROTEIRO_VALIDO)
                mock_json.return_value = BRIEFING_RESPONSE

                # Falha na 1ª validação, passa na 2ª
                mock_validator.return_value.validate_full = AsyncMock(
                    side_effect=[
                        make_validation(False, 5.0),  # 1ª: falha
                        make_validation(True, 8.0),   # 2ª: passa após refinamento
                    ]
                )

                result = await module.execute(
                    message="Roteiro de implante",
                    db=db,
                    client_slug="clinica-demo",
                )

        assert result["success"] is True
        # attempts deve ser > 1 (refinamento ocorreu)
        assert result["data"]["attempts"] > 1

    async def test_roteiro_sem_gancho_corpo_cta_retorna_false(self, setup_m01):
        """Texto gerado sem estrutura GANCHO/CORPO/CTA — parse falha."""
        client, db, patches = setup_m01

        texto_sem_estrutura = "Este é um texto qualquer sem as seções obrigatórias."

        with patches["feedback_loop"]:
            from modules.m01_roteiros.agent import M01Roteiros

            module = M01Roteiros()
            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch.object(module.claude, "extract_json", new_callable=AsyncMock) as mock_json, \
                 patch("modules.m01_roteiros.agent.RoteiroValidator"):

                mock_ask.return_value = make_claude_response(texto_sem_estrutura)
                mock_json.return_value = BRIEFING_RESPONSE

                result = await module.execute(
                    message="Roteiro de implante",
                    db=db,
                    client_slug="clinica-demo",
                )

        assert result["success"] is False
        assert "generation_failed" in result.get("actions_taken", [])


# ── Testes de resiliência ─────────────────────────────────────────────────────

class TestResiliencia:

    async def test_falha_do_claude_nao_lanca_excecao(self, setup_m01):
        """Se ask_claude() lançar exceção, execute() deve capturar e retornar dict."""
        client, db, patches = setup_m01

        with patches["feedback_loop"]:
            from modules.m01_roteiros.agent import M01Roteiros

            module = M01Roteiros()
            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch.object(module.claude, "extract_json", new_callable=AsyncMock) as mock_json:

                mock_ask.side_effect = Exception("Anthropic API unavailable")
                mock_json.return_value = BRIEFING_RESPONSE

                result = await module.execute(
                    message="Roteiro de implante",
                    db=db,
                    client_slug="clinica-demo",
                )
                assert result["success"] is False

    async def test_timeout_nao_lanca_excecao(self, setup_m01):
        """TimeoutError também deve ser capturado."""
        import asyncio
        client, db, patches = setup_m01

        with patches["feedback_loop"]:
            from modules.m01_roteiros.agent import M01Roteiros

            module = M01Roteiros()
            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch.object(module.claude, "extract_json", new_callable=AsyncMock) as mock_json:

                mock_ask.side_effect = asyncio.TimeoutError()
                mock_json.return_value = BRIEFING_RESPONSE

                result = await module.execute(
                    message="Roteiro de implante",
                    db=db,
                    client_slug="clinica-demo",
                )
                assert result["success"] is False


# ── Testes de rastreamento de tokens ─────────────────────────────────────────

class TestTokens:

    async def test_tokens_used_presente_e_positivo(self, setup_m01):
        client, db, patches = setup_m01

        with patches["feedback_loop"]:
            from modules.m01_roteiros.agent import M01Roteiros

            module = M01Roteiros()
            with patch.object(module, "ask_claude", new_callable=AsyncMock) as mock_ask, \
                 patch.object(module.claude, "extract_json", new_callable=AsyncMock) as mock_json, \
                 patch("modules.m01_roteiros.agent.RoteiroValidator") as mock_validator:

                mock_ask.return_value = make_claude_response(ROTEIRO_VALIDO)
                mock_json.return_value = BRIEFING_RESPONSE
                mock_validator.return_value.validate_full = AsyncMock(return_value=make_validation(True))

                result = await module.execute(
                    message="Roteiro de implante",
                    db=db,
                    client_slug="clinica-demo",
                )

        assert result["success"] is True
        tokens = result.get("tokens_used", 0)
        assert tokens > 0, "tokens_used deve ser maior que zero"


# ── Testes de can_handle ──────────────────────────────────────────────────────

class TestCanHandle:
    """
    can_handle() retorna float 0.0–1.0 indicando confiança do módulo
    em lidar com o comando. Não usa DB.
    """

    async def test_alta_confianca_para_roteiro(self):
        from modules.m01_roteiros.agent import M01Roteiros

        module = M01Roteiros()
        score = await module.can_handle("Gera roteiro de reels sobre implante")
        assert score >= 0.8, f"Esperado >= 0.8, obtido {score}"

    async def test_confianca_zero_para_assunto_nao_relacionado(self):
        from modules.m01_roteiros.agent import M01Roteiros

        module = M01Roteiros()
        score = await module.can_handle("Qual o relatório de campanhas dessa semana?")
        assert score == 0.0, f"Esperado 0.0, obtido {score}"

    async def test_confianca_media_para_verbo_generico(self):
        from modules.m01_roteiros.agent import M01Roteiros

        module = M01Roteiros()
        score = await module.can_handle("Cria algo")
        assert 0.0 < score < 0.8, f"Esperado entre 0.0 e 0.8, obtido {score}"
