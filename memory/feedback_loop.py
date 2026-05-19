"""
Villa — Feedback Loop
O motor de aprendizado do Villa.

Antes de tomar qualquer decisão, o Villa consulta este serviço para:
    1. Buscar decisões passadas similares (mesmo módulo, mesma ação, mesmo cliente)
    2. Identificar o que funcionou e o que não funcionou
    3. Extrair padrões de sucesso do feedback humano
    4. Injetar esse contexto no prompt do Claude como "memória"

Isso cria um ciclo real de melhoria contínua:
    Decisão → Resultado → Feedback → Melhor decisão → Melhor resultado → ...

Quanto mais o Villa opera, mais dados de resultado e feedback acumula,
e mais precisas ficam suas decisões futuras.
"""

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from core.models import ModuleCode
from memory.decision_log import DecisionLogService
from memory.knowledge_base import KnowledgeBaseService
from integrations.anthropic_client import claude


class FeedbackLoop:
    """
    Motor de aprendizado do Villa.
    
    Uso dentro de um módulo:
    
        loop = FeedbackLoop(db)
        
        # Antes de gerar um roteiro, consultar memória
        memory = await loop.build_context(
            module=ModuleCode.M01_ROTEIROS,
            action="gerar_roteiro",
            client_slug="ottoboni",
            current_input={"tema": "implantes", "formato": "reels"},
        )
        
        # memory["prompt_injection"] contém o bloco de texto para injetar
        # no prompt do Claude — ele vai "lembrar" do que funcionou antes
        
        response = await claude.ask(
            message=briefing,
            system=f"{base_system_prompt}\\n\\n{memory['prompt_injection']}",
        )
        
        # Registrar a decisão
        decision_id = await loop.record_decision(
            module=ModuleCode.M01_ROTEIROS,
            action="gerar_roteiro",
            input_data={"tema": "implantes"},
            output_data={"roteiro": response["text"], "score": 8.5},
            reasoning=memory["reasoning_context"],
            client_slug="ottoboni",
            tokens_input=response["tokens_input"],
            tokens_output=response["tokens_output"],
            model_used=response["model"],
            cost_usd=response["cost_usd"],
        )
        
        # Depois, quando souber o resultado:
        await loop.evaluate(decision_id, "success", {"ctr": 2.3, "approved": True})
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.decisions = DecisionLogService(db)

    # ═══════════════════════════════════════════════════
    # BUILD CONTEXT — Monta a "memória" antes de decidir
    # ═══════════════════════════════════════════════════

    async def build_context(
        self,
        module: ModuleCode,
        action: str,
        client_slug: Optional[str] = None,
        current_input: Optional[dict] = None,
        include_cross_client: bool = True,
        include_knowledge: bool = False,
        knowledge_query: Optional[str] = None,
        max_examples: int = 5,
    ) -> dict:
        """
        Constrói o contexto de memória para injetar no prompt do Claude.
        
        Busca em 4 fontes:
            1. Decisões passadas do mesmo módulo/ação/cliente com feedback
            2. Decisões passadas do mesmo módulo/ação/cliente com outcome
            3. Insights de outros clientes (transferência de aprendizado)
            4. Base de conhecimento (RAG) — se habilitado
        
        Args:
            module: Módulo que vai decidir
            action: Tipo de ação
            client_slug: Cliente envolvido
            current_input: Dados de entrada atuais (para contexto)
            include_cross_client: Buscar padrões de outros clientes
            include_knowledge: Buscar na base de conhecimento (RAG)
            knowledge_query: Query para busca semântica
            max_examples: Máximo de exemplos passados para incluir
            
        Returns:
            Dict com:
                prompt_injection: bloco de texto para injetar no system prompt
                reasoning_context: resumo do raciocínio para logar na decisão
                examples_found: quantos exemplos passados foram encontrados
                sources: de onde vieram os dados
        """
        sections = []
        sources = []
        total_examples = 0

        # ── 1. Decisões com feedback humano (sinal mais forte) ──
        feedback_decisions = await self.decisions.get_similar_decisions(
            module=module,
            action=action,
            client_slug=client_slug,
            with_feedback_only=True,
            limit=max_examples,
        )

        if feedback_decisions:
            block = self._format_feedback_block(feedback_decisions, "APRENDIZADOS COM FEEDBACK HUMANO")
            sections.append(block)
            sources.append("feedback_humano")
            total_examples += len(feedback_decisions)

        # ── 2. Decisões com outcome avaliado (sem feedback, mas com resultado) ──
        remaining = max_examples - total_examples
        if remaining > 0:
            outcome_decisions = await self.decisions.get_similar_decisions(
                module=module,
                action=action,
                client_slug=client_slug,
                outcome="success",
                limit=remaining,
            )

            # Filtrar os que já vieram no passo 1
            seen_ids = {d["id"] for d in feedback_decisions}
            outcome_decisions = [d for d in outcome_decisions if d["id"] not in seen_ids]

            if outcome_decisions:
                block = self._format_outcome_block(outcome_decisions, "DECISÕES BEM-SUCEDIDAS ANTERIORES")
                sections.append(block)
                sources.append("outcomes_positivos")
                total_examples += len(outcome_decisions)

        # ── 3. Erros para evitar ──
        failed_decisions = await self.decisions.get_similar_decisions(
            module=module,
            action=action,
            client_slug=client_slug,
            outcome="failure",
            limit=3,
        )

        if failed_decisions:
            block = self._format_failure_block(failed_decisions)
            sections.append(block)
            sources.append("erros_passados")

        # ── 4. Insights cross-client (transferência de aprendizado) ──
        if include_cross_client and total_examples < max_examples:
            cross_insights = await self.decisions.get_cross_client_insights(
                module=module,
                action=action,
                limit=3,
            )

            # Filtrar insights do próprio cliente (já cobertos acima)
            if client_slug:
                cross_insights = [i for i in cross_insights if i["client"] != client_slug]

            if cross_insights:
                block = self._format_cross_client_block(cross_insights)
                sections.append(block)
                sources.append("cross_client")
                total_examples += len(cross_insights)

        # ── 5. Padrões do cliente ──
        if client_slug:
            patterns = await self.decisions.get_client_patterns(client_slug, module)
            if patterns["feedback_themes"]:
                block = self._format_client_patterns_block(patterns, client_slug)
                sections.append(block)
                sources.append("client_patterns")

        # ── 6. Base de conhecimento (RAG) ──
        if include_knowledge and knowledge_query:
            try:
                kb = KnowledgeBaseService(self.db)
                kb_results = await kb.search(knowledge_query, limit=3, client_slug=client_slug)
                if kb_results:
                    block = self._format_knowledge_block(kb_results)
                    sections.append(block)
                    sources.append("knowledge_base")
            except Exception:
                pass  # RAG é opcional — não quebra o fluxo

        # ── Montar o prompt injection ──
        if sections:
            prompt_injection = (
                "\n\n## MEMÓRIA OPERACIONAL\n"
                "Use as informações abaixo como referência para sua decisão. "
                "Priorize padrões confirmados por feedback humano. "
                "Evite repetir erros documentados.\n\n"
                + "\n\n".join(sections)
            )
        else:
            prompt_injection = ""

        # ── Resumo do raciocínio ──
        reasoning_context = (
            f"Consultei {total_examples} decisões passadas de {', '.join(sources) or 'nenhuma fonte'}. "
        )
        if feedback_decisions:
            reasoning_context += f"{len(feedback_decisions)} com feedback humano. "
        if failed_decisions:
            reasoning_context += f"{len(failed_decisions)} erros para evitar. "

        return {
            "prompt_injection": prompt_injection,
            "reasoning_context": reasoning_context,
            "examples_found": total_examples,
            "sources": sources,
            "has_memory": total_examples > 0,
        }

    # ═══════════════════════════════════════════════════
    # ATALHOS PARA REGISTRO E AVALIAÇÃO
    # ═══════════════════════════════════════════════════

    async def record_decision(self, **kwargs) -> str:
        """Atalho para decisions.record()."""
        return await self.decisions.record(**kwargs)

    async def evaluate(
        self,
        decision_id: str,
        outcome: str,
        outcome_details: Optional[dict] = None,
    ) -> None:
        """Atalho para decisions.evaluate()."""
        await self.decisions.evaluate(decision_id, outcome, outcome_details)

    async def add_feedback(self, decision_id: str, feedback: str) -> None:
        """Atalho para decisions.add_feedback()."""
        await self.decisions.add_feedback(decision_id, feedback)

    # ═══════════════════════════════════════════════════
    # AUTO-AVALIAÇÃO
    # ═══════════════════════════════════════════════════

    async def self_evaluate(
        self,
        module: ModuleCode,
        action: str,
        input_data: dict,
        output_data: dict,
        client_slug: Optional[str] = None,
    ) -> dict:
        """
        O Villa auto-avalia uma decisão comparando com padrões passados.
        Não substitui feedback humano, mas dá uma primeira estimativa.
        
        Usado quando não tem humano disponível para avaliar imediatamente.
        """
        # Buscar padrões de sucesso
        success_patterns = await self.decisions.get_similar_decisions(
            module=module,
            action=action,
            client_slug=client_slug,
            outcome="success",
            with_feedback_only=True,
            limit=5,
        )

        if not success_patterns:
            return {
                "can_evaluate": False,
                "reason": "Sem dados suficientes para auto-avaliação",
            }

        # Pedir ao Claude para comparar
        patterns_text = "\n".join(
            f"- Decisão bem-sucedida: {p['reasoning'] or 'sem raciocínio registrado'} → Feedback: {p['human_feedback'] or 'sem feedback'}"
            for p in success_patterns
        )

        prompt = (
            f"Compare esta nova decisão com padrões de sucesso anteriores.\n\n"
            f"PADRÕES DE SUCESSO:\n{patterns_text}\n\n"
            f"NOVA DECISÃO:\n"
            f"Input: {str(input_data)[:500]}\n"
            f"Output: {str(output_data)[:500]}\n\n"
            f"Responda em JSON: {{\"score\": 0-10, \"confidence\": 0-1, \"reasoning\": \"...\", \"suggestions\": [\"...\"]}}"
        )

        result = await claude.extract_json(
            message=prompt,
            model="fast",  # Haiku — rápido para auto-avaliação
        )

        return {
            "can_evaluate": True,
            "evaluation": result.get("data"),
            "patterns_used": len(success_patterns),
        }

    # ═══════════════════════════════════════════════════
    # FORMATAÇÃO DE BLOCOS PARA O PROMPT
    # ═══════════════════════════════════════════════════

    def _format_feedback_block(self, decisions: list[dict], title: str) -> str:
        """Formata decisões com feedback humano para injeção no prompt."""
        lines = [f"### {title}"]
        for i, d in enumerate(decisions, 1):
            lines.append(f"\n**Exemplo {i}:**")
            if d.get("reasoning"):
                lines.append(f"- Raciocínio: {d['reasoning'][:300]}")
            if d.get("outcome"):
                lines.append(f"- Resultado: {d['outcome']}")
            if d.get("outcome_details"):
                details = ", ".join(f"{k}: {v}" for k, v in (d["outcome_details"] or {}).items())
                lines.append(f"- Métricas: {details[:200]}")
            if d.get("human_feedback"):
                lines.append(f"- **Feedback humano: {d['human_feedback'][:300]}**")
        return "\n".join(lines)

    def _format_outcome_block(self, decisions: list[dict], title: str) -> str:
        """Formata decisões com outcome positivo."""
        lines = [f"### {title}"]
        for i, d in enumerate(decisions, 1):
            lines.append(f"\n**Caso {i}:**")
            if d.get("reasoning"):
                lines.append(f"- Abordagem: {d['reasoning'][:200]}")
            if d.get("outcome_details"):
                details = ", ".join(f"{k}: {v}" for k, v in (d["outcome_details"] or {}).items())
                lines.append(f"- Resultado: {details[:200]}")
        return "\n".join(lines)

    def _format_failure_block(self, decisions: list[dict]) -> str:
        """Formata erros para evitar."""
        lines = ["### ERROS A EVITAR"]
        for i, d in enumerate(decisions, 1):
            lines.append(f"\n**Erro {i}:**")
            if d.get("reasoning"):
                lines.append(f"- O que foi feito: {d['reasoning'][:200]}")
            if d.get("human_feedback"):
                lines.append(f"- Por que falhou: {d['human_feedback'][:200]}")
            elif d.get("outcome_details"):
                lines.append(f"- Resultado negativo: {str(d['outcome_details'])[:200]}")
        return "\n".join(lines)

    def _format_cross_client_block(self, insights: list[dict]) -> str:
        """Formata insights de outros clientes."""
        lines = ["### PADRÕES QUE FUNCIONAM EM OUTROS CLIENTES"]
        for i, ins in enumerate(insights, 1):
            lines.append(f"\n**De {ins['client']}:**")
            if ins.get("reasoning"):
                lines.append(f"- Abordagem: {ins['reasoning'][:200]}")
            if ins.get("human_feedback"):
                lines.append(f"- Feedback: {ins['human_feedback'][:200]}")
        return "\n".join(lines)

    def _format_client_patterns_block(self, patterns: dict, client_slug: str) -> str:
        """Formata padrões específicos do cliente."""
        lines = [f"### PADRÕES DO CLIENTE {client_slug.upper()}"]
        if patterns.get("feedback_themes"):
            lines.append("\nFeedbacks recebidos:")
            for fb in patterns["feedback_themes"][:5]:
                lines.append(f"- \"{fb[:150]}\"")
        succ = len(patterns.get("successful_patterns", []))
        fail = len(patterns.get("failed_patterns", []))
        total = patterns.get("total_evaluated", 0)
        if total > 0:
            lines.append(f"\nHistórico: {succ} sucessos, {fail} falhas de {total} avaliados ({round(succ/total*100)}% taxa de sucesso)")
        return "\n".join(lines)

    def _format_knowledge_block(self, results: list[dict]) -> str:
        """Formata resultados da base de conhecimento (RAG)."""
        lines = ["### INFORMAÇÕES DA BASE DE CONHECIMENTO"]
        for r in results:
            lines.append(f"\n**{r.get('title', 'Documento')}** (relevância: {r.get('score', '?')}):")
            lines.append(f"{r.get('text', '')[:400]}")
        return "\n".join(lines)
