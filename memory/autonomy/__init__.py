"""
Villa — Autonomy Engine (P2.A)

Dois loops de autonomia:

- ProactiveScanner (Loop 1): "O que o Villa deveria estar fazendo agora?"
  Varre clientes ativos, detecta anomalias/oportunidades e dispara acoes
  via orchestrator.handle_event(). Reativo a estado, nao a comando.

- ReflectionLoop (Loop 2): "O que o Villa deveria estar aprendendo agora?"
  Apos acoes significativas, faz analise retrospectiva (por que aconteceu,
  era previsivel, o que fazer diferente) e salva conhecimento derivado no
  Client OS do cliente e no Client OS institucional (slug=webxp_agency).

Ambos sao defensivos: qualquer falha e absorvida e logada, nunca derruba
o scheduler nem outros fluxos.
"""

from memory.autonomy.proactive_scanner import (
    ProactiveScanner,
    ScanResult,
    proactive_scanner,
)
from memory.autonomy.reflection_loop import (
    ReflectionLoop,
    ReflectionResult,
    reflection_loop,
)

WEBXP_AGENCY_SLUG = "webxp_agency"

__all__ = [
    "ProactiveScanner",
    "ScanResult",
    "proactive_scanner",
    "ReflectionLoop",
    "ReflectionResult",
    "reflection_loop",
    "WEBXP_AGENCY_SLUG",
]
