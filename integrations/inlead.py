"""
Villa — Integração InLead
Parser de webhooks do InLead com mapeamento de campos aleatórios.

O InLead gera nomes de campos aleatórios por formulário
(ex: $json.body.cL8voa para "nome"). Cada cliente da WebXP
tem um mapeamento diferente, armazenado em clients.inlead_field_mapping.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Client


class InLeadParser:
    """
    Traduz dados de webhooks InLead para formato padronizado.

    O mapeamento por cliente é armazenado no banco:
        clients.inlead_field_mapping = {
            "cL8voa": "name",
            "xK2mbn": "phone",
            "pQ9frs": "email",
            "jW4tyu": "specialty",
            ...
        }

    Uso:
        parser = InLeadParser(db_session)
        lead_data = await parser.parse(raw_webhook_data, client_slug="ottoboni")
        # Retorna: {"name": "João", "phone": "11999998888", "email": "..."}
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def parse(
        self,
        raw_data: dict,
        client_slug: str | None = None,
        client_id: str | None = None,
    ) -> dict:
        """
        Traduz dados brutos do InLead para campos padronizados.

        Args:
            raw_data: Payload do webhook (campos com nomes aleatórios)
            client_slug: Slug do cliente para buscar o mapeamento
            client_id: ID do cliente (alternativa ao slug)

        Returns:
            Dict com campos padronizados:
                name, phone, email, specialty, interest,
                source, form_id, submission_id, raw_data
        """
        # Buscar mapeamento do cliente
        mapping = await self._get_mapping(client_slug, client_id)

        if not mapping:
            # Sem mapeamento: retorna dados brutos com tentativa de detecção
            return self._auto_detect(raw_data)

        # Aplicar mapeamento
        parsed = {}
        for raw_key, value in raw_data.items():
            if raw_key in mapping:
                standard_key = mapping[raw_key]
                parsed[standard_key] = value
            # Manter campos não mapeados sob prefixo
            parsed[f"_raw_{raw_key}"] = value

        # Campos do sistema (sempre presentes)
        parsed["form_id"] = raw_data.get("form_id", raw_data.get("formId"))
        parsed["submission_id"] = raw_data.get("id", raw_data.get("submissionId"))
        parsed["raw_data"] = raw_data

        return parsed

    async def _get_mapping(
        self,
        client_slug: str | None,
        client_id: str | None,
    ) -> dict | None:
        """Busca o mapeamento de campos do InLead para um cliente."""
        if client_slug:
            result = await self.db.execute(select(Client).where(Client.slug == client_slug))
        elif client_id:
            result = await self.db.execute(select(Client).where(Client.id == client_id))
        else:
            return None

        client = result.scalar_one_or_none()
        if client and client.inlead_field_mapping:
            return client.inlead_field_mapping
        return None

    def _auto_detect(self, data: dict) -> dict:
        """
        Tentativa automática de detectar campos quando não há mapeamento.
        Usa heurísticas (regex de telefone, email, etc.).
        """
        import re

        parsed = {"raw_data": data}
        phone_pattern = re.compile(r"^\+?\d{10,13}$")
        email_pattern = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.]+$")

        for key, value in data.items():
            if not isinstance(value, str):
                continue
            val = value.strip()

            if email_pattern.match(val):
                parsed.setdefault("email", val)
            elif phone_pattern.match(
                val.replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
            ):
                parsed.setdefault("phone", val)
            elif len(val) > 2 and len(val) < 100 and not val.isdigit():
                # Provavelmente um nome
                if "name" not in parsed:
                    parsed["name"] = val

        return parsed

    async def identify_client_by_form(self, form_id: str) -> Client | None:
        """
        Identifica qual cliente da WebXP corresponde a um form_id do InLead.
        Busca pelo campo inlead_form_id na tabela clients.
        """
        result = await self.db.execute(select(Client).where(Client.inlead_form_id == form_id))
        return result.scalar_one_or_none()

    @staticmethod
    def build_mapping(sample_data: dict, field_names: dict[str, str]) -> dict:
        """
        Helper para construir o mapeamento a partir de uma amostra.

        Uso durante setup de novo cliente:
            mapping = InLeadParser.build_mapping(
                sample_data={"cL8voa": "João Silva", "xK2mbn": "11999998888"},
                field_names={"cL8voa": "name", "xK2mbn": "phone"},
            )
            # Salvar em clients.inlead_field_mapping
        """
        return {raw_key: standard_name for raw_key, standard_name in field_names.items()}
