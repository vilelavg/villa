"""
Villa — Endpoint de Comandos
Onde Caio, Thaís e equipe enviam comandos ao Villa em linguagem natural.
POST /command → Orquestrador → Módulo correto → Resposta.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.middleware.auth import get_current_user
from api.middleware.rate_limit import command_limiter
from core.database import get_db
from core.models import CommandRequest, CommandResponse, ModuleCode, User
from core.orchestrator import orchestrator

router = APIRouter()


@router.post("", response_model=CommandResponse)
async def execute_command(
    command: CommandRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    _rate=Depends(command_limiter),
):
    """
    Recebe um comando em linguagem natural e roteia para o módulo correto.
    
    Exemplos de comandos:
        "Gera um roteiro para o Ottoboni sobre implantes"
        "Como estão as campanhas do Linardi essa semana?"
        "Qual foi o melhor criativo de lentes de contato?"
        "Agenda uma consulta para o lead João amanhã às 14h"
        "Manda o relatório semanal do Elite"
    """
    return await orchestrator.process_command(
        message=command.message,
        db=db,
        user=user,
        client_slug=command.client_slug,
        module_hint=command.module,
    )


@router.post("/direct/{module}", response_model=CommandResponse)
async def execute_direct_module(
    module: ModuleCode,
    command: CommandRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    _rate=Depends(command_limiter),
):
    """
    Executa um comando diretamente em um módulo específico,
    sem passar pelo orquestrador.
    
    Útil para testes e quando o usuário sabe exatamente o que quer.
    
    Ex: POST /command/direct/m01_roteiros
        {"message": "Gera roteiro de implante para Ottoboni"}
    """
    return await orchestrator.process_command(
        message=command.message,
        db=db,
        user=user,
        client_slug=command.client_slug,
        module_hint=module,
    )
