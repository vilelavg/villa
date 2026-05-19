"""
Villa — Decision Log
Registro estruturado de TODA decisão que o Villa toma.

Cada decisão tem 3 fases:
    1. REGISTRO: o que decidiu, com que dados, por que razão
    2. AVALIAÇÃO: o que aconteceu depois (sucesso? falha? parcial?)
    3. FEEDBACK: o que Caio/Thaís acharam (aprovaram? rejeitaram? ajustaram?)

Esse ciclo alimenta o feedback loop — o Villa consulta decisões passadas
e seus resultados antes de tomar novas decisões.
"""

from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4

from sqlalchemy import select, update, func, and_, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import DecisionLog, ModuleCode, Client


class DecisionLogService:
    """
    Gerencia o ciclo de vida das decisões do Villa.
    
    Ciclo completo:
        # 1. Villa toma uma decisão
        decision_id = await decisions.record(
            module=ModuleCode.M01_ROTEIROS,
            action="gerar_roteiro",
            input_data={"client": "ottoboni", "tema": "implantes"},
            output_data={"roteiro": "...", "hook_score": 8.5},
            reasoning="Cliente pediu roteiro de implantes. Base de dados mostra que ganchos com números performam 40% melhor para este cliente.",
            client_slug="ottoboni",
        )
        
        # 2. Resultado é avaliado (dias depois)
        await decisions.evaluate(
            decision_id=decision_id,
            outcome="success",
            outcome_details={"approved": True, "ctr": 2.3, "views": 15000},
        )
        
        # 3. Humano dá feedback
        await decisions.add_feedback(
            decision_id=decision_id,
            feedback="Roteiro excelente, o gancho com número funcionou muito bem. Replicar essa abordagem."
        )
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    # ═══════════════════════════════════════════════════
    # FASE 1: REGISTRO
    # ═══════════════════════════════════════════════════

    async def record(
        self,
        module: ModuleCode,
        action: str,
        input_data: Optional[dict] = None,
        output_data: Optional[dict] = None,
        reasoning: Optional[str] = None,
        client_slug: Optional[str] = None,
        tokens_input: Optional[int] = None,
        tokens_output: Optional[int] = None,
        model_used: Optional[str] = None,
        cost_usd: Optional[float] = None,
    ) -> str:
        """
        Registra uma decisão. Retorna o ID para avaliação posterior.
        
        Args:
            module: Módulo que tomou a decisão
            action: Tipo de ação (ex: "gerar_roteiro", "qualificar_lead")
            input_data: Dados de entrada (briefing, mensagem do lead, etc.)
            output_data: Resultado produzido (roteiro, score, resposta)
            reasoning: POR QUE o Villa tomou essa decisão (crucial pro feedback loop)
            client_slug: Cliente envolvido
            tokens_input/output: Tokens consumidos
            model_used: Modelo Claude usado
            cost_usd: Custo estimado
            
        Returns:
            ID da decisão (UUID)
        """
        # Resolver client_id
        client_id = None
        if client_slug:
            result = await self.db.execute(
                select(Client.id).where(Client.slug == client_slug)
            )
            client_id = result.scalar_one_or_none()

        decision = DecisionLog(
            id=str(uuid4()),
            module=module,
            client_id=client_id,
            action=action,
            input_data=input_data,
            output_data=output_data,
            reasoning=reasoning,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            model_used=model_used,
            cost_usd=cost_usd,
        )

        self.db.add(decision)
        await self.db.flush()
        return decision.id

    # ═══════════════════════════════════════════════════
    # FASE 2: AVALIAÇÃO DE RESULTADO
    # ═══════════════════════════════════════════════════

    async def evaluate(
        self,
        decision_id: str,
        outcome: str,
        outcome_details: Optional[dict] = None,
    ) -> None:
        """
        Registra o resultado de uma decisão.
        
        Args:
            decision_id: ID da decisão
            outcome: "success" | "failure" | "partial" | "pending"
            outcome_details: Métricas de resultado (CTR, aprovação, conversão, etc.)
        """
        await self.db.execute(
            update(DecisionLog)
            .where(DecisionLog.id == decision_id)
            .values(
                outcome=outcome,
                outcome_details=outcome_details,
                evaluated_at=datetime.utcnow(),
            )
        )
        await self.db.flush()

    # ═══════════════════════════════════════════════════
    # FASE 3: FEEDBACK HUMANO
    # ═══════════════════════════════════════════════════

    async def add_feedback(
        self,
        decision_id: str,
        feedback: str,
    ) -> None:
        """
        Registra feedback humano sobre uma decisão.
        Esse é o sinal mais forte pro Villa aprender.
        
        Exemplos de feedback:
            "Roteiro aprovado, gancho com número funcionou"
            "Rejeitado — tom muito agressivo pro cliente"
            "Bom mas CTA precisa ser mais direta"
            "Lead qualificado corretamente, converteu em 3 dias"
        """
        await self.db.execute(
            update(DecisionLog)
            .where(DecisionLog.id == decision_id)
            .values(human_feedback=feedback)
        )
        await self.db.flush()

    # ═══════════════════════════════════════════════════
    # CONSULTAS (usadas pelo feedback loop)
    # ═══════════════════════════════════════════════════

    async def get_similar_decisions(
        self,
        module: ModuleCode,
        action: str,
        client_slug: Optional[str] = None,
        outcome: Optional[str] = None,
        with_feedback_only: bool = False,
        limit: int = 10,
    ) -> list[dict]:
        """
        Busca decisões passadas similares.
        Prioriza: com feedback humano > com outcome > recentes.
        
        Usado pelo feedback loop antes de tomar novas decisões.
        """
        query = (
            select(DecisionLog)
            .where(DecisionLog.module == module)
            .where(DecisionLog.action == action)
        )

        # Filtrar por cliente
        if client_slug:
            client_result = await self.db.execute(
                select(Client.id).where(Client.slug == client_slug)
            )
            client_id = client_result.scalar_one_or_none()
            if client_id:
                query = query.where(DecisionLog.client_id == client_id)

        # Filtrar por outcome
        if outcome:
            query = query.where(DecisionLog.outcome == outcome)

        # Apenas com feedback humano
        if with_feedback_only:
            query = query.where(DecisionLog.human_feedback.isnot(None))

        # Ordenar: com feedback primeiro, depois por data
        query = query.order_by(
            # Decisões com feedback humano primeiro
            DecisionLog.human_feedback.isnot(None).desc(),
            # Depois com outcome avaliado
            DecisionLog.outcome.isnot(None).desc(),
            # Mais recentes primeiro
            DecisionLog.created_at.desc(),
        ).limit(limit)

        result = await self.db.execute(query)
        decisions = result.scalars().all()

        return [
            {
                "id": d.id,
                "action": d.action,
                "input_data": d.input_data,
                "output_data": d.output_data,
                "reasoning": d.reasoning,
                "outcome": d.outcome,
                "outcome_details": d.outcome_details,
                "human_feedback": d.human_feedback,
                "created_at": d.created_at.isoformat() if d.created_at else None,
                "evaluated_at": d.evaluated_at.isoformat() if d.evaluated_at else None,
            }
            for d in decisions
        ]

    async def get_success_rate(
        self,
        module: ModuleCode,
        action: Optional[str] = None,
        days: int = 30,
    ) -> dict:
        """
        Calcula taxa de sucesso do módulo nos últimos N dias.
        Útil para monitoramento e auto-diagnóstico.
        """
        cutoff = datetime.utcnow() - timedelta(days=days)

        base_query = (
            select(DecisionLog)
            .where(DecisionLog.module == module)
            .where(DecisionLog.created_at >= cutoff)
            .where(DecisionLog.outcome.isnot(None))
        )
        if action:
            base_query = base_query.where(DecisionLog.action == action)

        result = await self.db.execute(base_query)
        decisions = result.scalars().all()

        total = len(decisions)
        if total == 0:
            return {"total": 0, "success_rate": None, "days": days}

        successes = sum(1 for d in decisions if d.outcome == "success")
        failures = sum(1 for d in decisions if d.outcome == "failure")
        partial = sum(1 for d in decisions if d.outcome == "partial")

        return {
            "total": total,
            "success": successes,
            "failure": failures,
            "partial": partial,
            "success_rate": round(successes / total * 100, 1),
            "days": days,
            "module": module.value,
        }

    async def get_client_patterns(
        self,
        client_slug: str,
        module: Optional[ModuleCode] = None,
        limit: int = 20,
    ) -> dict:
        """
        Analisa padrões de sucesso/falha para um cliente específico.
        O Villa usa isso para personalizar sua abordagem por cliente.
        
        Returns:
            Dict com: successful_patterns, failed_patterns, feedback_themes
        """
        client_result = await self.db.execute(
            select(Client.id).where(Client.slug == client_slug)
        )
        client_id = client_result.scalar_one_or_none()
        if not client_id:
            return {"successful_patterns": [], "failed_patterns": [], "feedback_themes": []}

        query = (
            select(DecisionLog)
            .where(DecisionLog.client_id == client_id)
            .where(DecisionLog.outcome.isnot(None))
            .order_by(DecisionLog.created_at.desc())
            .limit(limit)
        )
        if module:
            query = query.where(DecisionLog.module == module)

        result = await self.db.execute(query)
        decisions = result.scalars().all()

        successful = [
            {
                "action": d.action,
                "reasoning": d.reasoning,
                "outcome_details": d.outcome_details,
                "human_feedback": d.human_feedback,
            }
            for d in decisions if d.outcome == "success"
        ]

        failed = [
            {
                "action": d.action,
                "reasoning": d.reasoning,
                "outcome_details": d.outcome_details,
                "human_feedback": d.human_feedback,
            }
            for d in decisions if d.outcome == "failure"
        ]

        feedback_list = [
            d.human_feedback for d in decisions
            if d.human_feedback
        ]

        return {
            "client": client_slug,
            "successful_patterns": successful,
            "failed_patterns": failed,
            "feedback_themes": feedback_list,
            "total_evaluated": len(decisions),
        }

    async def get_cross_client_insights(
        self,
        module: ModuleCode,
        action: str,
        limit: int = 15,
    ) -> list[dict]:
        """
        Busca insights que funcionaram em OUTROS clientes.
        Permite transferência de aprendizado entre clientes.
        
        Ex: Se um gancho com números funciona para Ottoboni (implantes),
        talvez funcione para Linardi (lentes) também.
        """
        result = await self.db.execute(
            select(DecisionLog)
            .where(DecisionLog.module == module)
            .where(DecisionLog.action == action)
            .where(DecisionLog.outcome == "success")
            .where(DecisionLog.human_feedback.isnot(None))
            .order_by(DecisionLog.created_at.desc())
            .limit(limit)
        )
        decisions = result.scalars().all()

        # Agrupar por client_id para ver padrões cross-client
        insights = []
        for d in decisions:
            # Buscar slug do cliente
            slug = None
            if d.client_id:
                client_result = await self.db.execute(
                    select(Client.slug).where(Client.id == d.client_id)
                )
                slug = client_result.scalar_one_or_none()

            insights.append({
                "client": slug or "geral",
                "action": d.action,
                "reasoning": d.reasoning,
                "outcome_details": d.outcome_details,
                "human_feedback": d.human_feedback,
            })

        return insights

    async def get_pending_evaluations(
        self,
        module: Optional[ModuleCode] = None,
        days_old: int = 3,
        limit: int = 20,
    ) -> list[dict]:
        """
        Lista decisões que ainda não foram avaliadas.
        Útil para alertar Caio/Thaís a darem feedback.
        """
        cutoff = datetime.utcnow() - timedelta(days=days_old)

        query = (
            select(DecisionLog)
            .where(DecisionLog.outcome.is_(None))
            .where(DecisionLog.created_at <= cutoff)
            .order_by(DecisionLog.created_at.asc())
            .limit(limit)
        )
        if module:
            query = query.where(DecisionLog.module == module)

        result = await self.db.execute(query)
        decisions = result.scalars().all()

        return [
            {
                "id": d.id,
                "module": d.module.value if d.module else None,
                "action": d.action,
                "reasoning": d.reasoning,
                "created_at": d.created_at.isoformat() if d.created_at else None,
                "days_pending": (datetime.utcnow() - d.created_at).days if d.created_at else None,
            }
            for d in decisions
        ]

    async def get_cost_summary(
        self,
        days: int = 30,
        by_module: bool = True,
    ) -> dict:
        """
        Resumo de custos de API por período.
        Útil para controlar gasto com Anthropic API.
        """
        cutoff = datetime.utcnow() - timedelta(days=days)

        result = await self.db.execute(
            select(DecisionLog)
            .where(DecisionLog.created_at >= cutoff)
            .where(DecisionLog.cost_usd.isnot(None))
        )
        decisions = result.scalars().all()

        total_cost = sum(d.cost_usd or 0 for d in decisions)
        total_tokens_in = sum(d.tokens_input or 0 for d in decisions)
        total_tokens_out = sum(d.tokens_output or 0 for d in decisions)
        total_calls = len(decisions)

        summary = {
            "period_days": days,
            "total_cost_usd": round(total_cost, 4),
            "total_tokens_input": total_tokens_in,
            "total_tokens_output": total_tokens_out,
            "total_calls": total_calls,
            "avg_cost_per_call": round(total_cost / total_calls, 6) if total_calls else 0,
        }

        if by_module:
            by_mod = {}
            for d in decisions:
                mod = d.module.value if d.module else "unknown"
                if mod not in by_mod:
                    by_mod[mod] = {"cost_usd": 0, "calls": 0, "tokens": 0}
                by_mod[mod]["cost_usd"] += d.cost_usd or 0
                by_mod[mod]["calls"] += 1
                by_mod[mod]["tokens"] += (d.tokens_input or 0) + (d.tokens_output or 0)
            # Arredondar custos
            for mod in by_mod:
                by_mod[mod]["cost_usd"] = round(by_mod[mod]["cost_usd"], 4)
            summary["by_module"] = by_mod

        return summary
