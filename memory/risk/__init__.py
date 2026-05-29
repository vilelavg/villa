"""
Villa — Risk Engine (P2.C)

Camada de avaliacao de risco antes de cada acao autonoma do Villa.
Responde: "Posso agir agora ou devo confirmar com Caio?"

Design:
- Regras declarativas (sem LLM) cobrem 95% dos casos a custo zero
- LLM (Haiku) so para refinamento em casos medium com historico
- Fail-open: qualquer falha retorna approved=True para nao travar o Villa
- Integrado no orchestrator.handle_event() antes de module.execute()
"""

from memory.risk.risk_engine import (
    RiskAssessment,
    RiskEngine,
    risk_engine,
)

__all__ = [
    "RiskAssessment",
    "RiskEngine",
    "risk_engine",
]
