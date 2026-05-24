"""
Villa — Rate Limiting
Limita requisições por usuário/IP usando Redis.
Protege a API contra abuso e controla custo da Anthropic API.
"""


import redis.asyncio as aioredis
from fastapi import HTTPException, Request, status

from core.config import settings


class RateLimiter:
    """
    Rate limiter baseado em Redis com sliding window.
    
    Uso como dependency:
        limiter = RateLimiter(max_requests=60, window_seconds=60)
        
        @router.post("/command")
        async def command(request: Request, _=Depends(limiter)):
            ...
    """

    def __init__(
        self,
        max_requests: int = 60,
        window_seconds: int = 60,
        key_prefix: str = "ratelimit",
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.key_prefix = key_prefix
        self._redis: aioredis.Redis | None = None

    async def _get_redis(self) -> aioredis.Redis:
        """Obtém conexão com Redis (lazy initialization)."""
        if self._redis is None:
            self._redis = aioredis.from_url(
                settings.redis_url,
                decode_responses=True,
            )
        return self._redis

    def _get_key(self, identifier: str) -> str:
        """Monta a chave Redis para um identificador."""
        return f"{self.key_prefix}:{identifier}"

    async def _get_identifier(self, request: Request) -> str:
        """
        Extrai identificador da requisição.
        Prioridade: user_id do JWT > IP do cliente.
        """
        # Se tem usuário autenticado, usa o user_id
        if hasattr(request.state, "user") and request.state.user:
            return f"user:{request.state.user.id}"

        # Senão, usa o IP
        client_ip = request.client.host if request.client else "unknown"
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()

        return f"ip:{client_ip}"

    async def check(self, request: Request) -> None:
        """
        Verifica e incrementa o contador de rate limit.
        Levanta HTTPException 429 se exceder o limite.
        """
        try:
            r = await self._get_redis()
            identifier = await self._get_identifier(request)
            key = self._get_key(identifier)

            # Incrementar contador
            current = await r.incr(key)

            # Se é a primeira requisição, definir TTL
            if current == 1:
                await r.expire(key, self.window_seconds)

            # Verificar limite
            if current > self.max_requests:
                ttl = await r.ttl(key)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "error": "Rate limit excedido",
                        "max_requests": self.max_requests,
                        "window_seconds": self.window_seconds,
                        "retry_after": ttl,
                    },
                    headers={"Retry-After": str(ttl)},
                )

        except HTTPException:
            raise
        except Exception:
            # Se Redis falhar, permite a requisição (fail open)
            pass

    async def __call__(self, request: Request) -> None:
        """Permite usar como Depends() no FastAPI."""
        await self.check(request)

    async def get_remaining(self, request: Request) -> dict:
        """Retorna informações de rate limit para headers de resposta."""
        try:
            r = await self._get_redis()
            identifier = await self._get_identifier(request)
            key = self._get_key(identifier)

            current = await r.get(key)
            ttl = await r.ttl(key)

            used = int(current) if current else 0
            return {
                "X-RateLimit-Limit": str(self.max_requests),
                "X-RateLimit-Remaining": str(max(0, self.max_requests - used)),
                "X-RateLimit-Reset": str(ttl if ttl > 0 else self.window_seconds),
            }
        except Exception:
            return {}

    async def close(self) -> None:
        """Fecha conexão com Redis."""
        if self._redis:
            await self._redis.close()


# ── Instâncias pré-configuradas ──

# API geral: 60 req/min
api_limiter = RateLimiter(max_requests=60, window_seconds=60, key_prefix="rl:api")

# Webhooks: 120 req/min (volume alto de leads)
webhook_limiter = RateLimiter(max_requests=120, window_seconds=60, key_prefix="rl:webhook")

# Comandos (consome Claude API — mais restritivo): 20 req/min
command_limiter = RateLimiter(max_requests=20, window_seconds=60, key_prefix="rl:command")
