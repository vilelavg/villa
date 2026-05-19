"""
Villa — Validadores do M01 (Roteiros)
Tripla validação automática: Gancho, Corpo e CTA.

Cada validador:
    1. Envia o componente ao Claude com prompt específico
    2. Claude avalia com critérios ponderados e retorna JSON
    3. Se score < threshold, retorna feedback + sugestão de melhoria
    4. O módulo decide se refina automaticamente ou entrega para revisão

O threshold padrão é 7.0/10 para cada componente.
Configurável por cliente em clients.config.thresholds.
"""

import json
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from integrations.anthropic_client import AnthropicClient, claude
from modules.m01_roteiros.prompts import (
    HOOK_VALIDATION_PROMPT,
    BODY_VALIDATION_PROMPT,
    CTA_VALIDATION_PROMPT,
)


class RoteiroValidator:
    """
    Executa a tripla validação de um roteiro.
    
    Uso:
        validator = RoteiroValidator()
        result = await validator.validate_full(
            hook="23 implantes em 1 dia. Isso é possível?",
            body="O Dr. Ottoboni desenvolveu uma técnica...",
            cta="Link na bio para agendar sua avaliação gratuita",
            specialty="implantes",
        )
        
        if result["all_passed"]:
            # Roteiro aprovado
        else:
            # Usar result["feedback"] para refinar
    """

    def __init__(self, client: Optional[AnthropicClient] = None):
        self.claude = client or claude

    async def validate_hook(
        self,
        hook: str,
        specialty: str,
        min_score: float = 7.0,
    ) -> dict:
        """
        Valida o gancho (primeiros 3 segundos).
        
        Critérios ponderados:
            - Pattern Interrupt (peso 3)
            - Especificidade (peso 3)
            - Curiosidade (peso 2)
            - Anti-clichê (peso 2)
        """
        prompt = HOOK_VALIDATION_PROMPT.format(
            hook=hook,
            specialty=specialty,
        )

        response = await self.claude.extract_json(
            message=prompt,
            model="primary",
        )

        result = response.get("data")
        if not result:
            return {
                "component": "hook",
                "passed": False,
                "score": 0,
                "error": "Falha ao parsear resposta da validação",
                "raw": response.get("raw_text", ""),
                "tokens_used": response.get("tokens_input", 0) + response.get("tokens_output", 0),
            }

        score = result.get("weighted_score", 0)
        passed = score >= min_score

        return {
            "component": "hook",
            "passed": passed,
            "score": score,
            "scores_detail": result.get("scores", {}),
            "feedback": result.get("feedback", ""),
            "suggestion": result.get("suggestion", ""),
            "min_score": min_score,
            "tokens_used": response.get("tokens_input", 0) + response.get("tokens_output", 0),
            "cost_usd": response.get("cost_usd", 0),
        }

    async def validate_body(
        self,
        body: str,
        hook: str,
        specialty: str,
        min_score: float = 7.0,
    ) -> dict:
        """
        Valida o corpo (persuasão).
        
        Critérios ponderados:
            - Framework de persuasão (peso 3)
            - Prova e autoridade (peso 3)
            - Fluxo narrativo (peso 2)
            - Conexão com gancho (peso 2)
        """
        prompt = BODY_VALIDATION_PROMPT.format(
            body=body,
            hook=hook,
            specialty=specialty,
        )

        response = await self.claude.extract_json(
            message=prompt,
            model="primary",
        )

        result = response.get("data")
        if not result:
            return {
                "component": "body",
                "passed": False,
                "score": 0,
                "error": "Falha ao parsear resposta da validação",
                "tokens_used": response.get("tokens_input", 0) + response.get("tokens_output", 0),
            }

        score = result.get("weighted_score", 0)
        passed = score >= min_score

        return {
            "component": "body",
            "passed": passed,
            "score": score,
            "scores_detail": result.get("scores", {}),
            "feedback": result.get("feedback", ""),
            "suggestion": result.get("suggestion", ""),
            "min_score": min_score,
            "tokens_used": response.get("tokens_input", 0) + response.get("tokens_output", 0),
            "cost_usd": response.get("cost_usd", 0),
        }

    async def validate_cta(
        self,
        cta: str,
        hook: str,
        body: str,
        specialty: str,
        min_score: float = 7.0,
    ) -> dict:
        """
        Valida a CTA (chamada para ação).
        
        Critérios ponderados:
            - Ação única (peso 3)
            - Baixo atrito (peso 3)
            - Urgência/escassez (peso 2)
            - Rastreabilidade (peso 2)
        """
        # Resumir body para não estourar contexto
        body_summary = body[:300] + "..." if len(body) > 300 else body

        prompt = CTA_VALIDATION_PROMPT.format(
            cta=cta,
            hook=hook,
            body_summary=body_summary,
            specialty=specialty,
        )

        response = await self.claude.extract_json(
            message=prompt,
            model="primary",
        )

        result = response.get("data")
        if not result:
            return {
                "component": "cta",
                "passed": False,
                "score": 0,
                "error": "Falha ao parsear resposta da validação",
                "tokens_used": response.get("tokens_input", 0) + response.get("tokens_output", 0),
            }

        score = result.get("weighted_score", 0)
        passed = score >= min_score

        return {
            "component": "cta",
            "passed": passed,
            "score": score,
            "scores_detail": result.get("scores", {}),
            "feedback": result.get("feedback", ""),
            "suggestion": result.get("suggestion", ""),
            "min_score": min_score,
            "tokens_used": response.get("tokens_input", 0) + response.get("tokens_output", 0),
            "cost_usd": response.get("cost_usd", 0),
        }

    async def validate_full(
        self,
        hook: str,
        body: str,
        cta: str,
        specialty: str,
        min_hook_score: float = 7.0,
        min_body_score: float = 7.0,
        min_cta_score: float = 7.0,
    ) -> dict:
        """
        Tripla validação completa.
        Executa as 3 validações e consolida resultado.
        
        Returns:
            Dict com:
                all_passed: bool — se os 3 componentes passaram
                overall_score: float — média ponderada (gancho 35%, corpo 40%, CTA 25%)
                validations: dict — resultado de cada componente
                feedback_summary: str — resumo de todos os feedbacks
                total_tokens: int — tokens consumidos na validação
                total_cost: float — custo em USD
        """
        # Executar as 3 validações
        hook_result = await self.validate_hook(hook, specialty, min_hook_score)
        body_result = await self.validate_body(body, hook, specialty, min_body_score)
        cta_result = await self.validate_cta(cta, hook, body, specialty, min_cta_score)

        # Consolidar
        all_passed = (
            hook_result["passed"]
            and body_result["passed"]
            and cta_result["passed"]
        )

        # Média ponderada: gancho 35%, corpo 40%, CTA 25%
        overall_score = (
            hook_result["score"] * 0.35
            + body_result["score"] * 0.40
            + cta_result["score"] * 0.25
        )

        # Resumo de feedback
        feedback_parts = []
        for r in [hook_result, body_result, cta_result]:
            component = r["component"].upper()
            status = "✅" if r["passed"] else "❌"
            feedback_parts.append(
                f"{status} {component} ({r['score']:.1f}/10): {r['feedback']}"
            )

        total_tokens = sum(r.get("tokens_used", 0) for r in [hook_result, body_result, cta_result])
        total_cost = sum(r.get("cost_usd", 0) for r in [hook_result, body_result, cta_result])

        return {
            "all_passed": all_passed,
            "overall_score": round(overall_score, 1),
            "validations": {
                "hook": hook_result,
                "body": body_result,
                "cta": cta_result,
            },
            "feedback_summary": "\n".join(feedback_parts),
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
        }
