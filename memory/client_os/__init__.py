"""
Client OS — Memória episódica e estado narrativo vivo por cliente.

Camada acima do `memory/feedback_loop.py` (que faz RAG semântico sobre
interações passadas). O Client OS mantém estado *estruturado* e *queryable*
por cliente: fatos estáveis, episódios temporais, preferências observadas,
pendências abertas e objetivos ativos.

Auto-carregado pelo VillaCore antes de qualquer ação de módulo (Fase 1.B).

Uso típico:
    from memory.client_os import ClientOS

    os_ = await ClientOS.for_slug(db, "ottoboni")
    narrative = await os_.narrative()
    await os_.record_episode("campaign_paused", "Pausei X por CPL alto", outcome="positive")
"""
from .exceptions import ClientNotFoundError, ClientOSError
from .narrative import compile_narrative
from .state import ClientOS

__all__ = [
    "ClientOS",
    "ClientOSError",
    "ClientNotFoundError",
    "compile_narrative",
]
