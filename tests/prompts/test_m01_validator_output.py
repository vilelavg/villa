"""
Villa — tests/prompts/test_m01_validator_output.py

Testes de propriedades dos outputs do RoteiroValidator.

O que são "testes de propriedades LLM":
    Não testamos o conteúdo exato do que o Claude gera (impossível —
    cada chamada é diferente). Testamos as PROPRIEDADES ESTRUTURAIS
    do resultado processado pelo validador:
        - Campos obrigatórios presentes
        - Tipos corretos (bool, float, int, str)
        - Intervalos válidos (score 0-10, overall 0-10)
        - Pesos aplicados corretamente na média ponderada
        - Lógica de all_passed coerente com resultados individuais
        - feedback_summary inclui os 3 componentes
        - total_tokens é soma dos 3

Como funciona:
    Mockamos o Claude (extract_json) com respostas realistas
    e verificamos que o validator processa e consolida corretamente.
    O Claude real nunca é chamado — os testes rodam sem API key.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.prompts


# ── Factories de resposta Claude fake ─────────────────────────────────────────


def make_claude_validation_response(
    weighted_score: float,
    feedback: str = "Bom gancho com pattern interrupt claro.",
    suggestion: str = "Adicionar número específico para aumentar especificidade.",
    scores: dict | None = None,
    tokens_input: int = 350,
    tokens_output: int = 180,
    cost_usd: float = 0.002,
) -> dict:
    """
    Simula a resposta do claude.extract_json() para validação de componente.
    Formato real retornado pelo AnthropicClient.extract_json().
    """
    return {
        "data": {
            "weighted_score": weighted_score,
            "feedback": feedback,
            "suggestion": suggestion,
            "scores": scores
            or {
                "pattern_interrupt": 8,
                "especificidade": 7,
                "curiosidade": 8,
                "anti_cliche": 7,
            },
        },
        "parse_error": False,
        "raw_text": "",
        "tokens_input": tokens_input,
        "tokens_output": tokens_output,
        "cost_usd": cost_usd,
    }


def make_failed_response(tokens_input: int = 200, tokens_output: int = 50) -> dict:
    """Simula falha de parse do Claude (data=None)."""
    return {
        "data": None,
        "parse_error": True,
        "raw_text": "Resposta malformada do Claude",
        "tokens_input": tokens_input,
        "tokens_output": tokens_output,
        "cost_usd": 0.001,
    }


# ── Fixture: RoteiroValidator com Claude mockado ──────────────────────────────


@pytest.fixture
def validator():
    """RoteiroValidator com claude.extract_json mockado."""
    with patch("integrations.anthropic_client.settings") as mock_settings:
        mock_settings.anthropic_api_key = "fake-key-test"
        mock_settings.anthropic_model_primary = "claude-sonnet-4-20250514"
        mock_settings.anthropic_model_fast = "claude-haiku-4-5-20251001"

        from modules.m01_roteiros.validators import RoteiroValidator

        v = RoteiroValidator()
        v.claude = AsyncMock()
        return v


HOOK_EXEMPLO = "23 implantes em 1 dia — isso é mesmo possível?"
BODY_EXEMPLO = (
    "O Dr. Ottoboni desenvolveu uma técnica exclusiva que permite múltiplos "
    "implantes com anestesia local. Em 12 anos, mais de 2.000 pacientes atendidos."
)
CTA_EXEMPLO = "Clique no link da bio e agende sua avaliação gratuita esta semana."


# ══════════════════════════════════════════════════════════════════════════════
# validate_hook — propriedades do retorno
# ══════════════════════════════════════════════════════════════════════════════


class TestValidateHookPropriedades:
    async def test_retorno_tem_todos_campos_obrigatorios(self, validator):
        validator.claude.extract_json = AsyncMock(return_value=make_claude_validation_response(8.2))

        result = await validator.validate_hook(HOOK_EXEMPLO, "implantes")

        campos = [
            "component",
            "passed",
            "score",
            "scores_detail",
            "feedback",
            "suggestion",
            "min_score",
            "tokens_used",
            "cost_usd",
        ]
        for campo in campos:
            assert campo in result, f"Campo obrigatório ausente: '{campo}'"

    async def test_component_e_hook(self, validator):
        validator.claude.extract_json = AsyncMock(return_value=make_claude_validation_response(8.2))

        result = await validator.validate_hook(HOOK_EXEMPLO, "implantes")

        assert result["component"] == "hook"

    async def test_passed_true_quando_score_acima_do_threshold(self, validator):
        validator.claude.extract_json = AsyncMock(
            return_value=make_claude_validation_response(weighted_score=8.5)
        )

        result = await validator.validate_hook(HOOK_EXEMPLO, "implantes", min_score=7.0)

        assert result["passed"] is True
        assert result["score"] == 8.5

    async def test_passed_false_quando_score_abaixo_do_threshold(self, validator):
        validator.claude.extract_json = AsyncMock(
            return_value=make_claude_validation_response(weighted_score=5.5)
        )

        result = await validator.validate_hook(HOOK_EXEMPLO, "implantes", min_score=7.0)

        assert result["passed"] is False
        assert result["score"] == 5.5

    async def test_score_exatamente_no_threshold_passa(self, validator):
        """Score == min_score deve passar (>= não só >)."""
        validator.claude.extract_json = AsyncMock(
            return_value=make_claude_validation_response(weighted_score=7.0)
        )

        result = await validator.validate_hook(HOOK_EXEMPLO, "implantes", min_score=7.0)

        assert result["passed"] is True

    async def test_tokens_used_e_soma_de_input_e_output(self, validator):
        validator.claude.extract_json = AsyncMock(
            return_value=make_claude_validation_response(
                weighted_score=8.0, tokens_input=300, tokens_output=150
            )
        )

        result = await validator.validate_hook(HOOK_EXEMPLO, "implantes")

        assert result["tokens_used"] == 450

    async def test_min_score_reflete_threshold_configurado(self, validator):
        validator.claude.extract_json = AsyncMock(return_value=make_claude_validation_response(8.0))

        result = await validator.validate_hook(HOOK_EXEMPLO, "implantes", min_score=8.5)

        assert result["min_score"] == 8.5

    async def test_falha_de_parse_retorna_passed_false_e_score_zero(self, validator):
        """Se o Claude retorna JSON inválido, o validator deve degradar graciosamente."""
        validator.claude.extract_json = AsyncMock(return_value=make_failed_response())

        result = await validator.validate_hook(HOOK_EXEMPLO, "implantes")

        assert result["passed"] is False
        assert result["score"] == 0
        assert "error" in result


# ══════════════════════════════════════════════════════════════════════════════
# validate_full — consolidação dos 3 componentes
# ══════════════════════════════════════════════════════════════════════════════


class TestValidateFullConsolidacao:
    async def test_retorno_tem_todos_campos_obrigatorios(self, validator):
        validator.claude.extract_json = AsyncMock(return_value=make_claude_validation_response(8.0))

        result = await validator.validate_full(
            hook=HOOK_EXEMPLO,
            body=BODY_EXEMPLO,
            cta=CTA_EXEMPLO,
            specialty="implantes",
        )

        campos = [
            "all_passed",
            "overall_score",
            "validations",
            "feedback_summary",
            "total_tokens",
            "total_cost_usd",
        ]
        for campo in campos:
            assert campo in result, f"Campo obrigatório ausente: '{campo}'"

    async def test_validations_tem_os_3_componentes(self, validator):
        validator.claude.extract_json = AsyncMock(return_value=make_claude_validation_response(8.0))

        result = await validator.validate_full(
            hook=HOOK_EXEMPLO, body=BODY_EXEMPLO, cta=CTA_EXEMPLO, specialty="implantes"
        )

        assert "hook" in result["validations"]
        assert "body" in result["validations"]
        assert "cta" in result["validations"]

    async def test_all_passed_true_quando_os_3_passam(self, validator):
        validator.claude.extract_json = AsyncMock(
            return_value=make_claude_validation_response(weighted_score=8.5)
        )

        result = await validator.validate_full(
            hook=HOOK_EXEMPLO, body=BODY_EXEMPLO, cta=CTA_EXEMPLO, specialty="implantes"
        )

        assert result["all_passed"] is True

    async def test_all_passed_false_se_qualquer_um_falha(self, validator):
        """Se só o hook falha, all_passed deve ser False."""
        respostas = [
            make_claude_validation_response(5.0),  # hook falha (< 7.0)
            make_claude_validation_response(8.5),  # body passa
            make_claude_validation_response(8.0),  # cta passa
        ]
        validator.claude.extract_json = AsyncMock(side_effect=respostas)

        result = await validator.validate_full(
            hook=HOOK_EXEMPLO, body=BODY_EXEMPLO, cta=CTA_EXEMPLO, specialty="implantes"
        )

        assert result["all_passed"] is False

    async def test_overall_score_e_media_ponderada_35_40_25(self, validator):
        """
        Pesos: gancho 35%, corpo 40%, CTA 25%.
        Hook=8.0, Body=9.0, CTA=7.0
        Esperado: 8.0*0.35 + 9.0*0.40 + 7.0*0.25 = 2.80 + 3.60 + 1.75 = 8.15
        """
        respostas = [
            make_claude_validation_response(8.0),  # hook
            make_claude_validation_response(9.0),  # body
            make_claude_validation_response(7.0),  # cta
        ]
        validator.claude.extract_json = AsyncMock(side_effect=respostas)

        result = await validator.validate_full(
            hook=HOOK_EXEMPLO, body=BODY_EXEMPLO, cta=CTA_EXEMPLO, specialty="implantes"
        )

        assert result["overall_score"] == round(8.0 * 0.35 + 9.0 * 0.40 + 7.0 * 0.25, 1)

    async def test_overall_score_esta_entre_0_e_10(self, validator):
        validator.claude.extract_json = AsyncMock(return_value=make_claude_validation_response(8.0))

        result = await validator.validate_full(
            hook=HOOK_EXEMPLO, body=BODY_EXEMPLO, cta=CTA_EXEMPLO, specialty="implantes"
        )

        assert 0 <= result["overall_score"] <= 10

    async def test_total_tokens_e_soma_dos_3(self, validator):
        """total_tokens = tokens_hook + tokens_body + tokens_cta."""
        respostas = [
            make_claude_validation_response(8.0, tokens_input=300, tokens_output=150),
            make_claude_validation_response(8.0, tokens_input=400, tokens_output=200),
            make_claude_validation_response(8.0, tokens_input=250, tokens_output=120),
        ]
        validator.claude.extract_json = AsyncMock(side_effect=respostas)

        result = await validator.validate_full(
            hook=HOOK_EXEMPLO, body=BODY_EXEMPLO, cta=CTA_EXEMPLO, specialty="implantes"
        )

        expected = (300 + 150) + (400 + 200) + (250 + 120)
        assert result["total_tokens"] == expected

    async def test_feedback_summary_menciona_os_3_componentes(self, validator):
        validator.claude.extract_json = AsyncMock(return_value=make_claude_validation_response(8.0))

        result = await validator.validate_full(
            hook=HOOK_EXEMPLO, body=BODY_EXEMPLO, cta=CTA_EXEMPLO, specialty="implantes"
        )

        summary = result["feedback_summary"].upper()
        assert "HOOK" in summary
        assert "BODY" in summary
        assert "CTA" in summary

    async def test_feedback_summary_indica_status_de_cada_componente(self, validator):
        """feedback_summary deve indicar ✅ ou ❌ para cada componente."""
        respostas = [
            make_claude_validation_response(8.5),  # hook passa
            make_claude_validation_response(5.0),  # body falha
            make_claude_validation_response(8.0),  # cta passa
        ]
        validator.claude.extract_json = AsyncMock(side_effect=respostas)

        result = await validator.validate_full(
            hook=HOOK_EXEMPLO, body=BODY_EXEMPLO, cta=CTA_EXEMPLO, specialty="implantes"
        )

        assert "✅" in result["feedback_summary"]
        assert "❌" in result["feedback_summary"]

    async def test_total_cost_usd_e_soma_dos_3(self, validator):
        respostas = [
            make_claude_validation_response(8.0, cost_usd=0.002),
            make_claude_validation_response(8.0, cost_usd=0.003),
            make_claude_validation_response(8.0, cost_usd=0.0015),
        ]
        validator.claude.extract_json = AsyncMock(side_effect=respostas)

        result = await validator.validate_full(
            hook=HOOK_EXEMPLO, body=BODY_EXEMPLO, cta=CTA_EXEMPLO, specialty="implantes"
        )

        expected = round(0.002 + 0.003 + 0.0015, 6)
        assert result["total_cost_usd"] == expected

    async def test_claude_chamado_exatamente_3_vezes(self, validator):
        """validate_full deve chamar o Claude exatamente 3 vezes (hook + body + cta)."""
        validator.claude.extract_json = AsyncMock(return_value=make_claude_validation_response(8.0))

        await validator.validate_full(
            hook=HOOK_EXEMPLO, body=BODY_EXEMPLO, cta=CTA_EXEMPLO, specialty="implantes"
        )

        assert validator.claude.extract_json.call_count == 3
