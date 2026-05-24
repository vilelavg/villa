"""
Villa — Sistema de Permissões (RBAC)
Controle de acesso baseado em roles para usuários da WebXP.

Roles:
    admin    → Caio, Thaís: acesso total
    operator → Ana Lívia, Mariana: usar módulos, ver dados de clientes atribuídos
    sdr      → Jasmyne: apenas qualificação (M3) e agendamento (M5)
    readonly → (futuro): apenas visualizar relatórios

Ações classificadas por risco:
    low    → automático (gerar roteiro, montar relatório, consultar base)
    medium → log + notificação (mover card, enviar mensagem, criar evento)
    high   → confirmação humana (alterar campanha, excluir dado, enviar proposta)
"""

from core.models import ActionRisk, ModuleCode, UserRole

# ═══════════════════════════════════════════════════════════════
# MAPA DE PERMISSÕES
# ═══════════════════════════════════════════════════════════════

# Quais módulos cada role pode acessar
MODULE_ACCESS: dict[UserRole, set[ModuleCode]] = {
    UserRole.ADMIN: set(ModuleCode),  # Todos os módulos
    UserRole.OPERATOR: {
        ModuleCode.M01_ROTEIROS,
        ModuleCode.M02_RELATORIOS,
        ModuleCode.M03_QUALIFICACAO,
        ModuleCode.M04_CAMPANHAS,
        ModuleCode.M05_AGENDAMENTO,
        ModuleCode.M06_ATENDIMENTO,
        ModuleCode.M08_ONBOARDING,
        ModuleCode.M09_ARQUIVOS,
        ModuleCode.M12_ALERTAS,
        ModuleCode.M13_CONHECIMENTO,
    },
    UserRole.SDR: {
        ModuleCode.M03_QUALIFICACAO,
        ModuleCode.M05_AGENDAMENTO,
        ModuleCode.M06_ATENDIMENTO,
    },
    UserRole.READONLY: set(),  # Nenhum módulo — apenas relatórios via API
}

# Ações que cada role pode executar
ACTION_ACCESS: dict[UserRole, set[str]] = {
    UserRole.ADMIN: {
        "read",
        "write",
        "delete",
        "configure",
        "manage_users",
        "view_audit",
        "confirm_high_risk",
        "export_data",
        "manage_modules",
    },
    UserRole.OPERATOR: {
        "read",
        "write",
        "generate_report",
        "generate_roteiro",
        "qualify_lead",
        "schedule_appointment",
        "send_message_template",
    },
    UserRole.SDR: {
        "read",
        "qualify_lead",
        "schedule_appointment",
        "view_lead_details",
    },
    UserRole.READONLY: {
        "read",
        "view_reports",
        "view_dashboards",
    },
}

# Classificação de risco por tipo de ação
ACTION_RISK_MAP: dict[str, ActionRisk] = {
    # Baixo risco — automático
    "generate_roteiro": ActionRisk.LOW,
    "generate_report": ActionRisk.LOW,
    "analyze_campaign": ActionRisk.LOW,
    "qualify_lead": ActionRisk.LOW,
    "query_knowledge_base": ActionRisk.LOW,
    "generate_hypothesis": ActionRisk.LOW,
    "read": ActionRisk.LOW,
    # Médio risco — log + notificação
    "move_card_kommo": ActionRisk.MEDIUM,
    "send_whatsapp_template": ActionRisk.MEDIUM,
    "create_calendar_event": ActionRisk.MEDIUM,
    "update_sheets": ActionRisk.MEDIUM,
    "send_capi_event": ActionRisk.MEDIUM,
    "schedule_appointment": ActionRisk.MEDIUM,
    "write": ActionRisk.MEDIUM,
    # Alto risco — confirmação humana obrigatória
    "modify_campaign": ActionRisk.HIGH,
    "pause_campaign": ActionRisk.HIGH,
    "scale_budget": ActionRisk.HIGH,
    "send_proposal": ActionRisk.HIGH,
    "delete_data": ActionRisk.HIGH,
    "export_sensitive_data": ActionRisk.HIGH,
    "modify_permissions": ActionRisk.HIGH,
    "send_custom_whatsapp": ActionRisk.HIGH,
    "delete": ActionRisk.HIGH,
    "configure": ActionRisk.HIGH,
}


# ═══════════════════════════════════════════════════════════════
# SERVIÇO DE PERMISSÕES
# ═══════════════════════════════════════════════════════════════


class PermissionService:
    """
    Verifica permissões de acesso para usuários do Villa.

    Uso:
        perms = PermissionService()

        if perms.can_access_module(user_role, ModuleCode.M01_ROTEIROS):
            # Executa o módulo

        if perms.can_execute(user_role, "modify_campaign"):
            risk = perms.get_action_risk("modify_campaign")
            if risk == ActionRisk.HIGH:
                # Solicitar confirmação humana antes de executar
    """

    def can_access_module(self, role: UserRole, module: ModuleCode) -> bool:
        """Verifica se o role tem acesso a um módulo específico."""
        return module in MODULE_ACCESS.get(role, set())

    def can_execute(self, role: UserRole, action: str) -> bool:
        """Verifica se o role pode executar uma ação específica."""
        allowed = ACTION_ACCESS.get(role, set())
        return action in allowed

    def get_action_risk(self, action: str) -> ActionRisk:
        """Retorna o nível de risco de uma ação."""
        return ACTION_RISK_MAP.get(action, ActionRisk.MEDIUM)

    def requires_confirmation(self, action: str) -> bool:
        """Verifica se a ação requer confirmação humana."""
        return self.get_action_risk(action) == ActionRisk.HIGH

    def get_accessible_modules(self, role: UserRole) -> list[ModuleCode]:
        """Retorna lista de módulos acessíveis para um role."""
        return sorted(MODULE_ACCESS.get(role, set()), key=lambda m: m.value)

    def get_allowed_actions(self, role: UserRole) -> set[str]:
        """Retorna set de ações permitidas para um role."""
        return ACTION_ACCESS.get(role, set())

    def check_or_raise(
        self,
        role: UserRole,
        action: str,
        module: ModuleCode | None = None,
    ) -> ActionRisk:
        """
        Verifica permissão e retorna o nível de risco.
        Levanta PermissionError se não autorizado.

        Returns:
            ActionRisk da ação (para o chamador decidir se precisa confirmação)
        """
        if not self.can_execute(role, action):
            raise PermissionError(
                f"Usuário com role '{role.value}' não tem permissão para '{action}'"
            )

        if module and not self.can_access_module(role, module):
            raise PermissionError(
                f"Usuário com role '{role.value}' não tem acesso ao módulo '{module.value}'"
            )

        return self.get_action_risk(action)


# Instância global
permissions = PermissionService()
