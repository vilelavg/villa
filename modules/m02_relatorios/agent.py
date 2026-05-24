"""
Villa — Módulo M02: Relatórios Automatizados
Prioridade 2 do MVP.

Fluxo:
    1. Coleta dados de Meta Ads, Google Ads, Kommo, InLead, Villa DB
    2. Consolida métricas em formato padronizado
    3. Envia ao Claude para análise de primeira camada
    4. Formata no formato correto (WhatsApp diário, semanal, mensal)
    5. Armazena no banco e entrega para envio
"""

import logging

logger = logging.getLogger(__name__)

from datetime import date, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import (
    Client,
    ClientStatus,
    ModuleCode,
    Report,
    User,
)
from memory.feedback_loop import FeedbackLoop
from modules.base import BaseModule
from modules.m02_relatorios.collectors import DataCollector
from modules.m02_relatorios.formatters import ReportFormatter

ANALYSIS_SYSTEM_PROMPT = """Você é o Villa, módulo de relatórios da WebXP Agency.

Sua função é analisar dados de performance de campanhas e gerar insights acionáveis.

## Regras de análise

1. Sempre comece pelo número mais importante: ROI ou CPL
2. Compare com thresholds do cliente quando disponíveis
3. Identifique tendências (subindo, caindo, estável) — não apenas números isolados
4. Destaque anomalias (CPL 2x acima da média, campanha sem leads, etc.)
5. Termine com 2-3 recomendações concretas e específicas
6. Use linguagem direta — "O CPL subiu 30% e precisa de atenção" não "Observamos uma variação"
7. Nunca invente dados — se falta informação, diga que falta

## Tom

Direto, orientado a dados, sem enrolação. Como um sócio operacional apresentando resultado.
"""

ANALYSIS_PROMPT = """Analise os dados de performance abaixo e gere insights acionáveis.

CLIENTE: {client_name} ({specialty})
PERÍODO: {period_start} a {period_end}
TIPO: Relatório {report_type}

DADOS CONSOLIDADOS:
{consolidated_data}

DETALHAMENTO META ADS:
{meta_data}

LEADS:
{leads_data}

AGENDAMENTOS:
{appointments_data}

THRESHOLDS DO CLIENTE:
{thresholds}

{memory_context}

Gere uma análise com:
1. Resumo executivo (2-3 frases)
2. Pontos positivos
3. Pontos de atenção
4. Recomendações concretas (máximo 3)
"""


class M02Relatorios(BaseModule):
    """Módulo de relatórios automatizados."""

    code = ModuleCode.M02_RELATORIOS
    name = "Relatórios"
    description = (
        "Coleta dados de Meta Ads, Google Ads, Kommo e InLead, "
        "consolida métricas e gera relatórios com análise automática."
    )

    KEYWORDS = [
        "relatório", "relatorio", "report",
        "métricas", "metricas", "metrics",
        "performance", "desempenho",
        "resultado", "resultados",
        "dados", "números", "numeros",
        "como está", "como estão", "como tá",
        "campanha", "campanhas",
        "semanal", "mensal", "diário", "diario",
    ]

    def __init__(self):
        super().__init__()
        self.collector = DataCollector.__new__(DataCollector)
        self.formatter = ReportFormatter()

    async def can_handle(self, message: str, context: dict | None = None) -> float:
        """Retorna confiança de 0-1."""
        msg_lower = message.lower()

        # Eventos do scheduler
        if context and context.get("event_type") in ("scheduler_daily", "scheduler_weekly"):
            return 0.95

        matches = sum(1 for kw in self.KEYWORDS if kw in msg_lower)

        if matches >= 3:
            return 0.9
        if matches >= 2:
            return 0.8
        if matches >= 1:
            return 0.6
        return 0.0

    async def execute(
        self,
        message: str,
        db: AsyncSession,
        user: User | None = None,
        client_slug: str | None = None,
        context: dict | None = None,
    ) -> dict:
        """
        Gera relatório para um cliente ou para todos.
        
        Comandos aceitos:
            "Relatório semanal do Ottoboni"
            "Como estão as campanhas do Linardi?"
            "Manda os números da semana"
            Scheduler: gera para todos os clientes ativos
        """
        feedback_loop = FeedbackLoop(db)
        collector = DataCollector(db)
        context = context or {}

        # Detectar tipo de relatório
        report_type = self._detect_report_type(message, context)

        # Detectar período
        period_start, period_end = self._detect_period(report_type)

        # Se scheduler, gerar para todos os clientes
        if context.get("event_type") in ("scheduler_daily", "scheduler_weekly"):
            return await self._generate_for_all_clients(
                db, collector, report_type, period_start, period_end, feedback_loop
            )

        # Se comando direto, gerar para um cliente específico
        client = await self._resolve_client(db, client_slug, message)
        if not client:
            return {
                "success": False,
                "message": "Não identifiquei o cliente. Especifique o nome ou diga 'todos'.",
                "actions_taken": ["client_not_found"],
            }

        # Gerar relatório para o cliente
        return await self._generate_single_report(
            db, collector, client, report_type, period_start, period_end, feedback_loop
        )

    async def _generate_single_report(
        self,
        db: AsyncSession,
        collector: DataCollector,
        client: Client,
        report_type: str,
        period_start: date,
        period_end: date,
        feedback_loop: FeedbackLoop,
    ) -> dict:
        """Gera relatório para um único cliente."""
        # Coletar dados
        data = await collector.collect_all(client, period_start, period_end)

        # Consultar memória
        memory = await feedback_loop.build_context(
            module=self.code,
            action="gerar_relatorio",
            client_slug=client.slug,
        )

        # Análise do Claude
        thresholds = (client.config or {}).get("thresholds", {})
        try:
            analysis_response = await self.ask_claude(
                message=ANALYSIS_PROMPT.format(
                    client_name=client.name,
                    specialty=client.specialty or "odontologia",
                    period_start=period_start,
                    period_end=period_end,
                    report_type=report_type,
                    consolidated_data=str(data.get("consolidated", {})),
                    meta_data=str(data.get("meta_ads", {})),
                    leads_data=str(data.get("leads_summary", {})),
                    appointments_data=str(data.get("appointments", {})),
                    thresholds=str(thresholds),
                    memory_context=memory["prompt_injection"],
                ),
                db=db,
                system_override=ANALYSIS_SYSTEM_PROMPT,
                client_slug=client.slug,
            )

            analysis = analysis_response["text"]

        except Exception as e:
            logger.exception("[M02] Erro na análise Claude para %s: %s", client.slug, e)
            await self.increment_execution(db, success=False)
            return {
                "success": False,
                "message": "Erro ao gerar análise do relatório. Dados coletados mas análise indisponível.",
                "actions_taken": ["data_collected", "error"],
                "data": {"error": str(e), "client": client.slug, "raw_data": data.get("consolidated")},
            }

        # Formatar
        if report_type == "daily":
            formatted = self.formatter.format_daily_whatsapp(data, analysis, client.name)
        elif report_type == "weekly":
            formatted = self.formatter.format_weekly(data, analysis)
        else:
            formatted = self.formatter.format_weekly(data, analysis)

        # Salvar no banco
        report = Report(
            id=str(uuid4()),
            client_id=client.id,
            report_type=report_type,
            period_start=period_start,
            period_end=period_end,
            data=data,
            analysis=analysis,
            summary_whatsapp=self.formatter.format_daily_whatsapp(data, analysis, client.name),
        )
        db.add(report)
        await db.flush()

        # Registrar decisão
        decision_id = await feedback_loop.record_decision(
            module=self.code,
            action="gerar_relatorio",
            input_data={"client": client.slug, "type": report_type, "period": f"{period_start} a {period_end}"},
            output_data={"report_id": report.id, "consolidated": data.get("consolidated")},
            reasoning=memory["reasoning_context"],
            client_slug=client.slug,
            tokens_input=analysis_response.get("tokens_input", 0),
            tokens_output=analysis_response.get("tokens_output", 0),
            model_used=analysis_response.get("model"),
            cost_usd=analysis_response.get("cost_usd", 0),
        )

        return {
            "success": True,
            "message": formatted,
            "data": {
                "report_id": report.id,
                "decision_id": decision_id,
                "client": client.slug,
                "type": report_type,
                "period": f"{period_start} a {period_end}",
                "consolidated": data.get("consolidated"),
            },
            "actions_taken": ["data_collected", "analysis_generated", "report_saved"],
            "tokens_used": analysis_response.get("tokens_input", 0) + analysis_response.get("tokens_output", 0),
        }

    async def _generate_for_all_clients(
        self,
        db: AsyncSession,
        collector: DataCollector,
        report_type: str,
        period_start: date,
        period_end: date,
        feedback_loop: FeedbackLoop,
    ) -> dict:
        """Gera relatórios para todos os clientes ativos (scheduler)."""
        result = await db.execute(
            select(Client).where(Client.status == ClientStatus.ACTIVE)
        )
        clients = result.scalars().all()

        generated = 0
        errors = 0

        for client in clients:
            try:
                await self._generate_single_report(
                    db, collector, client, report_type, period_start, period_end, feedback_loop
                )
                generated += 1
            except Exception:
                errors += 1

        return {
            "success": True,
            "message": f"Relatórios {report_type} gerados: {generated}/{len(clients)} clientes ({errors} erros)",
            "data": {"generated": generated, "errors": errors, "total_clients": len(clients)},
            "actions_taken": [f"batch_{report_type}_reports"],
        }

    async def _resolve_client(
        self, db: AsyncSession, slug: str | None, message: str
    ) -> Client | None:
        """Resolve cliente pelo slug ou nome no texto."""
        if slug:
            result = await db.execute(select(Client).where(Client.slug == slug))
            return result.scalar_one_or_none()

        result = await db.execute(select(Client))
        clients = result.scalars().all()
        msg_lower = message.lower()
        for c in clients:
            if c.name.lower() in msg_lower or c.slug.lower() in msg_lower:
                return c
        return None

    def _detect_report_type(self, message: str, context: dict) -> str:
        """Detecta tipo de relatório pelo texto ou contexto."""
        msg_lower = message.lower()
        if context.get("event_type") == "scheduler_daily" or "diário" in msg_lower or "diario" in msg_lower:
            return "daily"
        if context.get("event_type") == "scheduler_weekly" or "semanal" in msg_lower:
            return "weekly"
        if "mensal" in msg_lower:
            return "monthly"
        return "weekly"

    def _detect_period(self, report_type: str) -> tuple[date, date]:
        """Determina o período baseado no tipo de relatório."""
        today = date.today()
        if report_type == "daily":
            return today - timedelta(days=1), today - timedelta(days=1)
        if report_type == "weekly":
            return today - timedelta(days=7), today - timedelta(days=1)
        if report_type == "monthly":
            first_day = today.replace(day=1) - timedelta(days=1)
            return first_day.replace(day=1), first_day
        return today - timedelta(days=7), today - timedelta(days=1)
