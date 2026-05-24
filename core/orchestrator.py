"""
Villa — Orquestrador Central
Cérebro do Villa: recebe comandos e eventos, identifica qual módulo
deve atuar, delega a tarefa e consolida a resposta.

Dois modos de operação:
    1. COMANDO: Caio/Thaís enviam texto → Villa interpreta → módulo executa
    2. EVENTO: Webhook dispara → Villa identifica tipo → módulo reage
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import (
    Client,
    CommandResponse,
    ModuleCode,
    User,
    UserRole,
)
from core.permissions import permissions
from integrations.anthropic_client import claude
from modules.base import BaseModule
from security.audit_log import AuditService


class Orchestrator:
    """
    Orquestrador central do Villa.

    Responsabilidades:
        - Receber comandos em linguagem natural e rotear para o módulo correto
        - Receber eventos de webhooks e disparar ações automáticas
        - Consolidar respostas dos módulos
        - Validar permissões antes de executar
        - Registrar tudo no audit log

    Uso:
        orchestrator = Orchestrator()
        orchestrator.register_module(M01Roteiros())
        orchestrator.register_module(M02Relatorios())

        # Via comando
        result = await orchestrator.process_command("Gera roteiro pro Ottoboni", db, user)

        # Via evento
        result = await orchestrator.handle_event("inlead_new_lead", payload, db)
    """

    def __init__(self):
        self._modules: dict[ModuleCode, BaseModule] = {}
        self._event_routes: dict[str, list[ModuleCode]] = {}

    # ═══════════════════════════════════════════════════
    # REGISTRO DE MÓDULOS
    # ═══════════════════════════════════════════════════

    def register_module(self, module: BaseModule) -> None:
        """Registra um módulo no orquestrador."""
        self._modules[module.code] = module

    def register_event_route(self, event_type: str, modules: list[ModuleCode]) -> None:
        """
        Mapeia um tipo de evento para módulos que devem reagir.

        Ex:
            orchestrator.register_event_route("inlead_new_lead", [ModuleCode.M03_QUALIFICACAO])
            orchestrator.register_event_route("kommo_lead_status_changed", [
                ModuleCode.M05_AGENDAMENTO,
                ModuleCode.M02_RELATORIOS,
            ])
        """
        self._event_routes[event_type] = modules

    # ═══════════════════════════════════════════════════
    # PROCESSAMENTO DE COMANDOS (linguagem natural)
    # ═══════════════════════════════════════════════════

    async def process_command(
        self,
        message: str,
        db: AsyncSession,
        user: User,
        client_slug: str | None = None,
        module_hint: ModuleCode | None = None,
    ) -> CommandResponse:
        """
        Processa um comando em linguagem natural.

        Fluxo:
            1. Se module_hint foi dado, usa direto
            2. Senão, pede ao Claude para classificar o comando
            3. Verifica permissões do usuário
            4. Verifica se o módulo está ativo
            5. Executa o módulo
            6. Registra no audit log
        """
        audit = AuditService(db)

        # ── Passo 1: Resolver o módulo ──
        if module_hint and module_hint in self._modules:
            target_module = self._modules[module_hint]
            route_method = "hint"
        else:
            target_module, route_method = await self._route_command(message, db)

        if not target_module:
            return CommandResponse(
                success=False,
                message=(
                    "Não consegui identificar qual módulo deve lidar com esse comando. "
                    "Tente ser mais específico ou use /command/direct/{module}."
                ),
                actions_taken=["routing_failed"],
            )

        # ── Passo 2: Resolver o cliente (se mencionado) ──
        resolved_slug = client_slug or await self._extract_client(message, db)

        # ── Passo 3: Verificar permissões ──
        user_role = UserRole(user.role)
        if not permissions.can_access_module(user_role, target_module.code):
            await audit.log(
                action="command_permission_denied",
                user_id=user.id,
                module=target_module.code,
                details={"message": message[:200], "role": user.role},
                success=False,
            )
            return CommandResponse(
                success=False,
                message=f"Seu perfil ({user.role}) não tem acesso ao módulo {target_module.name}.",
                module_used=target_module.code.value,
                actions_taken=["permission_denied"],
            )

        # ── Passo 4: Verificar se módulo está ativo ──
        if not await target_module.is_active(db):
            return CommandResponse(
                success=False,
                message=f"O módulo {target_module.name} está desativado. Ative-o nas configurações.",
                module_used=target_module.code.value,
                actions_taken=["module_inactive"],
            )

        # ── Passo 5: Executar ──
        try:
            result = await target_module.execute(
                message=message,
                db=db,
                user=user,
                client_slug=resolved_slug,
                context={"route_method": route_method},
            )

            await target_module.increment_execution(db, success=True)

            await audit.log(
                action=f"command_executed_{target_module.code.value}",
                user_id=user.id,
                module=target_module.code,
                details={
                    "message": message[:200],
                    "client": resolved_slug,
                    "route_method": route_method,
                    "success": result.get("success", True),
                },
            )

            return CommandResponse(
                success=result.get("success", True),
                message=result.get("message", "Comando executado."),
                module_used=target_module.code.value,
                data=result.get("data"),
                actions_taken=result.get("actions_taken", []),
                tokens_used=result.get("tokens_used"),
            )

        except Exception as e:
            await target_module.increment_execution(db, success=False)

            await audit.log(
                action=f"command_error_{target_module.code.value}",
                user_id=user.id,
                module=target_module.code,
                details={"message": message[:200], "error": str(e)},
                success=False,
                error_message=str(e),
            )

            return CommandResponse(
                success=False,
                message=f"Erro ao executar {target_module.name}: {str(e)}",
                module_used=target_module.code.value,
                actions_taken=["execution_error"],
            )

    # ═══════════════════════════════════════════════════
    # PROCESSAMENTO DE EVENTOS (webhooks, scheduler)
    # ═══════════════════════════════════════════════════

    async def handle_event(
        self,
        event_type: str,
        payload: dict,
        db: AsyncSession,
    ) -> list[dict]:
        """
        Processa um evento de webhook ou scheduler.
        Pode disparar múltiplos módulos em sequência.

        Args:
            event_type: Tipo do evento (ex: "inlead_new_lead", "kommo_lead_status_changed")
            payload: Dados do evento
            db: Sessão do banco

        Returns:
            Lista de resultados (um por módulo acionado)
        """
        audit = AuditService(db)
        results = []

        # Buscar módulos mapeados para este evento
        target_codes = self._event_routes.get(event_type, [])

        if not target_codes:
            # Tentar roteamento inteligente
            target_codes = await self._route_event(event_type, payload, db)

        for code in target_codes:
            module = self._modules.get(code)
            if not module:
                continue

            if not await module.is_active(db):
                continue

            try:
                result = await module.execute(
                    message=event_type,
                    db=db,
                    context={"event_type": event_type, "payload": payload},
                )
                await module.increment_execution(db, success=True)

                await audit.log(
                    action=f"event_{event_type}_{code.value}",
                    module=code,
                    details={"event_type": event_type, "success": result.get("success")},
                )

                results.append({"module": code.value, **result})

            except Exception as e:
                await module.increment_execution(db, success=False)
                await audit.log(
                    action=f"event_error_{code.value}",
                    module=code,
                    details={"event_type": event_type, "error": str(e)},
                    success=False,
                    error_message=str(e),
                )
                results.append({"module": code.value, "success": False, "error": str(e)})

        return results

    # ═══════════════════════════════════════════════════
    # ROTEAMENTO INTELIGENTE
    # ═══════════════════════════════════════════════════

    async def _route_command(
        self,
        message: str,
        db: AsyncSession,
    ) -> tuple[BaseModule | None, str]:
        """
        Decide qual módulo deve lidar com o comando.

        Estratégia em 2 passos:
            1. Pede a cada módulo ativo sua confiança (can_handle)
            2. Se nenhum tem confiança alta, usa Claude para classificar
        """
        # Passo 1: Perguntar aos módulos
        scores: list[tuple[BaseModule, float]] = []
        for module in self._modules.values():
            if await module.is_active(db):
                confidence = await module.can_handle(message)
                if confidence > 0.1:
                    scores.append((module, confidence))

        scores.sort(key=lambda x: x[1], reverse=True)

        # Se algum módulo tem confiança >= 0.7, usa direto
        if scores and scores[0][1] >= 0.7:
            return scores[0][0], "can_handle"

        # Passo 2: Classificação via Claude (Haiku — rápido e barato)
        active_modules = {
            code.value: mod.description
            for code, mod in self._modules.items()
            if await mod.is_active(db)
        }

        if not active_modules:
            return None, "no_active_modules"

        modules_desc = "\n".join(f"- {code}: {desc}" for code, desc in active_modules.items())

        result = await claude.classify(
            text=message,
            categories=list(active_modules.keys()),
            system=(
                f"Classifique o comando do usuário no módulo correto.\n"
                f"Módulos disponíveis:\n{modules_desc}\n\n"
                f"Responda APENAS com o código do módulo (ex: m01_roteiros)."
            ),
        )

        category = result.get("category", "")
        for code_str, module in [(c.value, m) for c, m in self._modules.items()]:
            if code_str in category:
                return module, "claude_classification"

        # Se tiver scores do passo 1, usa o melhor mesmo com confiança baixa
        if scores:
            return scores[0][0], "best_effort"

        return None, "unresolved"

    async def _route_event(
        self,
        event_type: str,
        payload: dict,
        db: AsyncSession,
    ) -> list[ModuleCode]:
        """Roteamento inteligente de eventos não mapeados explicitamente."""
        routes = []

        # Heurísticas baseadas no tipo de evento
        if "inlead" in event_type or "new_lead" in event_type:
            routes.append(ModuleCode.M03_QUALIFICACAO)

        elif "kommo" in event_type:
            if "status_changed" in event_type:
                routes.append(ModuleCode.M03_QUALIFICACAO)
                routes.append(ModuleCode.M05_AGENDAMENTO)
            elif "lead_added" in event_type:
                routes.append(ModuleCode.M03_QUALIFICACAO)

        elif "whatsapp" in event_type:
            if "message" in event_type:
                routes.append(ModuleCode.M06_ATENDIMENTO)
                routes.append(ModuleCode.M03_QUALIFICACAO)

        elif "n8n" in event_type:
            if "capi" in event_type:
                routes.append(ModuleCode.M04_CAMPANHAS)
            elif "report" in event_type:
                routes.append(ModuleCode.M02_RELATORIOS)
            elif "inlead" in event_type or "new_lead" in event_type or "lead" in event_type:
                # Lead captado via N8N (InLead → N8N → Villa)
                routes.append(ModuleCode.M02_RELATORIOS)  # registrar no relatório
                routes.append(ModuleCode.M14_SUPORTE_MARI)  # analisar para banco SDR
            elif "kommo" in event_type:
                # Evento do Kommo roteado via N8N
                routes.append(ModuleCode.M02_RELATORIOS)

        elif "scheduler" in event_type:
            if "daily" in event_type:
                routes.append(ModuleCode.M02_RELATORIOS)
                routes.append(ModuleCode.M12_ALERTAS)
            elif "weekly" in event_type:
                routes.append(ModuleCode.M02_RELATORIOS)
                routes.append(ModuleCode.M07_RETROALIMENTACAO)

        return routes

    async def _extract_client(self, message: str, db: AsyncSession) -> str | None:
        """
        Tenta extrair o slug do cliente mencionado no comando.
        Busca nomes e slugs conhecidos no banco.
        """
        result = await db.execute(select(Client.name, Client.slug))
        clients = result.all()

        message_lower = message.lower()
        for name, slug in clients:
            if name.lower() in message_lower or slug.lower() in message_lower:
                return slug

        return None

    # ═══════════════════════════════════════════════════
    # STATUS E GERENCIAMENTO
    # ═══════════════════════════════════════════════════

    async def get_all_status(self, db: AsyncSession) -> list[dict]:
        """Retorna status de todos os módulos registrados."""
        statuses = []
        for module in self._modules.values():
            status = await module.get_status(db)
            statuses.append(status)
        return sorted(statuses, key=lambda s: s["module"])

    def get_registered_modules(self) -> list[str]:
        """Lista módulos registrados no orquestrador."""
        return [code.value for code in self._modules.keys()]

    def get_event_routes(self) -> dict[str, list[str]]:
        """Lista mapeamento de eventos para módulos."""
        return {
            event: [code.value for code in modules] for event, modules in self._event_routes.items()
        }


# ── Instância global ──
orchestrator = Orchestrator()


def setup_orchestrator() -> Orchestrator:
    """
    Configura o orquestrador com todos os módulos e event routes.
    Chamado no startup do FastAPI.

    TODO: Importar e registrar cada módulo conforme forem implementados.
    """
    # ── Registrar módulos ──
    from modules.m01_roteiros.agent import M01Roteiros

    orchestrator.register_module(M01Roteiros())

    from modules.m02_relatorios.agent import M02Relatorios

    orchestrator.register_module(M02Relatorios())

    from modules.m03_qualificacao.agent import M03Qualificacao

    orchestrator.register_module(M03Qualificacao())

    from modules.m04_campanhas.agent import M04Campanhas

    orchestrator.register_module(M04Campanhas())

    from modules.m05_agendamento.agent import M05Agendamento

    orchestrator.register_module(M05Agendamento())

    from modules.m06_atendimento.agent import M06Atendimento

    orchestrator.register_module(M06Atendimento())

    from modules.m07_retroalimentacao.agent import M07Retroalimentacao

    orchestrator.register_module(M07Retroalimentacao())

    from modules.m08_onboarding.agent import M08Onboarding

    orchestrator.register_module(M08Onboarding())

    from modules.m09_arquivos.agent import M09Arquivos

    orchestrator.register_module(M09Arquivos())

    from modules.m10_smooth.agent import M10Smooth

    orchestrator.register_module(M10Smooth())

    from modules.m11_hipoteses.agent import M11Hipoteses

    orchestrator.register_module(M11Hipoteses())

    from modules.m12_alertas.agent import M12Alertas

    orchestrator.register_module(M12Alertas())

    from modules.m14_suporte_mari.agent import M14SuporteMari

    orchestrator.register_module(M14SuporteMari())

    from modules.m15_monitor_smooth.agent import M15MonitorSmooth

    orchestrator.register_module(M15MonitorSmooth())

    # ── Registrar event routes ──
    orchestrator.register_event_route(
        "inlead_new_lead",
        [
            ModuleCode.M03_QUALIFICACAO,
        ],
    )
    orchestrator.register_event_route(
        "kommo_lead_status_changed",
        [
            ModuleCode.M03_QUALIFICACAO,
            ModuleCode.M05_AGENDAMENTO,
            ModuleCode.M02_RELATORIOS,
        ],
    )
    orchestrator.register_event_route(
        "kommo_lead_added",
        [
            ModuleCode.M03_QUALIFICACAO,
        ],
    )
    orchestrator.register_event_route(
        "whatsapp_message",
        [
            ModuleCode.M06_ATENDIMENTO,
        ],
    )
    orchestrator.register_event_route(
        "scheduler_daily",
        [
            ModuleCode.M02_RELATORIOS,
            ModuleCode.M12_ALERTAS,
            ModuleCode.M04_CAMPANHAS,
        ],
    )
    orchestrator.register_event_route(
        "scheduler_weekly",
        [
            ModuleCode.M02_RELATORIOS,
            ModuleCode.M07_RETROALIMENTACAO,
        ],
    )

    orchestrator.register_event_route(
        "smooth_group_message",
        [
            ModuleCode.M15_MONITOR_SMOOTH,
        ],
    )
    orchestrator.register_event_route(
        "scheduler_weekly_sdr_analyze",
        [
            ModuleCode.M14_SUPORTE_MARI,
        ],
    )
    orchestrator.register_event_route(
        "scheduler_weekly_smooth_insights",
        [
            ModuleCode.M15_MONITOR_SMOOTH,
        ],
    )

    # ── Eventos N8N (InLead → N8N → Villa) ──
    orchestrator.register_event_route(
        "n8n_inlead_new_lead",
        [
            ModuleCode.M02_RELATORIOS,
            ModuleCode.M14_SUPORTE_MARI,
        ],
    )
    orchestrator.register_event_route(
        "n8n_kommo_lead_updated",
        [
            ModuleCode.M02_RELATORIOS,
        ],
    )
    orchestrator.register_event_route(
        "n8n_capi_event",
        [
            ModuleCode.M04_CAMPANHAS,
        ],
    )
    orchestrator.register_event_route(
        "n8n_report_request",
        [
            ModuleCode.M02_RELATORIOS,
        ],
    )

    return orchestrator
