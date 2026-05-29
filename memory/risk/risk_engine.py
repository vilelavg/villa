"""
Villa — Risk Engine (P2.C)

Avalia o risco de uma acao antes que o orquestrador a execute. E a ultima
barreira entre o Autonomy Engine e ações reais no mundo (Kommo, Meta, Ads).

Como funciona:
    1. Regras declarativas classificam o risco em low/medium/high
    2. Se medium e ha contexto suficiente, refinamento opcional via Haiku
    3. Retorna RiskAssessment com approved + requires_confirmation

Fail-open: qualquer excecao retorna approved=True. O motivo e simples — se o
Risk Engine quebrar, e melhor o Villa agir (e talvez errar pequeno) do que
ficar paralisado. Erros sao logados para auditoria.

Categorias de modulos:
    READ_ONLY: leitura/analise pura (M02 relatorios, M04 analise de campanhas).
               Risco baixo por design.
    WRITE_INTERNAL: escreve no banco interno (M01 roteiros, M11 hipoteses,
                    M12 alertas). Risco baixo a medio.
    WRITE_EXTERNAL: acoes externas (Kommo, Meta, Google Ads, envio de
                    mensagens). Risco medio a alto — exige cuidado.

A categoria WRITE_EXTERNAL ainda nao existe em produção (Villa nao executa
acoes externas hoje), mas o Risk Engine ja esta preparado para quando o
P3 (Kommo Operator + Meta Manager) entrar em cena.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ── Categorizacao de modulos por tipo de acao ──
# Mapeia ModuleCode (string) para a categoria de impacto.
# Ausente do dict => categoria default WRITE_INTERNAL (conservador).

READ_ONLY_MODULES = {
    "m02_relatorios",
    "m04_campanhas",      # analise, nao executa nada na Meta
    "m09_arquivos",
    "m14_suporte_mari",
    "m15_monitor_smooth",
}

WRITE_INTERNAL_MODULES = {
    "m01_roteiros",
    "m03_qualificacao",
    "m05_agendamento",
    "m06_atendimento",
    "m07_retroalimentacao",
    "m08_onboarding",
    "m10_smooth",
    "m11_hipoteses",
    "m12_alertas",
}

WRITE_EXTERNAL_MODULES: set[str] = set()  # Reservado para P3


# ── Eventos autonomy conhecidos (do ProactiveScanner) ──
KNOWN_AUTONOMY_EVENTS = {
    "autonomy_cpl_spike",
    "autonomy_frequency_high",
    "autonomy_stale_creatives",
    "autonomy_low_health",
}


@dataclass
class RiskAssessment:
    """Resultado da avaliacao de risco para uma acao."""

    approved: bool                              # Pode executar?
    requires_confirmation: bool = False         # Deve notificar Caio antes?
    risk_level: str = "low"                     # "low" | "medium" | "high"
    reason: str = ""                            # Por que essa decisao
    used_llm: bool = False                      # Se passou pelo refinamento LLM
    extras: dict[str, Any] = field(default_factory=dict)


class RiskEngine:
    """
    Avalia risco antes de cada acao do Villa.

    Uso (dentro do orquestrador):
        assessment = await risk_engine.evaluate(
            event_type="autonomy_cpl_spike",
            module_code="m04_campanhas",
            severity="critical",
            client_slug="ottoboni",
            db=db,
        )
        if not assessment.approved:
            # nao executa o modulo
            ...
    """

    def __init__(self, claude_client: Any | None = None):
        self._claude = claude_client  # Lazy resolved no primeiro uso

    async def evaluate(
        self,
        event_type: str,
        module_code: str,
        severity: str = "info",
        client_slug: str | None = None,
        db: AsyncSession | None = None,
        action_description: str | None = None,
    ) -> RiskAssessment:
        """
        Avalia o risco de uma acao. Nunca lanca excecao (fail-open).

        Args:
            event_type: tipo do evento (ex: "autonomy_cpl_spike", "inlead_new_lead")
            module_code: codigo do modulo que vai executar (ex: "m04_campanhas")
            severity: severidade do evento ("info" | "opportunity" | "warning" | "critical")
            client_slug: cliente alvo (opcional, usado para contexto LLM)
            db: sessao do banco (opcional, necessario para refinamento LLM)
            action_description: descricao curta da acao (opcional)

        Returns:
            RiskAssessment com approved e demais campos.
        """
        try:
            assessment = self._evaluate_rules(event_type, module_code, severity)

            # Refinamento opcional via LLM apenas para casos medium com contexto
            if (
                assessment.risk_level == "medium"
                and client_slug
                and db is not None
                and not assessment.used_llm
            ):
                try:
                    refined = await self._refine_with_llm(
                        assessment=assessment,
                        event_type=event_type,
                        module_code=module_code,
                        severity=severity,
                        client_slug=client_slug,
                        db=db,
                        action_description=action_description,
                    )
                    if refined is not None:
                        return refined
                except Exception as e:
                    logger.debug("RiskEngine: refinamento LLM falhou: %s", e)

            return assessment

        except Exception as e:
            # Fail-open: qualquer falha permite a acao (logada)
            logger.warning(
                "RiskEngine: falha na avaliacao, permitindo acao (fail-open): %s", e
            )
            return RiskAssessment(
                approved=True,
                risk_level="low",
                reason=f"fail_open: {e}",
                extras={"fail_open": True},
            )

    # ── Avaliacao por regras (sem LLM, custo zero) ──

    def _evaluate_rules(
        self,
        event_type: str,
        module_code: str,
        severity: str,
    ) -> RiskAssessment:
        """
        Aplica regras declarativas. Ordem importa — primeira regra que bate vence.
        """
        category = self._module_category(module_code)

        # Regra 1: WRITE_EXTERNAL sempre exige confirmacao (proativo para P3)
        if category == "write_external":
            return RiskAssessment(
                approved=False,
                requires_confirmation=True,
                risk_level="high",
                reason=f"modulo '{module_code}' executa acao externa — confirmacao obrigatoria",
            )

        # Regra 2: severity critical sempre exige confirmacao,
        # independente da categoria do modulo
        if severity == "critical":
            return RiskAssessment(
                approved=False,
                requires_confirmation=True,
                risk_level="high",
                reason=f"severidade critical em '{event_type}' — confirmacao obrigatoria",
            )

        # Regra 3: READ_ONLY sempre liberado (leitura nao causa dano)
        if category == "read_only":
            return RiskAssessment(
                approved=True,
                risk_level="low",
                reason=f"modulo '{module_code}' e read-only",
            )

        # Regra 4: severity opportunity sempre liberado (sem urgencia, sem dano)
        if severity == "opportunity":
            return RiskAssessment(
                approved=True,
                risk_level="low",
                reason="oportunidade detectada — acao segura",
            )

        # Regra 5: evento desconhecido (nao mapeado) com severity warning+
        # vai para medium para forcar avaliacao mais cuidadosa
        if event_type.startswith("autonomy_") and event_type not in KNOWN_AUTONOMY_EVENTS:
            return RiskAssessment(
                approved=True,
                requires_confirmation=False,
                risk_level="medium",
                reason=f"evento autonomy nao mapeado: '{event_type}'",
            )

        # Regra 6: WRITE_INTERNAL com warning — risco medio mas liberado
        if category == "write_internal" and severity == "warning":
            return RiskAssessment(
                approved=True,
                risk_level="medium",
                reason=f"modulo '{module_code}' escreve internamente com severidade {severity}",
            )

        # Default: liberado com risco baixo
        return RiskAssessment(
            approved=True,
            risk_level="low",
            reason="acao padrao — risco baixo",
        )

    # ── Refinamento via LLM (apenas medium com contexto) ──

    async def _refine_with_llm(
        self,
        assessment: RiskAssessment,
        event_type: str,
        module_code: str,
        severity: str,
        client_slug: str,
        db: AsyncSession,
        action_description: str | None,
    ) -> RiskAssessment | None:
        """
        Refina avaliacao medium consultando o Client OS do cliente.
        Usa Haiku (modelo fast) para manter custo baixo.

        Retorna None se nao conseguir refinar (mantem assessment original).
        """
        # Lazy import do claude
        if self._claude is None:
            try:
                from integrations.anthropic_client import claude

                self._claude = claude
            except Exception:
                return None

        # Carrega contexto do cliente
        try:
            from memory.client_os import ClientOS

            cos = await ClientOS.for_slug(db, client_slug)
            narrative = await cos.narrative()
        except Exception:
            return None

        if not narrative or len(narrative.strip()) < 50:
            # Sem contexto util, mantem assessment original
            return None

        prompt = f"""Voce e o Risk Engine do Villa avaliando se uma acao autonoma e segura.

ACAO:
- Evento: {event_type}
- Modulo: {module_code}
- Severidade: {severity}
- Descricao: {action_description or "(nao especificada)"}
- Cliente: {client_slug}

CONTEXTO DO CLIENTE:
{narrative[:1500]}

AVALIACAO PRELIMINAR: risco {assessment.risk_level} ({assessment.reason})

Considere o historico do cliente. Existe algo que indique que essa acao
deveria exigir confirmacao humana? Responda APENAS em JSON valido:
{{
    "approved": true/false,
    "requires_confirmation": true/false,
    "risk_level": "low|medium|high",
    "reason": "frase curta justificando"
}}"""

        try:
            parsed = await self._claude.extract_json(message=prompt, model="fast")
            data = parsed.get("data")
            if not isinstance(data, dict):
                return None

            return RiskAssessment(
                approved=bool(data.get("approved", assessment.approved)),
                requires_confirmation=bool(
                    data.get("requires_confirmation", assessment.requires_confirmation)
                ),
                risk_level=str(data.get("risk_level", assessment.risk_level)),
                reason=str(data.get("reason", assessment.reason)),
                used_llm=True,
                extras={"original_assessment": assessment.reason},
            )
        except Exception:
            return None

    # ── Helpers ──

    @staticmethod
    def _module_category(module_code: str) -> str:
        """Retorna a categoria do modulo: read_only | write_internal | write_external."""
        if module_code in READ_ONLY_MODULES:
            return "read_only"
        if module_code in WRITE_EXTERNAL_MODULES:
            return "write_external"
        # Default conservador: write_internal
        return "write_internal"


# ── Instancia global ──
risk_engine = RiskEngine()


__all__ = [
    "RiskAssessment",
    "RiskEngine",
    "risk_engine",
    "READ_ONLY_MODULES",
    "WRITE_INTERNAL_MODULES",
    "WRITE_EXTERNAL_MODULES",
    "KNOWN_AUTONOMY_EVENTS",
]
