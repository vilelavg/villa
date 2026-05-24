"""
Villa — Webhook Receivers
Recebe eventos de InLead, Kommo, WhatsApp e N8N.
Cada webhook é validado, logado e roteado para o módulo correto.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.middleware.auth import verify_webhook_signature, verify_whatsapp_webhook
from api.middleware.rate_limit import webhook_limiter
from core.config import settings
from core.database import get_db
from core.models import ModuleCode
from core.orchestrator import orchestrator
from security.audit_log import AuditService

router = APIRouter()


# ═══════════════════════════════════════════════════════════════
# INLEAD — Novo lead capturado
# ═══════════════════════════════════════════════════════════════


@router.post("/inlead")
async def webhook_inlead(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _rate=Depends(webhook_limiter),
):
    """
    Recebe leads do InLead via webhook.
    Campos têm naming aleatório — o módulo M3 faz o mapeamento.

    Fluxo:
        InLead → este endpoint → identifica cliente → M3 (qualificação)
    """
    body = await request.body()
    payload = await request.json()

    # Validar assinatura (se configurada)
    signature = request.headers.get("X-Webhook-Signature", "")
    if settings.inlead_webhook_secret and signature:
        if not verify_webhook_signature(body, signature, settings.inlead_webhook_secret):
            raise HTTPException(status_code=401, detail="Assinatura inválida")

    # Log da ação
    audit = AuditService(db)
    await audit.log(
        action="webhook_inlead_received",
        module=ModuleCode.M03_QUALIFICACAO,
        details={
            "form_id": payload.get("form_id"),
            "fields_count": len(payload),
        },
    )

    # Rotear para o orquestrador
    await orchestrator.handle_event("inlead_new_lead", payload, db)

    return {
        "status": "received",
        "source": "inlead",
        "timestamp": datetime.utcnow().isoformat(),
    }


# ═══════════════════════════════════════════════════════════════
# KOMMO — Eventos do CRM (card movido, lead criado, etc.)
# ═══════════════════════════════════════════════════════════════


@router.post("/kommo")
async def webhook_kommo(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _rate=Depends(webhook_limiter),
):
    """
    Recebe eventos do Kommo CRM.

    Eventos relevantes:
        - lead_added: novo lead no pipeline
        - lead_status_changed: card moveu de etapa
        - lead_deleted: lead removido
        - note_added: nota adicionada ao lead

    Fluxo:
        Kommo → este endpoint → identifica evento → módulo correto
    """
    body = await request.body()
    payload = await request.json()

    # Validar assinatura
    signature = request.headers.get("X-Signature", "")
    if settings.kommo_webhook_secret and signature:
        if not verify_webhook_signature(body, signature, settings.kommo_webhook_secret):
            raise HTTPException(status_code=401, detail="Assinatura inválida")

    # Identificar tipo de evento
    event_type = _detect_kommo_event(payload)

    audit = AuditService(db)
    await audit.log(
        action=f"webhook_kommo_{event_type}",
        module=ModuleCode.M03_QUALIFICACAO,
        details={
            "event_type": event_type,
            "lead_id": payload.get("leads", {}).get("status", [{}])[0].get("id")
            if "leads" in payload
            else None,
        },
    )

    # Rotear para o orquestrador
    await orchestrator.handle_event(f"kommo_{event_type}", payload, db)

    return {
        "status": "received",
        "source": "kommo",
        "event_type": event_type,
        "timestamp": datetime.utcnow().isoformat(),
    }


def _detect_kommo_event(payload: dict) -> str:
    """Detecta o tipo de evento do Kommo baseado no payload."""
    if "leads" in payload:
        leads = payload["leads"]
        if "add" in leads:
            return "lead_added"
        if "status" in leads:
            return "lead_status_changed"
        if "delete" in leads:
            return "lead_deleted"
        if "update" in leads:
            return "lead_updated"
    if "contacts" in payload:
        return "contact_updated"
    if "account" in payload:
        return "account_updated"
    return "unknown"


# ═══════════════════════════════════════════════════════════════
# WHATSAPP — Mensagens recebidas e status de entrega
# ═══════════════════════════════════════════════════════════════


@router.get("/whatsapp")
async def webhook_whatsapp_verify(
    mode: str = Query(alias="hub.mode", default=""),
    token: str = Query(alias="hub.verify_token", default=""),
    challenge: str = Query(alias="hub.challenge", default=""),
):
    """
    Verificação de webhook do WhatsApp Business API.
    Meta envia GET para confirmar que o endpoint é válido.
    """
    result = verify_whatsapp_webhook(mode, token, challenge)
    if result:
        return PlainTextResponse(content=result)
    raise HTTPException(status_code=403, detail="Verificação falhou")


@router.post("/whatsapp")
async def webhook_whatsapp(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _rate=Depends(webhook_limiter),
):
    """
    Recebe mensagens e status do WhatsApp Business API.

    Tipos de evento:
        - messages: mensagem recebida de um lead/cliente
        - statuses: status de entrega (sent, delivered, read)

    Fluxo:
        WhatsApp → este endpoint → identifica tipo → M3 ou M6
    """
    payload = await request.json()

    # Extrair dados da estrutura do WhatsApp Cloud API
    entry = payload.get("entry", [{}])[0]
    changes = entry.get("changes", [{}])[0]
    value = changes.get("value", {})

    # Mensagens recebidas
    messages = value.get("messages", [])
    statuses = value.get("statuses", [])

    audit = AuditService(db)

    if messages:
        for msg in messages:
            msg_type = msg.get("type", "unknown")
            from_number = msg.get("from", "")

            await audit.log(
                action="webhook_whatsapp_message",
                module=ModuleCode.M06_ATENDIMENTO,
                details={
                    "from": from_number[-4:]
                    if from_number
                    else "",  # Últimos 4 dígitos (privacidade)
                    "type": msg_type,
                    "has_text": "text" in msg,
                },
            )

            # Rotear para o orquestrador
            await orchestrator.handle_event(
                "whatsapp_message",
                {
                    "from": from_number,
                    "message": msg,
                    "contact": value.get("contacts", [{}])[0],
                },
                db,
            )

    if statuses:
        for st in statuses:
            await audit.log(
                action=f"webhook_whatsapp_status_{st.get('status', 'unknown')}",
                module=ModuleCode.M06_ATENDIMENTO,
                details={"message_id": st.get("id"), "status": st.get("status")},
            )

    return {"status": "received"}


# ═══════════════════════════════════════════════════════════════
# N8N — Eventos de workflows existentes
# ═══════════════════════════════════════════════════════════════


@router.post("/n8n")
async def webhook_n8n(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_n8n_api_key: str | None = Header(None),
    _rate=Depends(webhook_limiter),
):
    """
    Recebe eventos do N8N (workflows existentes da WebXP).

    Payload esperado:
        {
            "event": "inlead_new_lead",      ← tipo do evento
            "workflow": "inlead_capture",    ← nome do workflow no N8N
            "client_slug": "webxp",          ← cliente (opcional)
            "data": { ...dados do evento... }
        }

    Eventos suportados:
        n8n_inlead_new_lead      → M02 (relatório) + M14 (SDR)
        n8n_kommo_lead_updated   → M02 (relatório)
        n8n_capi_event           → M04 (campanhas)
        n8n_report_request       → M02 (relatório)

    Header obrigatório:
        x-n8n-api-key: {N8N_API_KEY do .env}
    """
    # Validar API key
    if settings.n8n_api_key and x_n8n_api_key != settings.n8n_api_key:
        raise HTTPException(status_code=401, detail="N8N API key inválida")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Payload inválido — esperado JSON")

    workflow_name = payload.get("workflow", "unknown")
    event_type = payload.get("event", "generic")
    client_slug = payload.get("client_slug")
    data = payload.get("data", {})

    # Enriquecer payload com client_slug para o orquestrador
    enriched_payload = {
        **payload,
        "source": "n8n",
        "workflow": workflow_name,
        "client_slug": client_slug,
        "data": data,
    }

    audit = AuditService(db)
    await audit.log(
        action=f"webhook_n8n_{event_type}",
        details={
            "workflow": workflow_name,
            "client_slug": client_slug,
            "event": event_type,
            "data_keys": list(data.keys()),
        },
    )

    # Rotear para o orquestrador
    results = await orchestrator.handle_event(f"n8n_{event_type}", enriched_payload, db)

    return {
        "status": "received",
        "source": "n8n",
        "workflow": workflow_name,
        "event": event_type,
        "modules_triggered": [r.get("module") for r in results if r.get("success")],
        "timestamp": datetime.utcnow().isoformat(),
    }
