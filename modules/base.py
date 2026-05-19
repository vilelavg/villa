"""
Villa — Classe Base de Módulos
Todos os 13 módulos herdam desta classe.
Define a interface padrão: execute, can_handle, get_status.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.models import ModuleCode, ModuleConfig, DecisionLog, User
from integrations.anthropic_client import AnthropicClient, claude
from security.audit_log import AuditService


class BaseModule(ABC):
    """
    Classe base para todos os módulos do Villa.
    
    Cada módulo implementa:
        - code: identificador do módulo (M01, M02, etc.)
        - name: nome legível
        - description: o que o módulo faz
        - execute(): lógica principal
        - can_handle(): se consegue lidar com determinado comando/evento
    
    A classe base fornece:
        - Acesso ao Claude API
        - Logging de decisões (feedback loop)
        - Audit log automático
        - Carregamento de config e prompt do banco
        - Controle de execução (ativo/inativo)
    """

    # ── Subclasses DEVEM definir ──
    code: ModuleCode
    name: str
    description: str

    def __init__(self):
        self.claude: AnthropicClient = claude

    # ═══════════════════════════════════════════════════
    # INTERFACE OBRIGATÓRIA
    # ═══════════════════════════════════════════════════

    @abstractmethod
    async def execute(
        self,
        message: str,
        db: AsyncSession,
        user: Optional[User] = None,
        client_slug: Optional[str] = None,
        context: Optional[dict] = None,
    ) -> dict:
        """
        Executa a tarefa principal do módulo.
        
        Args:
            message: Comando ou dados de entrada
            db: Sessão do banco de dados
            user: Usuário que disparou (None se scheduler/webhook)
            client_slug: Slug do cliente alvo (se aplicável)
            context: Dados adicionais de contexto
            
        Returns:
            dict com: success, message, data, actions_taken
        """
        ...

    @abstractmethod
    async def can_handle(self, message: str, context: Optional[dict] = None) -> float:
        """
        Retorna a confiança (0.0 a 1.0) de que este módulo
        é o correto para lidar com o comando/evento.
        
        O orquestrador chama can_handle() de todos os módulos ativos
        e roteia para o que retornar maior confiança.
        
        Args:
            message: Comando em linguagem natural ou tipo de evento
            context: Dados adicionais
            
        Returns:
            Float entre 0.0 (não consigo) e 1.0 (certeza absoluta)
        """
        ...

    # ═══════════════════════════════════════════════════
    # MÉTODOS FORNECIDOS PELA BASE
    # ═══════════════════════════════════════════════════

    async def ask_claude(
        self,
        message: str,
        db: AsyncSession,
        system_override: Optional[str] = None,
        model: str = "primary",
        client_slug: Optional[str] = None,
        **kwargs,
    ) -> dict:
        """
        Envia mensagem ao Claude com system prompt do módulo
        e registra a decisão no feedback loop.
        """
        # Carregar system prompt (do banco ou do arquivo)
        system = system_override or await self._get_system_prompt(db)

        # Chamar Claude
        response = await self.claude.ask(
            message=message,
            system=system,
            model=model,
            **kwargs,
        )

        # Registrar decisão no feedback loop
        await self._log_decision(
            db=db,
            action=f"claude_call_{self.code.value}",
            input_data={"message": message[:500], "model": model},
            output_data={"text": response["text"][:1000], "stop_reason": response["stop_reason"]},
            tokens_input=response["tokens_input"],
            tokens_output=response["tokens_output"],
            model_used=response["model"],
            cost_usd=response["cost_usd"],
            client_slug=client_slug,
        )

        return response

    async def get_config(self, db: AsyncSession) -> dict:
        """Carrega a configuração do módulo do banco."""
        result = await db.execute(
            select(ModuleConfig).where(ModuleConfig.module == self.code)
        )
        config = result.scalar_one_or_none()
        if config:
            return config.config or {}
        return {}

    async def get_training_data(self, db: AsyncSession) -> Optional[dict]:
        """Carrega dados de treinamento (exemplos, templates, referências)."""
        result = await db.execute(
            select(ModuleConfig).where(ModuleConfig.module == self.code)
        )
        config = result.scalar_one_or_none()
        if config:
            return config.training_data
        return None

    async def is_active(self, db: AsyncSession) -> bool:
        """Verifica se o módulo está ativo."""
        result = await db.execute(
            select(ModuleConfig.is_active).where(ModuleConfig.module == self.code)
        )
        active = result.scalar_one_or_none()
        return active is True

    async def get_status(self, db: AsyncSession) -> dict:
        """Retorna status completo do módulo."""
        result = await db.execute(
            select(ModuleConfig).where(ModuleConfig.module == self.code)
        )
        config = result.scalar_one_or_none()

        return {
            "module": self.code.value,
            "name": self.name,
            "description": self.description,
            "is_active": config.is_active if config else False,
            "execution_count": config.execution_count if config else 0,
            "error_count": config.error_count if config else 0,
            "last_executed_at": config.last_executed_at.isoformat() if config and config.last_executed_at else None,
            "has_training_data": bool(config.training_data) if config else False,
        }

    async def increment_execution(self, db: AsyncSession, success: bool = True) -> None:
        """Incrementa contador de execuções do módulo."""
        if success:
            await db.execute(
                update(ModuleConfig)
                .where(ModuleConfig.module == self.code)
                .values(
                    execution_count=ModuleConfig.execution_count + 1,
                    last_executed_at=datetime.utcnow(),
                )
            )
        else:
            await db.execute(
                update(ModuleConfig)
                .where(ModuleConfig.module == self.code)
                .values(error_count=ModuleConfig.error_count + 1)
            )

    async def get_past_decisions(
        self,
        db: AsyncSession,
        action: Optional[str] = None,
        client_slug: Optional[str] = None,
        outcome: Optional[str] = None,
        limit: int = 10,
    ) -> list[dict]:
        """
        Consulta decisões passadas deste módulo.
        Usado pelo feedback loop — o módulo consulta o que deu certo/errado
        antes de tomar novas decisões.
        """
        query = (
            select(DecisionLog)
            .where(DecisionLog.module == self.code)
            .order_by(DecisionLog.created_at.desc())
            .limit(limit)
        )
        if action:
            query = query.where(DecisionLog.action == action)
        if outcome:
            query = query.where(DecisionLog.outcome == outcome)

        result = await db.execute(query)
        decisions = result.scalars().all()

        return [
            {
                "action": d.action,
                "input": d.input_data,
                "output": d.output_data,
                "reasoning": d.reasoning,
                "outcome": d.outcome,
                "human_feedback": d.human_feedback,
                "created_at": d.created_at.isoformat(),
            }
            for d in decisions
        ]

    # ═══════════════════════════════════════════════════
    # MÉTODOS INTERNOS
    # ═══════════════════════════════════════════════════

    async def _get_system_prompt(self, db: AsyncSession) -> str:
        """Carrega system prompt: banco > arquivo > default."""
        # Tentar do banco
        result = await db.execute(
            select(ModuleConfig.system_prompt).where(ModuleConfig.module == self.code)
        )
        prompt = result.scalar_one_or_none()
        if prompt:
            return prompt

        # Tentar do arquivo
        prompt_file = f"prompts/modules/{self.code.value}.md"
        try:
            with open(prompt_file) as f:
                return f.read()
        except FileNotFoundError:
            pass

        # Default genérico
        return (
            f"Você é o Villa, módulo {self.name} ({self.code.value}). "
            f"{self.description} "
            "Você trabalha para a WebXP, agência de performance odontológica. "
            "Seja preciso, direto e use o tom profissional da WebXP."
        )

    async def _log_decision(
        self,
        db: AsyncSession,
        action: str,
        input_data: Optional[dict] = None,
        output_data: Optional[dict] = None,
        reasoning: Optional[str] = None,
        tokens_input: Optional[int] = None,
        tokens_output: Optional[int] = None,
        model_used: Optional[str] = None,
        cost_usd: Optional[float] = None,
        client_slug: Optional[str] = None,
    ) -> None:
        """Registra uma decisão no feedback loop."""
        # Resolver client_id a partir do slug
        client_id = None
        if client_slug:
            from core.models import Client
            result = await db.execute(
                select(Client.id).where(Client.slug == client_slug)
            )
            client_id = result.scalar_one_or_none()

        entry = DecisionLog(
            id=str(uuid4()),
            module=self.code,
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
        db.add(entry)
        await db.flush()
