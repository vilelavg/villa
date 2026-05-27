"""
Villa — Working Memory
Memoria de curto prazo por sessao, armazenada em Redis.

Diferenca em relacao ao Client OS:
- Client OS: contexto de longo prazo do CLIENTE (fatos, episodios, objetivos).
- Working Memory: contexto de curto prazo da SESSAO atual do USUARIO.

A Working Memory permite que o Villa "se lembre" do que foi discutido nas
ultimas mensagens da mesma sessao, sem ter que repetir contexto.

Chave Redis: wm:{user_id}:{session_id}
Valor: lista JSON de mensagens [{role: "user"|"assistant", content: str}]
TTL: 2 horas (renovado a cada acesso)

Comportamento defensivo: qualquer falha de Redis (indisponivel, timeout,
serializacao) e absorvida silenciosamente. Working Memory nunca derruba
um comando — no maximo, o Villa age sem memoria de sessao.
"""

from __future__ import annotations

import json
import logging

import redis.asyncio as aioredis

from core.config import settings

logger = logging.getLogger(__name__)

# ── Constantes ──
WM_TTL_SECONDS = 2 * 60 * 60  # 2 horas
WM_MAX_MESSAGES = 20  # mantem apenas as 20 ultimas mensagens da sessao
WM_KEY_PREFIX = "wm"  # prefixo no Redis


class WorkingMemory:
    """
    Wrapper sobre Redis para armazenar historico de sessao.

    Uso:
        wm = WorkingMemory()
        history = await wm.get("user_uuid", "session_xyz")
        await wm.append("user_uuid", "session_xyz", "user", "Como esta o Ottoboni?")
        await wm.append("user_uuid", "session_xyz", "assistant", "CPL atual...")
    """

    def __init__(self, redis_url: str | None = None):
        self._redis_url = redis_url or settings.redis_url
        self._client: aioredis.Redis | None = None

    async def _get_client(self) -> aioredis.Redis | None:
        """Retorna cliente Redis lazily. Retorna None se conexao falhar."""
        if self._client is not None:
            return self._client
        try:
            self._client = aioredis.from_url(
                self._redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2.0,
                socket_timeout=2.0,
            )
            # Smoke test — se Redis nao responde, log debug e cai pra None
            await self._client.ping()
            return self._client
        except Exception as e:
            logger.debug("Working Memory: Redis indisponivel (%s). Operando sem memoria.", e)
            self._client = None
            return None

    @staticmethod
    def _make_key(user_id: str, session_id: str) -> str:
        return f"{WM_KEY_PREFIX}:{user_id}:{session_id}"

    async def get(self, user_id: str, session_id: str) -> list[dict[str, str]]:
        """
        Retorna o historico da sessao como lista [{role, content}].
        Lista vazia se nao houver historico ou se Redis falhar.
        """
        if not user_id or not session_id:
            return []

        client = await self._get_client()
        if client is None:
            return []

        key = self._make_key(user_id, session_id)
        try:
            raw = await client.get(key)
            if not raw:
                return []
            data = json.loads(raw)
            if not isinstance(data, list):
                logger.debug("WM key %s contem tipo invalido (%s), retornando vazio", key, type(data))
                return []
            # Renova TTL a cada leitura — sessao ativa nao expira
            await client.expire(key, WM_TTL_SECONDS)
            return data
        except Exception as e:
            logger.debug("WM get falhou para key %s: %s", key, e)
            return []

    async def append(
        self,
        user_id: str,
        session_id: str,
        role: str,
        content: str,
    ) -> bool:
        """
        Adiciona uma mensagem ao historico da sessao.
        Trunca para WM_MAX_MESSAGES mensagens (mantem as mais recentes).

        Returns:
            True se salvou com sucesso, False caso contrario (nao propaga erro).
        """
        if not user_id or not session_id:
            return False
        if role not in ("user", "assistant", "system"):
            logger.debug("WM append: role invalido '%s'", role)
            return False
        if not content:
            return False

        client = await self._get_client()
        if client is None:
            return False

        try:
            current = await self.get(user_id, session_id)
            current.append({"role": role, "content": content})

            # Trunca mantendo as ultimas WM_MAX_MESSAGES
            if len(current) > WM_MAX_MESSAGES:
                current = current[-WM_MAX_MESSAGES:]

            key = self._make_key(user_id, session_id)
            await client.set(key, json.dumps(current), ex=WM_TTL_SECONDS)
            return True
        except Exception as e:
            logger.debug("WM append falhou: %s", e)
            return False

    async def clear(self, user_id: str, session_id: str) -> bool:
        """Apaga o historico da sessao. Util para 'esquecer' contexto."""
        if not user_id or not session_id:
            return False

        client = await self._get_client()
        if client is None:
            return False

        try:
            key = self._make_key(user_id, session_id)
            await client.delete(key)
            return True
        except Exception as e:
            logger.debug("WM clear falhou: %s", e)
            return False

    async def close(self) -> None:
        """Fecha conexao com Redis. Chamado no shutdown da app."""
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None


# ── Instancia global ──
working_memory = WorkingMemory()


# ── Helpers de conveniencia ──

async def load_session_history(user_id: str | None, session_id: str | None) -> list[dict[str, str]]:
    """
    Helper de alto nivel: retorna historico se ambos IDs presentes,
    [] caso contrario. Nao propaga excecoes.
    """
    if not user_id or not session_id:
        return []
    return await working_memory.get(user_id, session_id)


async def save_exchange(
    user_id: str | None,
    session_id: str | None,
    user_message: str,
    assistant_response: str,
) -> None:
    """
    Helper de alto nivel: salva um par (user, assistant) na sessao.
    Silently no-op se IDs ausentes ou Redis indisponivel.
    """
    if not user_id or not session_id:
        return
    if not user_message and not assistant_response:
        return
    if user_message:
        await working_memory.append(user_id, session_id, "user", user_message)
    if assistant_response:
        await working_memory.append(user_id, session_id, "assistant", assistant_response)


# ── Constantes exportadas para testes ──
__all__ = [
    "WorkingMemory",
    "working_memory",
    "load_session_history",
    "save_exchange",
    "WM_TTL_SECONDS",
    "WM_MAX_MESSAGES",
    "WM_KEY_PREFIX",
]
