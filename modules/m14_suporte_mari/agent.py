"""
Villa — Módulo M14: Suporte Mari (SDR Assistant)
Adicionado pós-reunião Caio+Thaís (19/05/2026).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PROPÓSITO
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
A Mari faz SDR para cursos odontológicos mas não é dentista.
Ela tem dificuldade com objeções técnicas. O Villa age como
seu copiloto invisível:

1. MODO INGEST — Recebe conversas da Mari (paste de texto
   ou webhook) e as armazena e analisa em background.

2. MODO SUGGEST — Dado um texto de lead, gera 2-3 sugestões
   de resposta prontas para Mari usar ou adaptar.

3. MODO ANALYZE — Processa conversas acumuladas, extrai
   padrões de objeção por curso e atualiza o banco.

4. MODO REPORT — Relatório do banco de objeções: o que mais
   aparece, qual resposta tem maior taxa de conversão.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FLUXO PRÁTICO (USAR AGORA)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Mari recebe: "não sei se tenho tempo pra fazer o curso"
Mari manda para o Villa: POST /commands com message=
  "suporte mari: [nome do curso] lead disse: não sei se tenho tempo"
Villa retorna 2-3 sugestões prontas para Mari escolher e adaptar.

Periodicamente (scheduler semanal):
- Villa analisa todas as conversas acumuladas
- Atualiza banco de objeções com padrões e melhores respostas
- Gera relatório semanal para Caio/Thaís
"""

from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import (
    Client, ModuleCode, User,
    SDRConversation, SDRObjection,
)
from modules.base import BaseModule
from memory.feedback_loop import FeedbackLoop


# ═══════════════════════════════════════════════════
# SYSTEM PROMPTS
# ═══════════════════════════════════════════════════

SYSTEM_SUGGEST = """Você é o Villa, assistente da Mari na WebXP Agency.

A Mari faz SDR (vendas) de cursos odontológicos para dentistas. Ela NÃO é dentista — tem dificuldade com objeções técnicas.

Seu papel: dado o que o lead disse, gerar sugestões de resposta que a Mari possa usar ou adaptar.

## Regras obrigatórias
1. Máximo 2 frases por sugestão
2. Tom: humano, caloroso, consultivo — não robótico nem de vendedor agressivo
3. Sem ponto final no fim (padrão WhatsApp)
4. Citar dados concretos quando possível (resultados do curso, depoimentos, mercado)
5. Nunca mentir ou inventar dados sobre o curso
6. Se não tem informação suficiente, criar resposta que valida a objeção e convida ao diálogo
7. Gerar 3 opções com diferentes abordagens (empática / prática / social proof)

## Formato de resposta
Retorne APENAS um JSON com o campo "suggestions": lista de 3 strings.
Sem explicações, sem markdown, só o JSON.
"""

SYSTEM_ANALYZE = """Você é o Villa, analista de padrões de vendas da WebXP Agency.

Vou te dar uma conversa entre a Mari (SDR) e um lead de curso odontológico.
Extraia:

1. Objeções presentes: quais foram as resistências do lead?
2. Categoria de cada objeção: "preco" | "tempo" | "credibilidade" | "tecnica" | "urgencia" | "outro"
3. Resposta que a Mari deu (se houver)
4. Outcome da conversa: "won" | "lost" | "pending" | "unknown"
5. Objeção principal (a mais determinante)

Retorne APENAS um JSON com os campos:
{
  "outcome": "...",
  "main_objection": "...",
  "objections": [
    {"text": "...", "category": "...", "mari_response": "..."}
  ]
}
Sem markdown, sem explicações.
"""

SYSTEM_REPORT = """Você é o Villa, analista de vendas da WebXP Agency.

Vou te dar o banco de objeções extraído de conversas da Mari com leads de cursos odontológicos.
Gere um relatório executivo para Caio e Thaís com:

1. Top 5 objeções mais frequentes (com frequência e taxa de conversão)
2. Curso com mais objeções de preço / credibilidade / tempo
3. Recomendações de conteúdo ou script para as objeções mais críticas
4. Pontos cegos: objeções onde a Mari tem menor taxa de resposta efetiva

Tom: direto, orientado a ação. Como um gerente de vendas apresentando o briefing semanal.
"""


# ═══════════════════════════════════════════════════
# SUGGEST PROMPT
# ═══════════════════════════════════════════════════

SUGGEST_PROMPT = """Gere 3 sugestões de resposta para a Mari enviar ao lead.

CURSO: {course_name}
LEAD: {lead_context}
O QUE O LEAD DISSE: "{lead_message}"

HISTÓRICO RELEVANTE (objeções similares no passado):
{objection_history}

Retorne APENAS o JSON com "suggestions": [str, str, str].
"""

ANALYZE_PROMPT = """Analise a conversa abaixo e extraia objeções e outcome.

CURSO: {course_name}
CONVERSA:
{conversation_text}

Retorne APENAS o JSON pedido no system prompt.
"""

REPORT_PROMPT = """Gere o relatório executivo de objeções da Mari.

PERÍODO: {period}
TOTAL DE CONVERSAS ANALISADAS: {total_conversations}
BANCO DE OBJEÇÕES:
{objections_data}

DISTRIBUIÇÃO DE OUTCOMES:
{outcomes_data}
"""


class M14SuporteMari(BaseModule):
    """SDR assistant: monitoramento silencioso + sugestões em tempo real."""

    code = ModuleCode.M14_SUPORTE_MARI
    name = "Suporte Mari"
    description = (
        "Copiloto da Mari no SDR de cursos odontológicos. "
        "Ingere conversas, gera sugestões de resposta em tempo real, "
        "constrói banco de objeções e gera relatórios semanais."
    )

    KEYWORDS = [
        "suporte mari", "mari", "sdr",
        "sugestão", "sugestao", "sugere",
        "objeção", "objecao", "objeções",
        "lead disse", "resposta para",
        "como responder", "o que falar",
        "banco de objeções", "relatório sdr",
    ]

    MODE_KEYWORDS = {
        "suggest": ["sugestão", "sugestao", "sugere", "como responder", "o que falar", "lead disse", "resposta para"],
        "ingest": ["conversa", "importar", "registrar conversa", "cole aqui"],
        "analyze": ["analisar conversas", "processar", "atualizar banco"],
        "report": [
            "relatório sdr", "relatorio sdr",
            "relatório de objeções", "relatorio de objecoes",
            "banco de objeções", "banco de objecoes",
            "relatório de objeção", "relatorio de objecao",
        ],
    }

    async def can_handle(self, message: str, context: Optional[dict] = None) -> float:
        msg_lower = message.lower()

        # Evento direto do scheduler
        if context and context.get("event_type") in ("scheduler_weekly_sdr_analyze",):
            return 0.98

        # Trigger explícito
        if "suporte mari" in msg_lower:
            return 0.95

        matches = sum(1 for kw in self.KEYWORDS if kw in msg_lower)
        if matches >= 2:
            return 0.85
        if matches >= 1:
            return 0.6
        return 0.0

    async def execute(
        self,
        message: str,
        db: AsyncSession,
        user: Optional[User] = None,
        client_slug: Optional[str] = None,
        context: Optional[dict] = None,
    ) -> dict:
        context = context or {}
        msg_lower = message.lower()

        # Detectar modo
        mode = context.get("mode") or self._detect_mode(msg_lower)

        if mode == "suggest":
            return await self._suggest(message, db, client_slug, context)
        elif mode == "ingest":
            return await self._ingest(message, db, client_slug, context)
        elif mode == "analyze":
            return await self._analyze_all(db, client_slug)
        elif mode == "report":
            return await self._generate_report(db, client_slug)
        else:
            # Default: modo suggest
            return await self._suggest(message, db, client_slug, context)

    # ═══════════════════════════════════════════════════
    # MODO SUGGEST — geração de sugestões em tempo real
    # ═══════════════════════════════════════════════════

    async def _suggest(
        self,
        message: str,
        db: AsyncSession,
        client_slug: Optional[str],
        context: dict,
    ) -> dict:
        """
        Dado o que o lead disse, gera 3 sugestões de resposta para a Mari.

        Formato do comando:
            "suporte mari: [Curso X] lead disse: não tenho tempo agora"
        """
        # Extrair curso e mensagem do lead
        course_name = context.get("course_name") or self._extract_course(message)
        lead_message = context.get("lead_message") or self._extract_lead_message(message)
        lead_context = context.get("lead_context", "Sem contexto adicional")

        if not lead_message:
            return {
                "success": False,
                "message": (
                    "Não encontrei o que o lead disse. Use o formato:\n"
                    "\"suporte mari: [Nome do Curso] lead disse: [mensagem do lead]\""
                ),
                "actions_taken": ["format_error"],
            }

        # Buscar objeções similares no banco
        objection_history = await self._get_similar_objections(
            db, lead_message, course_name, limit=3
        )
        history_text = self._format_objection_history(objection_history)

        # Chamar Claude para sugestões
        response = await self.ask_claude(
            message=SUGGEST_PROMPT.format(
                course_name=course_name or "Curso não especificado",
                lead_context=lead_context,
                lead_message=lead_message,
                objection_history=history_text,
            ),
            db=db,
            system_override=SYSTEM_SUGGEST,
            client_slug=client_slug,
            model="primary",
        )

        # Parsear JSON de sugestões
        import json
        try:
            raw = response["text"].strip()
            # Remove possíveis backticks de markdown
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            parsed = json.loads(raw.strip())
            suggestions = parsed.get("suggestions", [])
        except (json.JSONDecodeError, KeyError):
            # Fallback: dividir por newlines
            suggestions = [line.strip() for line in response["text"].split("\n") if line.strip()][:3]

        # Formatar resposta para Mari
        formatted = self._format_suggestions_for_mari(
            lead_message=lead_message,
            course_name=course_name,
            suggestions=suggestions,
        )

        return {
            "success": True,
            "message": formatted,
            "data": {
                "suggestions": suggestions,
                "course_name": course_name,
                "lead_message": lead_message,
                "history_used": len(objection_history),
            },
            "actions_taken": ["suggestions_generated"],
            "tokens_used": response.get("tokens_input", 0) + response.get("tokens_output", 0),
        }

    # ═══════════════════════════════════════════════════
    # MODO INGEST — ingesta de conversa para análise
    # ═══════════════════════════════════════════════════

    async def _ingest(
        self,
        message: str,
        db: AsyncSession,
        client_slug: Optional[str],
        context: dict,
    ) -> dict:
        """
        Salva uma conversa da Mari no banco para análise posterior.
        A conversa pode ser passada como texto bruto (paste do WhatsApp Web).
        """
        raw_text = context.get("conversation_text") or message
        course_name = context.get("course_name") or self._extract_course(message)
        lead_name = context.get("lead_name")
        lead_source = context.get("lead_source", "whatsapp")

        # Buscar client_id
        client_id = None
        if client_slug:
            result = await db.execute(
                select(Client.id).where(Client.slug == client_slug)
            )
            client_id = result.scalar_one_or_none()

        # Salvar conversa bruta
        conv = SDRConversation(
            id=str(uuid4()),
            client_id=client_id,
            lead_name=lead_name,
            course_name=course_name,
            lead_source=lead_source,
            raw_text=raw_text,
            messages=[],  # Parsing detalhado feito no analyze
            outcome="pending",
        )
        db.add(conv)
        await db.flush()

        # Fazer análise imediata em background
        try:
            analysis = await self._analyze_single_conversation(conv, db, client_slug)
            conv.objections_extracted = analysis.get("objections", [])
            conv.main_objection = analysis.get("main_objection")
            conv.outcome = analysis.get("outcome", "pending")
            conv.analyzed_at = datetime.utcnow()
            await db.flush()

            # Atualizar banco de objeções
            if conv.objections_extracted:
                await self._update_objection_db(
                    db, conv.objections_extracted, course_name, conv.outcome, client_id
                )

            actions = ["conversation_saved", "analyzed", "objections_updated"]
        except Exception:
            actions = ["conversation_saved", "analysis_pending"]

        return {
            "success": True,
            "message": (
                f"✅ Conversa registrada (ID: {conv.id[:8]}). "
                f"Objeções encontradas: {len(conv.objections_extracted or [])}. "
                f"Outcome: {conv.outcome}."
            ),
            "data": {
                "conversation_id": conv.id,
                "course_name": course_name,
                "objections_found": len(conv.objections_extracted or []),
                "outcome": conv.outcome,
            },
            "actions_taken": actions,
        }

    # ═══════════════════════════════════════════════════
    # MODO ANALYZE — processa conversas não analisadas
    # ═══════════════════════════════════════════════════

    async def _analyze_all(self, db: AsyncSession, client_slug: Optional[str]) -> dict:
        """Processa todas as conversas ainda não analisadas e atualiza o banco."""
        result = await db.execute(
            select(SDRConversation)
            .where(SDRConversation.analyzed_at.is_(None))
            .order_by(SDRConversation.created_at.asc())
            .limit(50)
        )
        conversations = result.scalars().all()

        analyzed = 0
        errors = 0

        for conv in conversations:
            try:
                analysis = await self._analyze_single_conversation(conv, db, client_slug)
                conv.objections_extracted = analysis.get("objections", [])
                conv.main_objection = analysis.get("main_objection")
                conv.outcome = analysis.get("outcome", "pending")
                conv.analyzed_at = datetime.utcnow()

                if conv.objections_extracted:
                    client_id = conv.client_id
                    await self._update_objection_db(
                        db, conv.objections_extracted, conv.course_name, conv.outcome, client_id
                    )
                analyzed += 1
            except Exception:
                errors += 1

        await db.flush()

        return {
            "success": True,
            "message": f"Análise concluída: {analyzed} conversas processadas ({errors} erros).",
            "data": {"analyzed": analyzed, "errors": errors, "total": len(conversations)},
            "actions_taken": ["batch_analyze_complete"],
        }

    async def _analyze_single_conversation(
        self, conv: SDRConversation, db: AsyncSession, client_slug: Optional[str]
    ) -> dict:
        """Analisa uma conversa e retorna objeções + outcome."""
        import json
        text = conv.raw_text or ""
        if not text.strip():
            return {"outcome": "unknown", "objections": [], "main_objection": None}

        response = await self.ask_claude(
            message=ANALYZE_PROMPT.format(
                course_name=conv.course_name or "não especificado",
                conversation_text=text[:3000],
            ),
            db=db,
            system_override=SYSTEM_ANALYZE,
            client_slug=client_slug,
            model="fast",  # Haiku — tarefa de extração, não precisa do modelo principal
        )

        raw = response["text"].strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())

    # ═══════════════════════════════════════════════════
    # MODO REPORT — relatório executivo de objeções
    # ═══════════════════════════════════════════════════

    async def _generate_report(self, db: AsyncSession, client_slug: Optional[str]) -> dict:
        """Gera relatório executivo do banco de objeções."""
        # Buscar objeções ordenadas por frequência
        result = await db.execute(
            select(SDRObjection)
            .order_by(SDRObjection.frequency.desc())
            .limit(30)
        )
        objections = result.scalars().all()

        if not objections:
            return {
                "success": True,
                "message": "Nenhuma objeção registrada ainda. Inicie ingestão de conversas com modo 'ingest'.",
                "actions_taken": ["report_empty"],
            }

        # Buscar distribuição de outcomes
        outcome_result = await db.execute(
            select(SDRConversation.outcome, func.count(SDRConversation.id))
            .group_by(SDRConversation.outcome)
        )
        outcomes = dict(outcome_result.all())

        # Montar dados para o Claude
        objections_data = "\n".join(
            f"- [{obj.category.upper()}] \"{obj.objection_text}\" "
            f"(freq: {obj.frequency}, won: {obj.won_with_this_objection}, lost: {obj.lost_with_this_objection})"
            + (f" | Melhor resposta: \"{(obj.best_responses or [{}])[0].get('text', '')}\"" if obj.best_responses else "")
            for obj in objections[:20]
        )

        outcomes_data = "\n".join(f"- {k}: {v}" for k, v in outcomes.items())

        total_convs_result = await db.execute(
            select(func.count(SDRConversation.id))
        )
        total = total_convs_result.scalar_one_or_none() or 0

        response = await self.ask_claude(
            message=REPORT_PROMPT.format(
                period="últimas 4 semanas",
                total_conversations=total,
                objections_data=objections_data,
                outcomes_data=outcomes_data,
            ),
            db=db,
            system_override=SYSTEM_REPORT,
            client_slug=client_slug,
        )

        feedback_loop = FeedbackLoop(db)
        await feedback_loop.record_decision(
            module=self.code,
            action="generate_sdr_report",
            input_data={"total_objections": len(objections), "total_conversations": total},
            output_data={"report_length": len(response["text"])},
            client_slug=client_slug,
        )

        return {
            "success": True,
            "message": response["text"],
            "data": {
                "total_objections": len(objections),
                "total_conversations": total,
                "top_categories": self._top_categories(objections),
            },
            "actions_taken": ["sdr_report_generated"],
            "tokens_used": response.get("tokens_input", 0) + response.get("tokens_output", 0),
        }

    # ═══════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════

    def _detect_mode(self, msg_lower: str) -> str:
        for mode, keywords in self.MODE_KEYWORDS.items():
            if any(kw in msg_lower for kw in keywords):
                return mode
        return "suggest"

    def _extract_course(self, message: str) -> Optional[str]:
        """Extrai nome do curso do comando. Ex: '[Implante Avançado] lead disse:'"""
        import re
        match = re.search(r"\[(.+?)\]", message)
        if match:
            return match.group(1)
        # Tentar após ":"
        parts = message.split(":")
        if len(parts) > 1:
            candidate = parts[1].strip().split("lead")[0].strip()
            if candidate and len(candidate) < 100:
                return candidate
        return None

    def _extract_lead_message(self, message: str) -> Optional[str]:
        """Extrai o que o lead disse. Ex: 'lead disse: não tenho tempo'"""
        lower = message.lower()
        for marker in ["lead disse:", "lead falou:", "lead: ", "ele disse:", "ela disse:"]:
            if marker in lower:
                idx = lower.index(marker) + len(marker)
                return message[idx:].strip()
        return None

    async def _get_similar_objections(
        self, db: AsyncSession, lead_message: str, course_name: Optional[str], limit: int = 3
    ) -> list:
        """Busca objeções similares no banco (busca por keywords simples por ora)."""
        words = set(lead_message.lower().split())
        result = await db.execute(
            select(SDRObjection).order_by(SDRObjection.frequency.desc()).limit(50)
        )
        objections = result.scalars().all()

        # Score por palavras em comum
        scored = []
        for obj in objections:
            obj_words = set(obj.objection_text.lower().split())
            overlap = len(words & obj_words)
            if overlap > 0:
                scored.append((overlap, obj))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [obj for _, obj in scored[:limit]]

    def _format_objection_history(self, objections: list) -> str:
        if not objections:
            return "(sem histórico de objeções similares ainda)"
        lines = []
        for obj in objections:
            win_rate = 0
            if obj.won_with_this_objection + obj.lost_with_this_objection > 0:
                total = obj.won_with_this_objection + obj.lost_with_this_objection
                win_rate = round(obj.won_with_this_objection / total * 100)
            lines.append(
                f"• \"{obj.objection_text}\" ({obj.category}) — {obj.frequency}x | "
                f"win rate: {win_rate}%"
            )
            if obj.best_responses:
                best = obj.best_responses[0].get("text", "")
                if best:
                    lines.append(f"  Melhor resposta: \"{best[:150]}\"")
        return "\n".join(lines)

    async def _update_objection_db(
        self,
        db: AsyncSession,
        objections: list,
        course_name: Optional[str],
        outcome: str,
        client_id: Optional[str],
    ) -> None:
        """Atualiza ou cria entradas no banco de objeções."""
        for obj_data in objections:
            obj_text = obj_data.get("text", "").strip()
            if not obj_text:
                continue
            category = obj_data.get("category", "outro")

            # Buscar se já existe objeção similar
            result = await db.execute(
                select(SDRObjection)
                .where(SDRObjection.category == category)
                .where(SDRObjection.course_name == course_name)
                .where(SDRObjection.client_id == client_id)
                .limit(5)
            )
            existing = result.scalars().all()

            # Simples check de duplicidade por palavras
            matched = None
            obj_words = set(obj_text.lower().split())
            for ex in existing:
                ex_words = set(ex.objection_text.lower().split())
                if len(obj_words & ex_words) / max(len(obj_words), 1) > 0.5:
                    matched = ex
                    break

            if matched:
                matched.frequency += 1
                if outcome == "won":
                    matched.won_with_this_objection += 1
                elif outcome == "lost":
                    matched.lost_with_this_objection += 1
            else:
                new_obj = SDRObjection(
                    id=str(uuid4()),
                    client_id=client_id,
                    course_name=course_name,
                    category=category,
                    objection_text=obj_text,
                    frequency=1,
                    won_with_this_objection=1 if outcome == "won" else 0,
                    lost_with_this_objection=1 if outcome == "lost" else 0,
                )
                db.add(new_obj)

        await db.flush()

    def _format_suggestions_for_mari(
        self, lead_message: str, course_name: Optional[str], suggestions: list
    ) -> str:
        lines = []
        if course_name:
            lines.append(f"📌 *{course_name}*")
        lines.append(f'💬 Lead: _{lead_message}_')
        lines.append("")
        lines.append("✍️ *Sugestões de resposta:*")
        lines.append("")
        for i, s in enumerate(suggestions, 1):
            lines.append(f"*{i}.* {s}")
            lines.append("")
        lines.append("_Escolha uma, adapte e envie. Se nenhuma servir, me manda o contexto completo._")
        return "\n".join(lines)

    def _top_categories(self, objections: list) -> dict:
        counts = {}
        for obj in objections:
            counts[obj.category] = counts.get(obj.category, 0) + obj.frequency
        return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))

    async def _resolve_client(self, db: AsyncSession, slug: Optional[str]) -> Optional[str]:
        if not slug:
            return None
        result = await db.execute(select(Client.id).where(Client.slug == slug))
        return result.scalar_one_or_none()
