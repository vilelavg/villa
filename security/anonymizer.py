"""
Villa — Anonimização de Dados
Remove ou mascara dados pessoais para relatórios agregados.
Garante conformidade com LGPD Art. 11 (dados de saúde).
"""

import hashlib
import re
from typing import Any


class Anonymizer:
    """
    Anonimiza dados pessoais para uso em relatórios e análises agregadas.

    Uso:
        anon = Anonymizer()
        safe_data = anon.anonymize_lead(lead_dict)
        safe_text = anon.mask_pii("O paciente João Silva, CPF 123.456.789-00")
    """

    # Padrões regex para PII em texto
    PATTERNS = {
        "cpf": re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}"),
        "phone": re.compile(r"(?:\+55\s?)?(?:\(?\d{2}\)?\s?)?\d{4,5}-?\d{4}"),
        "email": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
        "card": re.compile(r"\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}"),
    }

    # Campos considerados PII
    PII_FIELDS = {
        "name",
        "nome",
        "contact_name",
        "patient_name",
        "phone",
        "telefone",
        "celular",
        "contact_phone",
        "email",
        "contact_email",
        "cpf",
        "rg",
        "document",
        "address",
        "endereco",
    }

    # Campos de saúde (LGPD Art. 11 — categoria especial)
    HEALTH_FIELDS = {
        "anamnese",
        "diagnosis",
        "diagnostico",
        "treatment",
        "tratamento",
        "procedure",
        "procedimento",
        "condition",
        "condicao",
        "medical_history",
    }

    def anonymize_dict(self, data: dict, keep_keys: set[str] | None = None) -> dict:
        """
        Anonimiza um dicionário removendo/mascarando campos PII.

        Args:
            data: Dicionário com dados pessoais
            keep_keys: Campos que NÃO devem ser anonimizados

        Returns:
            Dicionário anonimizado
        """
        keep = keep_keys or set()
        result = {}

        for key, value in data.items():
            key_lower = key.lower()

            if key in keep:
                result[key] = value
            elif key_lower in self.PII_FIELDS:
                result[key] = self._mask_value(key_lower, value)
            elif key_lower in self.HEALTH_FIELDS:
                result[key] = "[DADO DE SAÚDE PROTEGIDO]"
            elif isinstance(value, dict):
                result[key] = self.anonymize_dict(value, keep)
            elif isinstance(value, str):
                result[key] = self.mask_pii(value)
            else:
                result[key] = value

        return result

    def anonymize_lead(self, lead: dict) -> dict:
        """
        Anonimiza dados de um lead para relatório agregado.
        Mantém: source, status, scores, datas.
        Remove: nome, telefone, email, dados brutos.
        """
        return self.anonymize_dict(
            lead,
            keep_keys={
                "id",
                "client_id",
                "status",
                "source",
                "utm_source",
                "utm_medium",
                "utm_campaign",
                "qualification_score",
                "deal_value",
                "created_at",
                "updated_at",
                "converted_at",
            },
        )

    def mask_pii(self, text: str) -> str:
        """
        Mascara PII encontrado em texto livre.

        Ex:
            "João Silva, CPF 123.456.789-00, tel (11) 98765-4321"
            → "*****, CPF ***.***.**9-00, tel (**) ****5-4321"
        """
        if not text or not isinstance(text, str):
            return text

        result = text

        # Mascarar CPF
        result = self.PATTERNS["cpf"].sub(
            lambda m: self._partial_mask(m.group(), show_last=4),
            result,
        )

        # Mascarar telefone
        result = self.PATTERNS["phone"].sub(
            lambda m: self._partial_mask(m.group(), show_last=4),
            result,
        )

        # Mascarar email
        result = self.PATTERNS["email"].sub(
            lambda m: self._mask_email(m.group()),
            result,
        )

        # Mascarar cartão
        result = self.PATTERNS["card"].sub(
            lambda m: self._partial_mask(m.group(), show_last=4),
            result,
        )

        return result

    def pseudonymize(self, value: str) -> str:
        """
        Pseudonimiza um valor — gera um hash consistente.
        O mesmo valor sempre gera o mesmo pseudônimo.
        Útil para análises agregadas que precisam de consistência.
        """
        if not value:
            return ""
        h = hashlib.sha256(value.encode()).hexdigest()[:12]
        return f"ANON-{h.upper()}"

    def _mask_value(self, field: str, value: Any) -> str:
        """Mascara um valor baseado no tipo de campo."""
        if not value:
            return ""
        val = str(value)
        if field in ("email", "contact_email"):
            return self._mask_email(val)
        if field in ("phone", "telefone", "celular", "contact_phone"):
            return self._partial_mask(val, show_last=4)
        if field in ("name", "nome", "contact_name", "patient_name"):
            return self.pseudonymize(val)
        return "*****"

    @staticmethod
    def _partial_mask(value: str, show_last: int = 4) -> str:
        """Mostra apenas os últimos N caracteres."""
        clean = re.sub(r"[^\d]", "", value)
        if len(clean) <= show_last:
            return "*" * len(clean)
        masked = "*" * (len(clean) - show_last) + clean[-show_last:]
        return masked

    @staticmethod
    def _mask_email(email: str) -> str:
        """Mascara email mantendo domínio parcial."""
        try:
            local, domain = email.split("@")
            masked_local = local[0] + "***" if local else "***"
            return f"{masked_local}@{domain}"
        except (ValueError, IndexError):
            return "*****@***"


# Instância global
anonymizer = Anonymizer()
