"""
Villa — Reflection Loop (Autonomy Engine, Loop 2)

"O que o Villa deveria estar aprendendo agora?"

Apos uma acao significativa do Villa (tipicamente disparada pelo
ProactiveScanner, mas tambem aplicavel a qualquer evento relevante), o
ReflectionLoop faz uma analise retrospectiva estruturada:

    1. Por que isso aconteceu? (causa, nao sintoma)
    2. Era previsivel? O Villa deveria ter antecipado?
    3. O que fazer diferente nas proximas vezes?
    4. Que conhecimento novo isso gera — para este cliente E para a WebXP?

O resultado e persistido em duas camadas do Client OS:
    - Client OS do cliente: episodio (com outcome) + fatos derivados
    - Client OS institucional (slug=webxp_agency): insights transversais
      que valem para o nicho/operacao como um todo

Controle de custo:
    - So reflete sobre acoes significativas (severity warning/critical/opportunity)
    - Usa modelo "fast" (Haiku) para a triagem; "primary" (Sonnet) so quando
      o proprio Villa classifica o aprendizado como alto valor.

Defensivo: qualquer falha e logada e absorvida. Reflexao nunca derruba fluxo.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

WEBXP_AGENCY_SLUG = "webxp_agency"

# Severidades que merecem reflexao. Eventos "info" sao ruido — nao refletimos.
REFLECTABLE_SEVERITIES = {"warning", "critical", "opportunity"}

REFLECTION_PROMPT = """Voce e o Villa refletindo sobre um evento que acabou de acontecer na operacao da WebXP (agencia de performance odontologica).

EVENTO:
{event_summary}

CONTEXTO DO CLIENTE:
{client_context}

RESULTADO DA ACAO TOMADA:
{action_outcome}

Reflita de forma estruturada e honesta. Responda APENAS em JSON valido:
{{
    "causa_raiz": "por que isso aconteceu — a causa real, nao o sintoma",
    "era_previsivel": true/false,
    "como_evitar": "o que o Villa pode fazer para antecipar/evitar nas proximas vezes",
    "aprendizado_cliente": "conhecimento novo especifico DESTE cliente (1 frase objetiva)",
    "aprendizado_webxp": "conhecimento que vale para a WebXP/nicho como um todo, ou null se nao houver padrao transversal",
    "valor": "baixo|medio|alto",
    "fato_derivado": {{
        "categoria": "categoria curta (ex: campanhas, criativos, comportamento_cliente)",
        "chave": "chave_snake_case",
        "valor": "valor objetivo do aprendizado"
    }}
}}"""


@dataclass
class ReflectionResult:
    """Resultado de uma reflexao."""

    reflected: bool = False
    causa_raiz: str | None = None
    aprendizado_cliente: str | None = None
    aprendizado_webxp: str | None = None
    valor: str | None = None
    saved_client_os: bool = False
    saved_webxp_os: bool = False
    error: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


class ReflectionLoop:
    """
    Loop 2 do Autonomy Engine.

    Uso:
        loop = ReflectionLoop()
        result = await loop.reflect(
            db=db,
            client_slug="ottoboni",
            event_type="autonomy_cpl_spike",
            event_reason="CPL subiu 40%",
            severity="warning",
            action_outcome={"summary": "frequencia 3.5, criativo cansado"},
        )
    """

    def __init__(self, claude_client: Any | None = None):
        # Import tardio para evitar ciclo e facilitar mock em teste
        if claude_client is None:
            from integrations.anthropic_client import claude

            self.claude = claude
        else:
            self.claude = claude_client

    async def reflect(
        self,
        db: AsyncSession,
        client_slug: str,
        event_type: str,
        event_reason: str,
        severity: str,
        action_outcome: dict[str, Any] | None = None,
    ) -> ReflectionResult:
        """
        Reflete sobre um evento e persiste o aprendizado.

        Retorna ReflectionResult. Nunca lanca excecao.
        """
        result = ReflectionResult()

        # Filtro de relevancia — nao refletimos sobre ruido
        if severity not in REFLECTABLE_SEVERITIES:
            logger.debug(
                "ReflectionLoop: severity '%s' nao reflectavel, pulando.", severity
            )
            return result

        try:
            client_context = await self._load_client_context(db, client_slug)
        except Exception as e:
            logger.debug("ReflectionLoop: contexto do cliente indisponivel: %s", e)
            client_context = "(sem contexto disponivel)"

        event_summary = f"[{severity}] {event_type}: {event_reason}"
        outcome_str = self._format_outcome(action_outcome)

        # ── Triagem com modelo fast ──
        try:
            reflection = await self._ask_reflection(
                event_summary=event_summary,
                client_context=client_context,
                action_outcome=outcome_str,
                model="fast",
            )
        except Exception as e:
            logger.warning("ReflectionLoop: falha na triagem: %s", e)
            result.error = f"triage: {e}"
            return result

        if reflection is None:
            result.error = "parse_failed"
            return result

        valor = (reflection.get("valor") or "baixo").lower()

        # ── Se alto valor, refaz com modelo primary para refinar ──
        if valor == "alto":
            try:
                refined = await self._ask_reflection(
                    event_summary=event_summary,
                    client_context=client_context,
                    action_outcome=outcome_str,
                    model="primary",
                )
                if refined is not None:
                    reflection = refined
                    valor = (reflection.get("valor") or valor).lower()
            except Exception as e:
                logger.debug("ReflectionLoop: refino primary falhou, usando triagem: %s", e)

        result.reflected = True
        result.causa_raiz = reflection.get("causa_raiz")
        result.aprendizado_cliente = reflection.get("aprendizado_cliente")
        result.aprendizado_webxp = reflection.get("aprendizado_webxp")
        result.valor = valor
        result.extras = reflection

        # ── Persistir no Client OS do cliente ──
        try:
            result.saved_client_os = await self._save_to_client_os(
                db, client_slug, event_type, event_reason, severity, reflection
            )
        except Exception as e:
            logger.warning("ReflectionLoop: falha salvando no Client OS do cliente: %s", e)

        # ── Persistir insight transversal no Client OS institucional ──
        aprendizado_webxp = reflection.get("aprendizado_webxp")
        if aprendizado_webxp and str(aprendizado_webxp).lower() not in ("null", "none", ""):
            try:
                result.saved_webxp_os = await self._save_to_webxp_os(
                    db, client_slug, event_type, reflection
                )
            except Exception as e:
                logger.warning("ReflectionLoop: falha salvando no Client OS WebXP: %s", e)

        return result

    # ── Internos ──

    async def _ask_reflection(
        self,
        event_summary: str,
        client_context: str,
        action_outcome: str,
        model: str,
    ) -> dict[str, Any] | None:
        """Pede a reflexao estruturada ao Claude e faz parse do JSON."""
        prompt = REFLECTION_PROMPT.format(
            event_summary=event_summary,
            client_context=client_context[:1500],
            action_outcome=action_outcome[:1000],
        )
        parsed = await self.claude.extract_json(message=prompt, model=model)
        data = parsed.get("data")
        if not isinstance(data, dict):
            return None
        return data

    async def _load_client_context(self, db: AsyncSession, client_slug: str) -> str:
        """Carrega o narrative do Client OS do cliente como contexto."""
        from memory.client_os import ClientOS

        cos = await ClientOS.for_slug(db, client_slug)
        narrative = await cos.narrative()
        return narrative or "(sem estado registrado)"

    async def _save_to_client_os(
        self,
        db: AsyncSession,
        client_slug: str,
        event_type: str,
        event_reason: str,
        severity: str,
        reflection: dict[str, Any],
    ) -> bool:
        """Salva episodio + fato derivado no Client OS do cliente."""
        from memory.client_os import ClientOS

        cos = await ClientOS.for_slug(db, client_slug)

        outcome = "negative" if severity in ("warning", "critical") else "neutral"

        await cos.record_episode(
            event_type,
            f"{event_reason} | Causa: {reflection.get('causa_raiz', 'n/d')}",
            details={
                "causa_raiz": reflection.get("causa_raiz"),
                "era_previsivel": reflection.get("era_previsivel"),
                "como_evitar": reflection.get("como_evitar"),
                "aprendizado": reflection.get("aprendizado_cliente"),
            },
            outcome=outcome,
            module_source="reflection_loop",
        )

        fato = reflection.get("fato_derivado")
        if isinstance(fato, dict) and fato.get("categoria") and fato.get("chave"):
            await cos.upsert_fact(
                str(fato["categoria"]),
                str(fato["chave"]),
                fato.get("valor"),
                confidence=0.7,  # aprendizado autonomo: confianca media
            )
        return True

    async def _save_to_webxp_os(
        self,
        db: AsyncSession,
        origin_client_slug: str,
        event_type: str,
        reflection: dict[str, Any],
    ) -> bool:
        """Salva insight transversal no Client OS institucional (webxp_agency)."""
        from memory.client_os import ClientOS

        cos = await ClientOS.for_slug(db, WEBXP_AGENCY_SLUG)

        await cos.record_episode(
            f"insight_{event_type}",
            str(reflection.get("aprendizado_webxp")),
            details={
                "origem_cliente": origin_client_slug,
                "causa_raiz": reflection.get("causa_raiz"),
                "valor": reflection.get("valor"),
            },
            outcome="positive",  # aprendizado e sempre ganho
            module_source="reflection_loop",
        )
        return True

    @staticmethod
    def _format_outcome(action_outcome: dict[str, Any] | None) -> str:
        if not action_outcome:
            return "(sem resultado registrado)"
        try:
            return json.dumps(action_outcome, ensure_ascii=False)[:1000]
        except Exception:
            return str(action_outcome)[:1000]


# ── Instancia global ──
# Criada normalmente via __init__ — self.claude e resolvido no momento
# da importacao do modulo (lazy import interno ao __init__).
reflection_loop = ReflectionLoop()


__all__ = [
    "ReflectionLoop",
    "ReflectionResult",
    "reflection_loop",
    "WEBXP_AGENCY_SLUG",
    "REFLECTABLE_SEVERITIES",
]
