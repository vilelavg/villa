"""
Villa — Endpoint de Clientes
CRUD completo de clientes (dentistas, clínicas, professores).

Rotas:
    POST   /clients           → Criar cliente
    GET    /clients           → Listar clientes
    GET    /clients/{slug}    → Detalhar cliente
    PATCH  /clients/{slug}    → Atualizar cliente
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.models import Client, ClientStatus, User, UserRole
from api.middleware.auth import get_current_user, require_role

router = APIRouter()


# ═══════════════════════════════════════════════════════════════
# SCHEMAS
# ═══════════════════════════════════════════════════════════════

class ClientCreate(BaseModel):
    """Payload para criação de cliente."""
    name: str = Field(..., min_length=2, max_length=200, description="Nome do cliente")
    slug: str = Field(..., min_length=2, max_length=100, description="Identificador único (ex: webxp, ottoboni)")
    specialty: Optional[str] = Field(None, max_length=200, description="Especialidade (ex: implantes, marketing odontologico)")
    client_type: Optional[str] = Field(None, max_length=50, description="professor | clinica | autonomo | agencia")
    contact_name: Optional[str] = Field(None, max_length=200, description="Nome do contato principal")
    contact_phone: Optional[str] = Field(None, max_length=20, description="Telefone do contato")
    contact_email: Optional[str] = Field(None, max_length=255, description="Email do contato")

    # IDs externos (opcionais — preenchidos conforme integrações ficam ativas)
    kommo_pipeline_id: Optional[int] = Field(None, description="ID do pipeline no Kommo CRM")
    meta_ad_account_id: Optional[str] = Field(None, max_length=50, description="ID da conta de anúncios no Meta")
    google_ads_id: Optional[str] = Field(None, max_length=50, description="Customer ID Google Ads")
    inlead_form_id: Optional[str] = Field(None, max_length=100, description="ID do formulário no InLead")
    whatsapp_number: Optional[str] = Field(None, max_length=20, description="Número WhatsApp do cliente")

    # Configurações
    config: Optional[dict] = Field(default_factory=dict, description="Configurações específicas (thresholds, tom de voz)")
    contract_value: Optional[float] = Field(None, description="Valor mensal do contrato")
    contract_start: Optional[date] = Field(None, description="Data de início do contrato")

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        """Slug deve ser lowercase, sem espaços, só letras/números/hífens."""
        import re
        slug = v.lower().strip()
        if not re.match(r'^[a-z0-9-]+$', slug):
            raise ValueError("Slug deve conter apenas letras minúsculas, números e hífens")
        return slug


class ClientUpdate(BaseModel):
    """Payload para atualização parcial de cliente."""
    name: Optional[str] = Field(None, min_length=2, max_length=200)
    specialty: Optional[str] = Field(None, max_length=200)
    client_type: Optional[str] = Field(None, max_length=50)
    contact_name: Optional[str] = Field(None, max_length=200)
    contact_phone: Optional[str] = Field(None, max_length=20)
    contact_email: Optional[str] = Field(None, max_length=255)
    kommo_pipeline_id: Optional[int] = None
    meta_ad_account_id: Optional[str] = Field(None, max_length=50)
    google_ads_id: Optional[str] = Field(None, max_length=50)
    inlead_form_id: Optional[str] = Field(None, max_length=100)
    whatsapp_number: Optional[str] = Field(None, max_length=20)
    config: Optional[dict] = None
    contract_value: Optional[float] = None
    contract_start: Optional[date] = None
    status: Optional[ClientStatus] = None


def _client_to_dict(client: Client) -> dict:
    """Serializa um Client para dict."""
    return {
        "id": client.id,
        "name": client.name,
        "slug": client.slug,
        "status": client.status if isinstance(client.status, str) else client.status.value,
        "specialty": client.specialty,
        "client_type": client.client_type,
        "contact_name": client.contact_name,
        "contact_phone": client.contact_phone,
        "contact_email": client.contact_email,
        "kommo_pipeline_id": client.kommo_pipeline_id,
        "meta_ad_account_id": client.meta_ad_account_id,
        "google_ads_id": client.google_ads_id,
        "inlead_form_id": client.inlead_form_id,
        "whatsapp_number": client.whatsapp_number,
        "contract_value": client.contract_value,
        "contract_start": client.contract_start.isoformat() if client.contract_start else None,
        "config": client.config or {},
        "created_at": client.created_at.isoformat() if client.created_at else None,
        "updated_at": client.updated_at.isoformat() if client.updated_at else None,
    }


# ═══════════════════════════════════════════════════════════════
# ROTAS
# ═══════════════════════════════════════════════════════════════

@router.post("", status_code=201)
async def create_client(
    payload: ClientCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    Cria um novo cliente.
    Requer role ADMIN.

    Exemplo:
        POST /clients
        {
            "name": "WebXP Agency",
            "slug": "webxp",
            "specialty": "marketing odontologico",
            "client_type": "agencia",
            "contact_name": "Caio"
        }
    """
    # Verificar slug único
    existing = await db.execute(
        select(Client).where(Client.slug == payload.slug)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail=f"Já existe um cliente com slug '{payload.slug}'"
        )

    # Criar cliente
    client = Client(
        name=payload.name,
        slug=payload.slug,
        status=ClientStatus.ACTIVE,
        specialty=payload.specialty,
        client_type=payload.client_type,
        contact_name=payload.contact_name,
        contact_phone=payload.contact_phone,
        contact_email=payload.contact_email,
        kommo_pipeline_id=payload.kommo_pipeline_id,
        meta_ad_account_id=payload.meta_ad_account_id,
        google_ads_id=payload.google_ads_id,
        inlead_form_id=payload.inlead_form_id,
        whatsapp_number=payload.whatsapp_number,
        config=payload.config or {},
        contract_value=payload.contract_value,
        contract_start=payload.contract_start,
    )
    db.add(client)
    await db.flush()

    return {
        "success": True,
        "message": f"Cliente '{client.name}' criado com sucesso.",
        "client": _client_to_dict(client),
    }


@router.get("")
async def list_clients(
    status: Optional[str] = Query(None, description="active | onboarding | paused | churned"),
    client_type: Optional[str] = Query(None, description="professor | clinica | autonomo | agencia"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Lista todos os clientes com filtros opcionais.

    Exemplos:
        GET /clients
        GET /clients?status=active
        GET /clients?client_type=clinica
    """
    query = select(Client).order_by(Client.name)

    if status:
        try:
            status_enum = ClientStatus(status)
            query = query.where(Client.status == status_enum.value)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Status inválido: '{status}'")

    if client_type:
        query = query.where(Client.client_type == client_type)

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    clients = result.scalars().all()

    return {
        "count": len(clients),
        "offset": offset,
        "limit": limit,
        "clients": [_client_to_dict(c) for c in clients],
    }


@router.get("/{slug}")
async def get_client(
    slug: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Retorna detalhes de um cliente pelo slug.

    Exemplo:
        GET /clients/webxp
    """
    result = await db.execute(
        select(Client).where(Client.slug == slug)
    )
    client = result.scalar_one_or_none()

    if not client:
        raise HTTPException(status_code=404, detail=f"Cliente '{slug}' não encontrado")

    return _client_to_dict(client)


@router.patch("/{slug}")
async def update_client(
    slug: str,
    payload: ClientUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """
    Atualiza campos de um cliente.
    Requer role ADMIN ou OPERATOR.

    Apenas os campos enviados no payload são atualizados.

    Exemplo:
        PATCH /clients/webxp
        {"kommo_pipeline_id": 12345, "contact_email": "caio@webxp.com.br"}
    """
    result = await db.execute(
        select(Client).where(Client.slug == slug)
    )
    client = result.scalar_one_or_none()

    if not client:
        raise HTTPException(status_code=404, detail=f"Cliente '{slug}' não encontrado")

    # Atualizar apenas campos enviados (PATCH parcial)
    update_data = payload.model_dump(exclude_none=True)

    for field, value in update_data.items():
        if field == "status" and isinstance(value, ClientStatus):
            setattr(client, field, value.value)
        else:
            setattr(client, field, value)

    await db.flush()

    return {
        "success": True,
        "message": f"Cliente '{client.name}' atualizado.",
        "client": _client_to_dict(client),
    }
