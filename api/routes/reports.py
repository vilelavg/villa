"""
Villa — Endpoint de Relatórios
Consulta relatórios gerados pelo módulo M2.
"""

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.middleware.auth import get_current_user, require_role
from core.database import get_db
from core.models import Client, Report, User, UserRole

router = APIRouter()


@router.get("")
async def list_reports(
    client_slug: str | None = Query(None, description="Filtrar por cliente"),
    report_type: str | None = Query(None, description="daily | weekly | monthly"),
    start_date: date | None = Query(None, description="Data início do período"),
    end_date: date | None = Query(None, description="Data fim do período"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Lista relatórios com filtros opcionais.
    
    Exemplos:
        GET /reports?client_slug=ottoboni&report_type=weekly
        GET /reports?start_date=2026-05-01&end_date=2026-05-15
        GET /reports?report_type=monthly&limit=5
    """
    query = select(Report).order_by(Report.created_at.desc())

    # Filtrar por cliente
    if client_slug:
        client_result = await db.execute(
            select(Client).where(Client.slug == client_slug)
        )
        client = client_result.scalar_one_or_none()
        if not client:
            raise HTTPException(status_code=404, detail=f"Cliente '{client_slug}' não encontrado")
        query = query.where(Report.client_id == client.id)

    # Filtrar por tipo
    if report_type:
        query = query.where(Report.report_type == report_type)

    # Filtrar por período
    if start_date:
        query = query.where(Report.period_start >= start_date)
    if end_date:
        query = query.where(Report.period_end <= end_date)

    # Paginação
    query = query.offset(offset).limit(limit)

    result = await db.execute(query)
    reports = result.scalars().all()

    return {
        "count": len(reports),
        "offset": offset,
        "limit": limit,
        "reports": [
            {
                "id": r.id,
                "client_id": r.client_id,
                "report_type": r.report_type,
                "period_start": r.period_start.isoformat(),
                "period_end": r.period_end.isoformat(),
                "analysis": r.analysis,
                "summary_whatsapp": r.summary_whatsapp,
                "sent": r.sent,
                "sent_at": r.sent_at.isoformat() if r.sent_at else None,
                "created_at": r.created_at.isoformat(),
            }
            for r in reports
        ],
    }


@router.get("/{report_id}")
async def get_report(
    report_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Retorna um relatório específico com todos os dados.
    """
    result = await db.execute(
        select(Report).where(Report.id == report_id)
    )
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="Relatório não encontrado")

    return {
        "id": report.id,
        "client_id": report.client_id,
        "report_type": report.report_type,
        "period_start": report.period_start.isoformat(),
        "period_end": report.period_end.isoformat(),
        "data": report.data,
        "analysis": report.analysis,
        "summary_whatsapp": report.summary_whatsapp,
        "summary_pdf_url": report.summary_pdf_url,
        "sent": report.sent,
        "sent_at": report.sent_at.isoformat() if report.sent_at else None,
        "sent_via": report.sent_via,
        "created_at": report.created_at.isoformat(),
    }


@router.post("/{report_id}/send")
async def send_report(
    report_id: str,
    via: str = Query("whatsapp", description="whatsapp | email | drive"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role(UserRole.ADMIN, UserRole.OPERATOR)),
):
    """
    Envia um relatório para o cliente.
    Requer role admin ou operator.
    
    TODO: Integrar com M2 para envio real via WhatsApp/email/Drive.
    """
    result = await db.execute(
        select(Report).where(Report.id == report_id)
    )
    report = result.scalar_one_or_none()

    if not report:
        raise HTTPException(status_code=404, detail="Relatório não encontrado")

    if report.sent:
        raise HTTPException(status_code=400, detail="Relatório já foi enviado")

    # TODO: Bloco 10 (M2) — Envio real
    # await m02_relatorios.send_report(report, via=via)

    report.sent = True
    report.sent_at = datetime.utcnow()
    report.sent_via = via
    await db.flush()

    return {
        "status": "sent",
        "report_id": report.id,
        "sent_via": via,
        "sent_at": report.sent_at.isoformat(),
    }
