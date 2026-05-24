"""
Villa — Integração Kommo CRM
CRUD de leads, pipelines, cards e notas.
Usa a API REST v4 do Kommo.
"""


import httpx

from core.config import settings


class KommoClient:
    """
    Cliente async para a API do Kommo CRM.
    
    Uso:
        kommo = KommoClient()
        leads = await kommo.get_leads(pipeline_id=12345)
        await kommo.move_lead(lead_id=67890, status_id=142)
        await kommo.add_note(lead_id=67890, text="Qualificado pelo Villa")
    """

    def __init__(self):
        self.base_url = settings.kommo_account_url.rstrip("/")
        self.token = settings.kommo_api_token
        self._client = httpx.AsyncClient(
            base_url=f"{self.base_url}/api/v4",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    # ═══════════════════════════════════════════════════
    # LEADS
    # ═══════════════════════════════════════════════════

    async def get_leads(
        self,
        pipeline_id: int | None = None,
        status_id: int | None = None,
        limit: int = 50,
        page: int = 1,
        query: str | None = None,
    ) -> list[dict]:
        """Busca leads com filtros opcionais."""
        params = {"limit": limit, "page": page}
        if pipeline_id:
            params["filter[pipe]"] = pipeline_id
        if status_id:
            params["filter[statuses]"] = status_id
        if query:
            params["query"] = query

        response = await self._client.get("/leads", params=params)
        response.raise_for_status()
        data = response.json()
        return data.get("_embedded", {}).get("leads", [])

    async def get_lead(self, lead_id: int) -> dict | None:
        """Busca um lead específico pelo ID."""
        response = await self._client.get(f"/leads/{lead_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def create_lead(
        self,
        name: str,
        pipeline_id: int,
        status_id: int | None = None,
        price: int | None = None,
        custom_fields: list[dict] | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """Cria um novo lead no Kommo."""
        lead_data = {"name": name, "pipeline_id": pipeline_id}
        if status_id:
            lead_data["status_id"] = status_id
        if price:
            lead_data["price"] = price
        if custom_fields:
            lead_data["custom_fields_values"] = custom_fields
        if tags:
            lead_data["_embedded"] = {
                "tags": [{"name": t} for t in tags]
            }

        response = await self._client.post("/leads", json=[lead_data])
        response.raise_for_status()
        return response.json().get("_embedded", {}).get("leads", [{}])[0]

    async def update_lead(self, lead_id: int, **fields) -> dict:
        """
        Atualiza campos de um lead.
        
        Uso:
            await kommo.update_lead(lead_id=123, price=6000, status_id=142)
        """
        fields["id"] = lead_id
        response = await self._client.patch("/leads", json=[fields])
        response.raise_for_status()
        return response.json().get("_embedded", {}).get("leads", [{}])[0]

    async def move_lead(self, lead_id: int, status_id: int, pipeline_id: int | None = None) -> dict:
        """Move um lead para outra etapa do kanban."""
        fields = {"status_id": status_id}
        if pipeline_id:
            fields["pipeline_id"] = pipeline_id
        return await self.update_lead(lead_id, **fields)

    async def set_lead_value(self, lead_id: int, value: int) -> dict:
        """Define o valor de venda de um lead (em centavos)."""
        return await self.update_lead(lead_id, price=value)

    # ═══════════════════════════════════════════════════
    # NOTAS
    # ═══════════════════════════════════════════════════

    async def add_note(
        self,
        lead_id: int,
        text: str,
        note_type: str = "common",
    ) -> dict:
        """
        Adiciona uma nota a um lead.
        
        Args:
            lead_id: ID do lead
            text: Texto da nota
            note_type: "common" | "call_in" | "call_out" | "service_message"
        """
        response = await self._client.post(
            f"/leads/{lead_id}/notes",
            json=[{
                "note_type": note_type,
                "params": {"text": text},
            }],
        )
        response.raise_for_status()
        return response.json().get("_embedded", {}).get("notes", [{}])[0]

    # ═══════════════════════════════════════════════════
    # PIPELINES E STATUS
    # ═══════════════════════════════════════════════════

    async def get_pipelines(self) -> list[dict]:
        """Lista todos os pipelines (funis) da conta."""
        response = await self._client.get("/leads/pipelines")
        response.raise_for_status()
        return response.json().get("_embedded", {}).get("pipelines", [])

    async def get_pipeline_statuses(self, pipeline_id: int) -> list[dict]:
        """Lista as etapas (statuses) de um pipeline."""
        pipelines = await self.get_pipelines()
        for p in pipelines:
            if p.get("id") == pipeline_id:
                return p.get("_embedded", {}).get("statuses", [])
        return []

    # ═══════════════════════════════════════════════════
    # CONTATOS
    # ═══════════════════════════════════════════════════

    async def get_contact(self, contact_id: int) -> dict | None:
        """Busca um contato pelo ID."""
        response = await self._client.get(f"/contacts/{contact_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def create_contact(
        self,
        name: str,
        phone: str | None = None,
        email: str | None = None,
    ) -> dict:
        """Cria um contato e retorna os dados."""
        contact = {"name": name, "custom_fields_values": []}
        if phone:
            contact["custom_fields_values"].append({
                "field_code": "PHONE",
                "values": [{"value": phone, "enum_code": "WORK"}],
            })
        if email:
            contact["custom_fields_values"].append({
                "field_code": "EMAIL",
                "values": [{"value": email, "enum_code": "WORK"}],
            })

        response = await self._client.post("/contacts", json=[contact])
        response.raise_for_status()
        return response.json().get("_embedded", {}).get("contacts", [{}])[0]

    async def link_contact_to_lead(self, lead_id: int, contact_id: int) -> dict:
        """Vincula um contato a um lead."""
        response = await self._client.post(
            f"/leads/{lead_id}/link",
            json=[{"to_entity_id": contact_id, "to_entity_type": "contacts"}],
        )
        response.raise_for_status()
        return response.json()

    # ═══════════════════════════════════════════════════
    # TAGS
    # ═══════════════════════════════════════════════════

    async def add_tags(self, lead_id: int, tags: list[str]) -> dict:
        """Adiciona tags a um lead."""
        return await self.update_lead(
            lead_id,
            _embedded={"tags": [{"name": t} for t in tags]},
        )

    # ═══════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════

    async def close(self):
        await self._client.aclose()


# Instância global
kommo = KommoClient()
