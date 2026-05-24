"""
Villa — M03 Qualificação: Lead Scoring
Avalia leads em 4 dimensões e decide próxima ação.

Dimensões (0-25 pontos cada, total 0-100):
    1. Compatibilidade com ICP
    2. Intenção de compra
    3. Capacidade financeira
    4. Timing (momento certo)

Classificação:
    80-100: Qualificado (transferir para humano)
    60-79:  Em qualificação (continuar conversa)
    40-59:  Nurturing (nutrir com conteúdo)
    0-39:   Desqualificado
"""



from core.models import Client, Lead, LeadStatus
from integrations.anthropic_client import claude
from modules.m03_qualificacao.prompts import SCORING_PROMPT

# Thresholds de qualificação
SCORE_QUALIFIED = 80
SCORE_QUALIFYING = 60
SCORE_NURTURING = 40


class LeadScorer:
    """
    Motor de lead scoring do Villa.
    
    Uso:
        scorer = LeadScorer()
        result = await scorer.score_lead(
            client=client,
            lead=lead,
            conversation_history=[...],
        )
        
        if result["qualification"] == "qualified":
            # Transferir para humano
        elif result["qualification"] == "disqualified":
            # Desqualificar
    """

    async def score_lead(
        self,
        client: Client,
        lead: Lead,
        conversation_history: list[dict],
    ) -> dict:
        """
        Avalia o lead com base na conversa e retorna score + ação recomendada.
        """
        client_config = client.config or {}

        # Montar histórico formatado
        history_text = self._format_conversation(conversation_history)

        # Montar dados do formulário
        form_data = lead.raw_data or {}
        form_text = ", ".join(f"{k}: {v}" for k, v in form_data.items() if not k.startswith("_"))

        prompt = SCORING_PROMPT.format(
            client_name=client.name,
            specialty=client.specialty or "odontologia",
            offer=client_config.get("offer", "serviços odontológicos"),
            icp=client_config.get("icp", "pacientes interessados em procedimentos odontológicos"),
            lead_name=lead.name or "não informado",
            lead_source=lead.source or "não identificado",
            form_data=form_text or "sem dados de formulário",
            conversation_history=history_text,
        )

        response = await claude.extract_json(
            message=prompt,
            model="primary",
        )

        result = response.get("data")
        if not result:
            return {
                "total_score": 0,
                "qualification": "error",
                "reasoning": "Falha ao processar scoring",
                "recommended_action": "continue_qualifying",
                "tokens_used": response.get("tokens_input", 0) + response.get("tokens_output", 0),
            }

        total = result.get("total_score", 0)

        # Aplicar thresholds customizados do cliente se existirem
        thresh_qualified = client_config.get("thresholds", {}).get("score_qualified", SCORE_QUALIFIED)
        thresh_qualifying = client_config.get("thresholds", {}).get("score_qualifying", SCORE_QUALIFYING)
        thresh_nurturing = client_config.get("thresholds", {}).get("score_nurturing", SCORE_NURTURING)

        # Classificar
        if total >= thresh_qualified:
            qualification = "qualified"
        elif total >= thresh_qualifying:
            qualification = "qualifying"
        elif total >= thresh_nurturing:
            qualification = "nurturing"
        else:
            qualification = "disqualified"

        # Override da classificação se o Claude sugeriu transferência
        if result.get("recommended_action") == "transfer_to_human" and total >= thresh_qualifying:
            qualification = "qualified"

        result["qualification"] = qualification
        result["tokens_used"] = response.get("tokens_input", 0) + response.get("tokens_output", 0)
        result["cost_usd"] = response.get("cost_usd", 0)

        return result

    def determine_lead_status(self, qualification: str) -> LeadStatus:
        """Converte classificação de qualificação para status do lead no banco."""
        mapping = {
            "qualified": LeadStatus.QUALIFIED,
            "qualifying": LeadStatus.QUALIFYING,
            "nurturing": LeadStatus.CONTACTED,
            "disqualified": LeadStatus.DISQUALIFIED,
        }
        return mapping.get(qualification, LeadStatus.QUALIFYING)

    def _format_conversation(self, messages: list[dict]) -> str:
        """Formata histórico de conversa para o prompt."""
        if not messages:
            return "(nenhuma mensagem ainda)"

        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            timestamp = msg.get("timestamp", "")

            if role == "lead":
                lines.append(f"LEAD: {content}")
            elif role == "villa":
                lines.append(f"VILLA: {content}")
            elif role == "human":
                lines.append(f"ATENDENTE: {content}")

        return "\n".join(lines)
