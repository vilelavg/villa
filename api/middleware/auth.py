"""
Villa — Autenticação JWT
Middleware de autenticação para a API do Villa.
Protege endpoints com token JWT + validação de role.
"""

from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.database import get_db
from core.models import User, UserRole

# ── Segurança ──
security_scheme = HTTPBearer(auto_error=False)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── Schemas ──
class TokenData(BaseModel):
    """Dados contidos no JWT."""
    user_id: str
    email: str
    role: UserRole
    exp: datetime


class TokenResponse(BaseModel):
    """Resposta do endpoint de login."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict


# ═══════════════════════════════════════════════════════════════
# FUNÇÕES DE AUTENTICAÇÃO
# ═══════════════════════════════════════════════════════════════

def hash_password(password: str) -> str:
    """Gera hash bcrypt de uma senha."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica senha contra hash armazenado."""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(
    user_id: str,
    email: str,
    role: UserRole,
    expires_minutes: int | None = None,
) -> str:
    """
    Cria um token JWT com os dados do usuário.
    
    Args:
        user_id: ID do usuário
        email: Email do usuário
        role: Role do usuário (admin, operator, sdr, readonly)
        expires_minutes: Tempo de expiração em minutos (padrão: config)
        
    Returns:
        Token JWT assinado
    """
    expire = datetime.now(UTC) + timedelta(
        minutes=expires_minutes or settings.jwt_expiration_minutes
    )
    payload = {
        "sub": user_id,
        "email": email,
        "role": role.value,
        "exp": expire,
        "iat": datetime.now(UTC),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> TokenData:
    """
    Decodifica e valida um token JWT.
    
    Raises:
        HTTPException 401: Token inválido ou expirado
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        return TokenData(
            user_id=payload["sub"],
            email=payload["email"],
            role=UserRole(payload["role"]),
            exp=datetime.fromtimestamp(payload["exp"], tz=UTC),
        )
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token inválido: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ═══════════════════════════════════════════════════════════════
# DEPENDENCIES DO FASTAPI
# ═══════════════════════════════════════════════════════════════

async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Dependency que extrai e valida o usuário atual do token JWT.
    
    Uso em routes:
        @router.get("/dados")
        async def dados(user: User = Depends(get_current_user)):
            if user.role == UserRole.ADMIN:
                ...
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticação não fornecido",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_data = decode_token(credentials.credentials)

    # Buscar usuário no banco
    result = await db.execute(
        select(User).where(User.id == token_data.user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário não encontrado",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuário desativado",
        )

    return user


def require_role(*allowed_roles: UserRole):
    """
    Dependency factory que restringe acesso por role.
    
    Uso em routes:
        @router.delete("/cliente/{id}")
        async def deletar(
            id: str,
            user: User = Depends(require_role(UserRole.ADMIN)),
        ):
            ...
    """
    async def role_checker(
        user: User = Depends(get_current_user),
    ) -> User:
        if UserRole(user.role) not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acesso restrito a: {', '.join(r.value for r in allowed_roles)}",
            )
        return user

    return role_checker


# ── Atalhos para roles comuns ──
require_admin = require_role(UserRole.ADMIN)
require_operator = require_role(UserRole.ADMIN, UserRole.OPERATOR)
require_sdr = require_role(UserRole.ADMIN, UserRole.OPERATOR, UserRole.SDR)


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """
    Dependency que retorna o usuário se autenticado, ou None.
    Útil para endpoints que funcionam com e sem auth (ex: webhooks).
    """
    if not credentials:
        return None
    try:
        return await get_current_user(credentials, db)
    except HTTPException:
        return None


# ═══════════════════════════════════════════════════════════════
# AUTENTICAÇÃO DE WEBHOOKS
# ═══════════════════════════════════════════════════════════════

def verify_webhook_signature(
    payload: bytes,
    signature: str,
    secret: str,
) -> bool:
    """
    Valida assinatura de webhook (Kommo, Meta, InLead).
    Cada serviço tem seu próprio método de assinatura.
    
    Args:
        payload: Corpo da requisição em bytes
        signature: Assinatura recebida no header
        secret: Secret configurado no .env
        
    Returns:
        True se a assinatura é válida
    """
    import hashlib
    import hmac

    expected = hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


def verify_whatsapp_webhook(
    mode: str,
    token: str,
    challenge: str,
) -> str | None:
    """
    Valida webhook verification do WhatsApp Business API.
    Retorna o challenge se válido, None se inválido.
    """
    if mode == "subscribe" and token == settings.whatsapp_verify_token:
        return challenge
    return None
