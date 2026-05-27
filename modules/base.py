"""
Villa — Classe Base de Módulos
Todos os 13 módulos herdam desta classe.
Define a interface padrão: execute, can_handle, get_status.
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import DecisionLog, ModuleCode, ModuleConfig, User
from integrations.anthropic_client import AnthropicClient, claude

logger = logging.getLogger(__name__)

# ── Constantes ──
# Limite de caracteres do Client OS narrative injetado no system prompt.
# Acima disso, o narrative e truncado para controle de tokens.
CLIENT_OS_NARRATIVE_MAX_CHARS = 2000


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
        - Enriquecimento automático com Client OS quando client_slug e passado
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
        user: User | None = None,
        client_slug: str | None = None,
        context: dict | None = None,
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
    async def can_handle(self, message: str, context: dict | None = None) -> float:
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
        system_override: str | None = None,
        model: str = "primary",
        client_slug: str | None = None,
        **kwargs,
    ) -> dict:
        """
        Envia mensagem ao Claude com system prompt do módulo
        e registra a decisão no feedback loop.

        Quando client_slug e passado, o system prompt e automaticamente
        enriquecido com o narrative do Client OS para este cliente
        (fatos, episodios, preferencias, pendencias, objetivos).

        Se o Client OS estiver indisponivel ou o cliente nao tiver
        estado registrado, mantem comportamento original sem
        enriquecimento.
        """
        # Carregar system prompt (do banco ou do arquivo)
        system = system_override or await self._get_system_prompt(db)

        # Enriquecer com Client OS se cliente foi identificado
        system = await self._enrich_system_with_client_os(system, db, client_slug)

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
        result = await db.execute(select(ModuleConfig).where(ModuleConfig.module == self.code))
        config = result.scalar_one_or_none()
        if config:
            return config.config or {}
        return {}

    async def get_training_data(self, db: AsyncSession) -> dict | None:
        """Carrega dados de treinamento (exemplos, templates, referências)."""
        result = await db.execute(select(ModuleConfig).where(ModuleConfig.module == self.code))
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
        result = await db.execute(select(ModuleConfig).where(ModuleConfig.module == self.code))
        config = result.scalar_one_or_none()

        return {
            "module": self.code.value,
            "name": self.name,
            "description": self.description,
            "is_active": config.is_active if config else False,
            "execution_count": config.execution_count if config else 0,
            "error_count": config.error_count if config else 0,
            "last_executed_at": config.last_executed_at.isoformat()
            if config and config.last_executed_at
            else None,
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
        action: str | None = None,
        client_slug: str | None = None,
        outcome: str | None = None,
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

    async def _enrich_system_with_client_os(
        self,
        system: str,
        db: AsyncSession,
        client_slug: str | None,
    ) -> str:
        """
        Enriquece o system prompt com o narrative do Client OS, se
        disponivel.

        Comportamento defensivo: qualquer falha (cliente nao existe,
        Client OS indisponivel, modulo memory.client_os ausente, narrative
        vazio) resulta em retornar o system prompt original sem
        modificacao. Esta funcao NUNCA propaga excecoes.

        Args:
            system: system prompt original
            db: sessao do banco
            client_slug: slug do cliente alvo (None desativa enriquecimento)

        Returns:
            system prompt original ou enriquecido com bloco de contexto.
        """
        if not client_slug:
            return system

        try:
            # Import local para evitar dependencia circular
            from memory.client_os import ClientOS

            cos = await ClientOS.for_slug(db, client_slug)
            narrative = await cos.narrative()

            if not narrative or not narrative.strip():
                return system

            # Truncar para controle de tokens
            if len(narrative) > CLIENT_OS_NARRATIVE_MAX_CHARS:
                narrative = (
                    narrative[:CLIENT_OS_NARRATIVE_MAX_CHARS]
                    + "\n[...narrative truncado]"
                )

            return f"{system}\n\n## Contexto do cliente\n{narrative}"

        except Exception as e:
            # Esperado: ClientNotFoundError quando cliente nao tem estado,
            # ImportError em ambiente sem Client OS, ClientOSError em falhas
            # de banco. Em todos os casos: mantem comportamento original.
            logger.debug(
                "Client OS nao enriqueceu prompt do modulo %s para cliente '%s': %s",
                self.code.value,
                client_slug,
                e,
            )
            return system

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
        input_data: dict | None = None,
        output_data: dict | None = None,
        reasoning: str | None = None,
        tokens_input: int | None = None,
        tokens_output: int | None = None,
        model_used: str | None = None,
        cost_usd: float | None = None,
        client_slug: str | None = None,
    ) -> None:
        """Registra uma decisão no feedback loop."""
        # Resolver client_id a partir do slug
        client_id = None
        if client_slug:
            from core.models import Client

            result = await db.execute(select(Client.id).where(Client.slug == client_slug))
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
