"""
Villa — Módulo M01: Geração e Validação de Roteiros
Prioridade 1 do MVP.

Fluxo completo:
    1. Recebe briefing (cliente, tema, formato, público)
    2. Consulta feedback loop (o que funcionou/falhou antes)
    3. Gera roteiro com prompt enriquecido pela memória
    4. Tripla validação automática (gancho, corpo, CTA)
    5. Se não passou: refina automaticamente (até 2 tentativas)
    6. Gera variações A/B do gancho
    7. Salva no banco com scores e registra decisão
    8. Retorna roteiro pronto para revisão humana
"""

import re
from typing import Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import (
    Client, Roteiro, RoteiroStatus, ModuleCode, User,
)
from modules.base import BaseModule
from modules.m01_roteiros.prompts import (
    SYSTEM_PROMPT,
    GENERATION_PROMPT,
    HOOK_VARIATIONS_PROMPT,
    REFINEMENT_PROMPT,
)
from modules.m01_roteiros.validators import RoteiroValidator
from memory.feedback_loop import FeedbackLoop


class M01Roteiros(BaseModule):
    """Módulo de geração e validação de roteiros."""

    code = ModuleCode.M01_ROTEIROS
    name = "Roteiros"
    description = (
        "Gera roteiros de vídeo (Reels, Stories, anúncios) com tripla validação "
        "automática (gancho, corpo, CTA) para clientes da WebXP."
    )

    # Palavras-chave que indicam que este módulo deve atuar
    KEYWORDS = [
        "roteiro", "roteiros", "script", "scripts",
        "gancho", "ganchos", "hook",
        "cta", "chamada para ação",
        "criativo", "criativos", "copy", "copies",
        "reels", "stories", "vídeo", "video",
        "escreve", "escreva", "cria", "crie", "gera", "gere",
        "monta", "monte", "faz", "faça",
    ]

    MAX_REFINEMENT_ATTEMPTS = 2

    def __init__(self):
        super().__init__()
        self.validator = RoteiroValidator()

    async def can_handle(self, message: str, context: Optional[dict] = None) -> float:
        """Retorna confiança de 0-1 de que este módulo deve lidar com o comando."""
        msg_lower = message.lower()

        # Match direto com palavras-chave
        matches = sum(1 for kw in self.KEYWORDS if kw in msg_lower)

        if matches == 0:
            return 0.0
        if matches >= 3:
            return 0.95
        if matches >= 2:
            return 0.85
        if matches >= 1:
            # Uma keyword sozinha — verificar se é verbo genérico
            generic_verbs = {"cria", "crie", "gera", "gere", "faz", "faça", "monta", "monte", "escreve", "escreva"}
            matched_kws = [kw for kw in self.KEYWORDS if kw in msg_lower]
            if all(kw in generic_verbs for kw in matched_kws):
                return 0.3  # Verbo genérico sem contexto — baixa confiança
            return 0.7

        return 0.0

    async def execute(
        self,
        message: str,
        db: AsyncSession,
        user: Optional[User] = None,
        client_slug: Optional[str] = None,
        context: Optional[dict] = None,
    ) -> dict:
        """
        Executa o fluxo completo de geração de roteiro.
        
        O message pode ser:
            - Briefing completo: "Roteiro de implantes pro Ottoboni, reels, público dentistas"
            - Comando curto: "Gera roteiro pro Ottoboni"
            - Evento do scheduler: (contexto automático)
        """
        feedback_loop = FeedbackLoop(db)

        # ── 1. Resolver cliente ──
        client = await self._resolve_client(db, client_slug, message)
        if not client:
            return {
                "success": False,
                "message": "Não consegui identificar o cliente. Especifique o nome ou slug.",
                "actions_taken": ["client_not_found"],
            }

        # ── 2. Extrair briefing do comando ──
        briefing = await self._extract_briefing(message, client)

        # ── 3. Consultar memória (feedback loop) ──
        memory = await feedback_loop.build_context(
            module=self.code,
            action="gerar_roteiro",
            client_slug=client.slug,
            current_input=briefing,
            include_knowledge=True,
            knowledge_query=f"roteiro {client.specialty} {briefing.get('topic', '')}",
        )

        # ── 4. Carregar thresholds do cliente ──
        config = await self.get_config(db)
        client_config = client.config or {}
        min_hook = client_config.get("thresholds", {}).get("min_hook_score", config.get("min_hook_score", 7.0))
        min_body = client_config.get("thresholds", {}).get("min_body_score", config.get("min_body_score", 7.0))
        min_cta = client_config.get("thresholds", {}).get("min_cta_score", config.get("min_cta_score", 7.0))

        # ── 5. Gerar roteiro ──
        training_data = await self.get_training_data(db)
        training_text = self._format_training_examples(training_data)

        generation_prompt = GENERATION_PROMPT.format(
            client_name=client.name,
            specialty=client.specialty or "odontologia geral",
            tone=client_config.get("tom_voz", "profissional e acessível"),
            topic=briefing.get("topic", "tema não especificado"),
            format=briefing.get("format", "Reels (30-60s)"),
            audience=briefing.get("audience", "pacientes e potenciais pacientes"),
            objective=briefing.get("objective", "gerar leads qualificados"),
            memory_context=memory["prompt_injection"],
            training_examples=training_text,
        )

        response = await self.ask_claude(
            message=generation_prompt,
            db=db,
            system_override=SYSTEM_PROMPT,
            client_slug=client.slug,
        )

        raw_text = response["text"]
        parsed = self._parse_roteiro(raw_text)

        if not parsed["hook"] or not parsed["body"] or not parsed["cta"]:
            return {
                "success": False,
                "message": "Erro ao gerar roteiro — estrutura incompleta. Tente novamente com mais detalhes no briefing.",
                "data": {"raw_text": raw_text[:500]},
                "actions_taken": ["generation_failed"],
            }

        # ── 6. Tripla validação ──
        validation = await self.validator.validate_full(
            hook=parsed["hook"],
            body=parsed["body"],
            cta=parsed["cta"],
            specialty=client.specialty or "odontologia",
            min_hook_score=min_hook,
            min_body_score=min_body,
            min_cta_score=min_cta,
        )

        # ── 7. Refinar se não passou (até 2 tentativas) ──
        attempt = 0
        while not validation["all_passed"] and attempt < self.MAX_REFINEMENT_ATTEMPTS:
            attempt += 1
            refined = await self._refine_roteiro(
                parsed, validation, client.specialty or "odontologia", db, client.slug
            )
            parsed = refined
            validation = await self.validator.validate_full(
                hook=parsed["hook"],
                body=parsed["body"],
                cta=parsed["cta"],
                specialty=client.specialty or "odontologia",
                min_hook_score=min_hook,
                min_body_score=min_body,
                min_cta_score=min_cta,
            )

        # ── 8. Gerar variações de gancho ──
        hook_variations = await self._generate_hook_variations(
            parsed["hook"], parsed["body"], client.specialty or "odontologia"
        )

        # ── 9. Salvar no banco ──
        status = RoteiroStatus.APPROVED if validation["all_passed"] else RoteiroStatus.DRAFT
        roteiro = Roteiro(
            id=str(uuid4()),
            client_id=client.id,
            status=status,
            title=parsed.get("title", f"Roteiro {client.name} — {briefing.get('topic', '')}"),
            hook=parsed["hook"],
            body=parsed["body"],
            cta=parsed["cta"],
            full_script=parsed.get("full_script", f"{parsed['hook']}\n\n{parsed['body']}\n\n{parsed['cta']}"),
            hook_score=validation["validations"]["hook"]["score"],
            hook_feedback=validation["validations"]["hook"]["feedback"],
            body_score=validation["validations"]["body"]["score"],
            body_feedback=validation["validations"]["body"]["feedback"],
            cta_score=validation["validations"]["cta"]["score"],
            cta_feedback=validation["validations"]["cta"]["feedback"],
            overall_score=validation["overall_score"],
            hook_variations=hook_variations,
            briefing=briefing,
            generation_params={
                "model": response.get("model"),
                "memory_sources": memory.get("sources", []),
                "refinement_attempts": attempt,
            },
        )
        db.add(roteiro)
        await db.flush()

        # ── 10. Registrar decisão no feedback loop ──
        decision_id = await feedback_loop.record_decision(
            module=self.code,
            action="gerar_roteiro",
            input_data=briefing,
            output_data={
                "roteiro_id": roteiro.id,
                "overall_score": validation["overall_score"],
                "all_passed": validation["all_passed"],
                "attempts": attempt + 1,
            },
            reasoning=memory["reasoning_context"],
            client_slug=client.slug,
            tokens_input=response.get("tokens_input", 0),
            tokens_output=response.get("tokens_output", 0),
            model_used=response.get("model"),
            cost_usd=response.get("cost_usd", 0),
        )

        # ── 11. Montar resposta ──
        actions = ["roteiro_generated", "triple_validation"]
        if attempt > 0:
            actions.append(f"refined_{attempt}x")
        if validation["all_passed"]:
            actions.append("auto_approved")

        return {
            "success": True,
            "message": self._format_response(parsed, validation, hook_variations, attempt),
            "data": {
                "roteiro_id": roteiro.id,
                "decision_id": decision_id,
                "client": client.slug,
                "status": status.value,
                "overall_score": validation["overall_score"],
                "all_passed": validation["all_passed"],
                "attempts": attempt + 1,
                "hook_variations_count": len(hook_variations) if hook_variations else 0,
            },
            "actions_taken": actions,
            "tokens_used": response.get("tokens_input", 0) + response.get("tokens_output", 0) + validation.get("total_tokens", 0),
        }

    # ═══════════════════════════════════════════════════
    # MÉTODOS INTERNOS
    # ═══════════════════════════════════════════════════

    async def _resolve_client(
        self, db: AsyncSession, slug: Optional[str], message: str
    ) -> Optional[Client]:
        """Resolve o cliente pelo slug ou buscando no texto."""
        if slug:
            result = await db.execute(select(Client).where(Client.slug == slug))
            return result.scalar_one_or_none()

        # Buscar pelo nome no texto
        result = await db.execute(select(Client))
        clients = result.scalars().all()
        msg_lower = message.lower()
        for c in clients:
            if c.name.lower() in msg_lower or c.slug.lower() in msg_lower:
                return c
        return None

    async def _extract_briefing(self, message: str, client: Client) -> dict:
        """Extrai informações de briefing do comando em linguagem natural."""
        result = await self.claude.extract_json(
            message=(
                f"Extraia as informações de briefing deste comando:\n\n"
                f'"{message}"\n\n'
                f"Cliente: {client.name} ({client.specialty})\n\n"
                f"Retorne JSON com: topic, format, audience, objective. "
                f"Se não foi mencionado, use valores padrão baseados na especialidade."
            ),
            model="fast",
        )
        data = result.get("data", {})
        if not data:
            data = {}
        data.setdefault("topic", client.specialty or "conteúdo odontológico")
        data.setdefault("format", "Reels (30-60s)")
        data.setdefault("audience", "pacientes e potenciais pacientes")
        data.setdefault("objective", "gerar leads qualificados")
        return data

    def _parse_roteiro(self, text: str) -> dict:
        """Extrai gancho, corpo, CTA e roteiro completo do texto gerado."""
        result = {"title": "", "hook": "", "body": "", "cta": "", "full_script": ""}

        # Título
        title_match = re.search(r"TÍTULO:\s*(.+?)(?:\n|$)", text)
        if title_match:
            result["title"] = title_match.group(1).strip()

        # Gancho
        hook_match = re.search(
            r"GANCHO[^:]*:\s*\n(.+?)(?=\nCORPO|\nBODY)", text, re.DOTALL
        )
        if hook_match:
            result["hook"] = hook_match.group(1).strip()

        # Corpo
        body_match = re.search(
            r"CORPO[^:]*:\s*\n(.+?)(?=\nCTA|\nCHAMADA)", text, re.DOTALL
        )
        if body_match:
            result["body"] = body_match.group(1).strip()

        # CTA
        cta_match = re.search(
            r"(?:CTA|CHAMADA PARA AÇÃO)[^:]*:\s*\n(.+?)(?=\nROTEIRO COMPLETO|$)", text, re.DOTALL
        )
        if cta_match:
            result["cta"] = cta_match.group(1).strip()

        # Roteiro completo
        full_match = re.search(
            r"ROTEIRO COMPLETO[^:]*:\s*\n(.+?)(?=---|$)", text, re.DOTALL
        )
        if full_match:
            result["full_script"] = full_match.group(1).strip()
        else:
            result["full_script"] = f"{result['hook']}\n\n{result['body']}\n\n{result['cta']}"

        return result

    async def _refine_roteiro(
        self,
        parsed: dict,
        validation: dict,
        specialty: str,
        db: AsyncSession,
        client_slug: str,
    ) -> dict:
        """Refina roteiro que não passou na validação."""
        feedback_parts = []
        for component in ["hook", "body", "cta"]:
            v = validation["validations"][component]
            if not v["passed"]:
                feedback_parts.append(
                    f"{component.upper()} ({v['score']:.1f}/10): {v['feedback']}"
                )
                if v.get("suggestion"):
                    feedback_parts.append(f"  Sugestão: {v['suggestion']}")

        prompt = REFINEMENT_PROMPT.format(
            hook=parsed["hook"],
            body=parsed["body"],
            cta=parsed["cta"],
            validation_feedback="\n".join(feedback_parts),
        )

        response = await self.ask_claude(
            message=prompt,
            db=db,
            system_override=SYSTEM_PROMPT,
            client_slug=client_slug,
        )

        return self._parse_roteiro(response["text"])

    async def _generate_hook_variations(
        self, hook: str, body: str, specialty: str, count: int = 3
    ) -> list[dict]:
        """Gera variações A/B do gancho."""
        prompt = HOOK_VARIATIONS_PROMPT.format(
            hook=hook, body=body, specialty=specialty, count=count
        )
        response = await self.claude.extract_json(message=prompt, model="primary")
        data = response.get("data", {})
        return data.get("variations", [])

    def _format_training_examples(self, training_data: Optional[dict]) -> str:
        """Formata exemplos de treinamento para incluir no prompt."""
        if not training_data:
            return ""
        examples = training_data.get("approved_roteiros", [])
        if not examples:
            return ""
        lines = ["\n## EXEMPLOS DE ROTEIROS APROVADOS\n"]
        for i, ex in enumerate(examples[:3], 1):
            lines.append(f"**Exemplo {i}:**")
            if ex.get("hook"):
                lines.append(f"Gancho: {ex['hook']}")
            if ex.get("performance"):
                lines.append(f"Performance: {ex['performance']}")
            lines.append("")
        return "\n".join(lines)

    def _format_response(
        self, parsed: dict, validation: dict, variations: list, attempts: int
    ) -> str:
        """Formata resposta legível para o usuário."""
        lines = []

        status_emoji = "✅" if validation["all_passed"] else "⚠️"
        lines.append(f"{status_emoji} **{parsed.get('title', 'Roteiro')}** (Score: {validation['overall_score']}/10)")

        if attempts > 0:
            lines.append(f"_Refinado {attempts}x automaticamente._")

        lines.append(f"\n**GANCHO** ({validation['validations']['hook']['score']:.1f}/10):")
        lines.append(parsed["hook"])

        lines.append(f"\n**CORPO** ({validation['validations']['body']['score']:.1f}/10):")
        lines.append(parsed["body"])

        lines.append(f"\n**CTA** ({validation['validations']['cta']['score']:.1f}/10):")
        lines.append(parsed["cta"])

        if variations:
            lines.append(f"\n**VARIAÇÕES DE GANCHO (A/B):**")
            for i, v in enumerate(variations, 1):
                lines.append(f"{i}. {v.get('hook', '')} _({v.get('approach', '')})_")

        if not validation["all_passed"]:
            lines.append(f"\n**PONTOS A MELHORAR:**")
            lines.append(validation["feedback_summary"])

        return "\n".join(lines)
