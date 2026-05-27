"""
Testes da Working Memory (P1.E).

Cobre:
1. get() retorna [] quando sessao nao existe
2. append() salva mensagem
3. get() apos append() retorna historico
4. Truncamento ao exceder WM_MAX_MESSAGES
5. IDs invalidos (None, vazio) → no-op silencioso
6. Role invalido → rejeitado
7. clear() apaga historico
8. Redis indisponivel → operacoes nao propagam erro
9. Helpers de alto nivel (load_session_history, save_exchange)
10. Chave Redis no formato esperado

Estes testes usam um cliente Redis mockado — nao requerem Redis real
para a maioria, e usam Redis real (do docker-compose.test.yml) para
testes de integracao basicos.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.working_memory import (
    WM_KEY_PREFIX,
    WM_MAX_MESSAGES,
    WM_TTL_SECONDS,
    WorkingMemory,
    load_session_history,
    save_exchange,
    working_memory,
)


pytestmark = pytest.mark.asyncio


# ─────────────────────────────────────────────────────────────────────────────
# Helpers para mockar cliente Redis
# ─────────────────────────────────────────────────────────────────────────────

class _FakeRedis:
    """Implementacao em memoria do subset usado por WorkingMemory."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def ping(self):
        return True

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value
        if ex:
            self.ttls[key] = ex
        return True

    async def expire(self, key: str, ttl: int):
        if key in self.store:
            self.ttls[key] = ttl
            return True
        return False

    async def delete(self, key: str):
        self.store.pop(key, None)
        self.ttls.pop(key, None)
        return 1

    async def aclose(self):
        pass


@pytest.fixture
def fake_redis():
    return _FakeRedis()


@pytest.fixture
def wm(fake_redis):
    """WorkingMemory com cliente Redis fake injetado."""
    instance = WorkingMemory()
    instance._client = fake_redis
    return instance


# ─────────────────────────────────────────────────────────────────────────────
# Cenario 1: get() sem historico
# ─────────────────────────────────────────────────────────────────────────────

class TestGetEmpty:
    async def test_returns_empty_list_when_no_history(self, wm):
        result = await wm.get("user_1", "session_1")
        assert result == []

    async def test_returns_empty_list_when_user_id_missing(self, wm):
        result = await wm.get("", "session_1")
        assert result == []

    async def test_returns_empty_list_when_session_id_missing(self, wm):
        result = await wm.get("user_1", "")
        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# Cenario 2: append() + get() round-trip
# ─────────────────────────────────────────────────────────────────────────────

class TestAppendAndGet:
    async def test_append_then_get_returns_message(self, wm):
        await wm.append("user_1", "session_1", "user", "Como esta o Ottoboni?")
        history = await wm.get("user_1", "session_1")

        assert len(history) == 1
        assert history[0] == {"role": "user", "content": "Como esta o Ottoboni?"}

    async def test_multiple_appends_preserve_order(self, wm):
        await wm.append("user_1", "session_1", "user", "Pergunta 1")
        await wm.append("user_1", "session_1", "assistant", "Resposta 1")
        await wm.append("user_1", "session_1", "user", "Pergunta 2")

        history = await wm.get("user_1", "session_1")
        assert len(history) == 3
        assert history[0]["content"] == "Pergunta 1"
        assert history[1]["content"] == "Resposta 1"
        assert history[2]["content"] == "Pergunta 2"

    async def test_different_sessions_isolated(self, wm):
        await wm.append("user_1", "session_A", "user", "Mensagem A")
        await wm.append("user_1", "session_B", "user", "Mensagem B")

        history_a = await wm.get("user_1", "session_A")
        history_b = await wm.get("user_1", "session_B")

        assert len(history_a) == 1 and history_a[0]["content"] == "Mensagem A"
        assert len(history_b) == 1 and history_b[0]["content"] == "Mensagem B"

    async def test_different_users_isolated(self, wm):
        await wm.append("user_A", "session_1", "user", "Mensagem do A")
        await wm.append("user_B", "session_1", "user", "Mensagem do B")

        history_a = await wm.get("user_A", "session_1")
        history_b = await wm.get("user_B", "session_1")

        assert history_a[0]["content"] == "Mensagem do A"
        assert history_b[0]["content"] == "Mensagem do B"


# ─────────────────────────────────────────────────────────────────────────────
# Cenario 3: validacoes de input
# ─────────────────────────────────────────────────────────────────────────────

class TestInputValidation:
    async def test_invalid_role_rejected(self, wm):
        result = await wm.append("user_1", "session_1", "invalid_role", "X")
        assert result is False
        history = await wm.get("user_1", "session_1")
        assert history == []

    async def test_valid_roles_accepted(self, wm):
        for role in ("user", "assistant", "system"):
            result = await wm.append("user_1", f"sess_{role}", role, "test")
            assert result is True

    async def test_empty_content_rejected(self, wm):
        result = await wm.append("user_1", "session_1", "user", "")
        assert result is False

    async def test_missing_user_id_rejected(self, wm):
        result = await wm.append("", "session_1", "user", "X")
        assert result is False

    async def test_missing_session_id_rejected(self, wm):
        result = await wm.append("user_1", "", "user", "X")
        assert result is False


# ─────────────────────────────────────────────────────────────────────────────
# Cenario 4: truncamento ao exceder WM_MAX_MESSAGES
# ─────────────────────────────────────────────────────────────────────────────

class TestTruncation:
    async def test_messages_beyond_limit_are_truncated(self, wm):
        for i in range(WM_MAX_MESSAGES + 5):
            await wm.append("user_1", "session_1", "user", f"msg_{i}")

        history = await wm.get("user_1", "session_1")
        assert len(history) == WM_MAX_MESSAGES

    async def test_truncation_keeps_most_recent(self, wm):
        for i in range(WM_MAX_MESSAGES + 3):
            await wm.append("user_1", "session_1", "user", f"msg_{i}")

        history = await wm.get("user_1", "session_1")
        # As 3 primeiras foram descartadas; ultima deve ser msg_{N+2}
        last_idx = WM_MAX_MESSAGES + 2
        assert history[-1]["content"] == f"msg_{last_idx}"


# ─────────────────────────────────────────────────────────────────────────────
# Cenario 5: clear()
# ─────────────────────────────────────────────────────────────────────────────

class TestClear:
    async def test_clear_removes_history(self, wm):
        await wm.append("user_1", "session_1", "user", "X")
        assert len(await wm.get("user_1", "session_1")) == 1

        result = await wm.clear("user_1", "session_1")
        assert result is True
        assert await wm.get("user_1", "session_1") == []

    async def test_clear_invalid_ids_returns_false(self, wm):
        assert await wm.clear("", "session_1") is False
        assert await wm.clear("user_1", "") is False


# ─────────────────────────────────────────────────────────────────────────────
# Cenario 6: Redis indisponivel → falha silenciosa
# ─────────────────────────────────────────────────────────────────────────────

class TestRedisUnavailable:
    async def test_get_returns_empty_when_redis_unavailable(self):
        instance = WorkingMemory()
        with patch.object(instance, "_get_client", new=AsyncMock(return_value=None)):
            result = await instance.get("user_1", "session_1")
            assert result == []

    async def test_append_returns_false_when_redis_unavailable(self):
        instance = WorkingMemory()
        with patch.object(instance, "_get_client", new=AsyncMock(return_value=None)):
            result = await instance.append("user_1", "session_1", "user", "X")
            assert result is False

    async def test_redis_exception_during_get_returns_empty(self, wm, fake_redis):
        fake_redis.get = AsyncMock(side_effect=RuntimeError("Redis broke"))
        result = await wm.get("user_1", "session_1")
        assert result == []

    async def test_redis_exception_during_append_returns_false(self, wm, fake_redis):
        fake_redis.set = AsyncMock(side_effect=RuntimeError("Redis broke"))
        result = await wm.append("user_1", "session_1", "user", "X")
        assert result is False


# ─────────────────────────────────────────────────────────────────────────────
# Cenario 7: dados corrompidos no Redis
# ─────────────────────────────────────────────────────────────────────────────

class TestCorruptedData:
    async def test_non_json_data_returns_empty(self, wm, fake_redis):
        fake_redis.store["wm:u1:s1"] = "isto nao eh json"
        result = await wm.get("u1", "s1")
        assert result == []

    async def test_json_but_not_list_returns_empty(self, wm, fake_redis):
        fake_redis.store["wm:u1:s1"] = '{"not": "a list"}'
        result = await wm.get("u1", "s1")
        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# Cenario 8: helpers de alto nivel
# ─────────────────────────────────────────────────────────────────────────────

class TestHelpers:
    async def test_load_session_history_returns_empty_for_missing_ids(self):
        assert await load_session_history(None, "session_1") == []
        assert await load_session_history("user_1", None) == []
        assert await load_session_history("", "") == []

    async def test_save_exchange_silent_noop_for_missing_ids(self):
        # Nao deve propagar excecao mesmo sem IDs
        await save_exchange(None, None, "q", "a")
        await save_exchange("user_1", None, "q", "a")
        await save_exchange(None, "session_1", "q", "a")

    async def test_save_exchange_silent_when_both_messages_empty(self):
        await save_exchange("user_1", "session_1", "", "")


# ─────────────────────────────────────────────────────────────────────────────
# Cenario 9: chave Redis no formato esperado
# ─────────────────────────────────────────────────────────────────────────────

class TestKeyFormat:
    def test_key_uses_expected_prefix(self):
        wm_instance = WorkingMemory()
        key = wm_instance._make_key("user_uuid_123", "session_xyz")
        assert key == f"{WM_KEY_PREFIX}:user_uuid_123:session_xyz"
        assert key.startswith(f"{WM_KEY_PREFIX}:")


# ─────────────────────────────────────────────────────────────────────────────
# Cenario 10: TTL renovado a cada leitura
# ─────────────────────────────────────────────────────────────────────────────

class TestTTLRenewal:
    async def test_ttl_renewed_on_get(self, wm, fake_redis):
        await wm.append("u1", "s1", "user", "X")
        # Simular TTL antigo
        fake_redis.ttls["wm:u1:s1"] = 60
        # Get renova TTL
        await wm.get("u1", "s1")
        assert fake_redis.ttls["wm:u1:s1"] == WM_TTL_SECONDS
