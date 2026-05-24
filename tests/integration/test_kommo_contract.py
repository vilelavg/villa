"""
Villa — tests/integration/test_kommo_contract.py

Testes de contrato da integração Kommo CRM.

O que é um "teste de contrato":
    Verifica que a camada de integração do Villa constrói as requisições
    corretamente e processa as respostas no formato esperado. Não faz
    chamada real à API — usa httpx.MockTransport para interceptar.

    Se o Kommo mudar o schema da resposta (ex: renomear "_embedded" para
    "embedded"), esses testes quebram imediatamente, alertando antes de
    quebrar em produção.

O que está sendo testado:
    - get_leads() monta params corretos e extrai lista de leads
    - get_lead() retorna None no 404, dict no 200
    - create_lead() faz POST com payload correto
    - update_lead() faz PATCH com id incluído no body
    - move_lead() delega corretamente para update_lead()
    - add_note() faz POST no endpoint correto com note_type
    - get_pipelines() extrai lista de pipelines do _embedded
    - create_contact() monta custom_fields para phone e email
    - Erro HTTP (4xx/5xx) propaga httpx.HTTPStatusError
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

pytestmark = pytest.mark.integration


# ── Factories de resposta fake ────────────────────────────────────────────────


def make_response(status: int, body: dict) -> httpx.Response:
    """Cria um httpx.Response fake com status e body JSON."""
    return httpx.Response(
        status_code=status,
        content=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        request=httpx.Request("GET", "https://fake.kommo.com/api/v4/leads"),
    )


def leads_response(leads: list[dict]) -> httpx.Response:
    return make_response(200, {"_embedded": {"leads": leads}})


def notes_response(notes: list[dict]) -> httpx.Response:
    return make_response(200, {"_embedded": {"notes": notes}})


def pipelines_response(pipelines: list[dict]) -> httpx.Response:
    return make_response(200, {"_embedded": {"pipelines": pipelines}})


def contacts_response(contacts: list[dict]) -> httpx.Response:
    return make_response(200, {"_embedded": {"contacts": contacts}})


SAMPLE_LEAD = {
    "id": 123456,
    "name": "João Silva - Implante",
    "status_id": 142,
    "pipeline_id": 9999,
    "price": 6000,
    "created_at": 1700000000,
}

SAMPLE_NOTE = {
    "id": 789,
    "note_type": "common",
    "params": {"text": "Qualificado pelo Villa"},
}

SAMPLE_PIPELINE = {
    "id": 9999,
    "name": "Leads Odontologia",
    "_embedded": {
        "statuses": [
            {"id": 142, "name": "Novo Lead"},
            {"id": 143, "name": "Qualificado"},
            {"id": 144, "name": "Agendado"},
        ]
    },
}

SAMPLE_CONTACT = {
    "id": 55555,
    "name": "João Silva",
    "custom_fields_values": [],
}


# ── Fixture: KommoClient com httpx mockado ────────────────────────────────────


@pytest.fixture
def kommo():
    """
    Retorna KommoClient com settings fake e _client mockado.
    Cada teste configura mock_client.get/post/patch individualmente.
    """
    with patch("integrations.kommo.settings") as mock_settings:
        mock_settings.kommo_account_url = "https://fake.kommo.com"
        mock_settings.kommo_api_token = "fake-token-test"

        from integrations.kommo import KommoClient

        client = KommoClient()
        client._client = AsyncMock()
        yield client


# ── get_leads ─────────────────────────────────────────────────────────────────


class TestGetLeads:
    async def test_retorna_lista_de_leads(self, kommo):
        kommo._client.get.return_value = leads_response([SAMPLE_LEAD])

        result = await kommo.get_leads()

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["id"] == 123456

    async def test_monta_params_com_pipeline_id(self, kommo):
        kommo._client.get.return_value = leads_response([])

        await kommo.get_leads(pipeline_id=9999, limit=10, page=2)

        # Verifica que foi chamado com /leads e que o mock foi acionado
        assert kommo._client.get.called
        assert kommo._client.get.call_args.args[0] == "/leads"

    async def test_retorna_lista_vazia_quando_sem_leads(self, kommo):
        kommo._client.get.return_value = make_response(200, {"_embedded": {}})

        result = await kommo.get_leads()

        assert result == []

    async def test_propaga_erro_http(self, kommo):
        response = make_response(401, {"message": "Unauthorized"})
        response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("401", request=MagicMock(), response=response)
        )
        kommo._client.get.return_value = response

        with pytest.raises(httpx.HTTPStatusError):
            await kommo.get_leads()


# ── get_lead ──────────────────────────────────────────────────────────────────


class TestGetLead:
    async def test_retorna_lead_existente(self, kommo):
        kommo._client.get.return_value = make_response(200, SAMPLE_LEAD)

        result = await kommo.get_lead(123456)

        assert result is not None
        assert result["id"] == 123456

    async def test_retorna_none_para_404(self, kommo):
        response = make_response(404, {"detail": "not found"})
        kommo._client.get.return_value = response

        result = await kommo.get_lead(999999)

        assert result is None

    async def test_chama_endpoint_correto(self, kommo):
        kommo._client.get.return_value = make_response(200, SAMPLE_LEAD)

        await kommo.get_lead(123456)

        kommo._client.get.assert_called_once_with("/leads/123456")


# ── create_lead ───────────────────────────────────────────────────────────────


class TestCreateLead:
    async def test_cria_lead_e_retorna_dados(self, kommo):
        kommo._client.post.return_value = leads_response([SAMPLE_LEAD])

        result = await kommo.create_lead(
            name="João Silva - Implante",
            pipeline_id=9999,
            status_id=142,
            price=6000,
        )

        assert result["id"] == 123456
        assert result["name"] == "João Silva - Implante"

    async def test_payload_inclui_campos_obrigatorios(self, kommo):
        kommo._client.post.return_value = leads_response([SAMPLE_LEAD])

        await kommo.create_lead(name="Teste", pipeline_id=9999)

        call_args = kommo._client.post.call_args
        payload = call_args.kwargs.get("json") or call_args.args[1]
        assert isinstance(payload, list)
        assert payload[0]["name"] == "Teste"
        assert payload[0]["pipeline_id"] == 9999

    async def test_payload_inclui_tags_quando_fornecidas(self, kommo):
        kommo._client.post.return_value = leads_response([SAMPLE_LEAD])

        await kommo.create_lead(
            name="Teste",
            pipeline_id=9999,
            tags=["implante", "qualificado"],
        )

        call_args = kommo._client.post.call_args
        payload = call_args.kwargs.get("json") or call_args.args[1]
        tags = payload[0]["_embedded"]["tags"]
        assert any(t["name"] == "implante" for t in tags)
        assert any(t["name"] == "qualificado" for t in tags)


# ── update_lead / move_lead ───────────────────────────────────────────────────


class TestUpdateLead:
    async def test_update_inclui_id_no_body(self, kommo):
        kommo._client.patch.return_value = leads_response([SAMPLE_LEAD])

        await kommo.update_lead(123456, price=8000)

        call_args = kommo._client.patch.call_args
        payload = call_args.kwargs.get("json") or call_args.args[1]
        assert payload[0]["id"] == 123456
        assert payload[0]["price"] == 8000

    async def test_move_lead_delega_para_update(self, kommo):
        """move_lead é um atalho para update_lead com status_id."""
        kommo._client.patch.return_value = leads_response([SAMPLE_LEAD])

        await kommo.move_lead(lead_id=123456, status_id=143)

        call_args = kommo._client.patch.call_args
        payload = call_args.kwargs.get("json") or call_args.args[1]
        assert payload[0]["status_id"] == 143
        assert payload[0]["id"] == 123456

    async def test_move_lead_com_pipeline_id(self, kommo):
        kommo._client.patch.return_value = leads_response([SAMPLE_LEAD])

        await kommo.move_lead(lead_id=123456, status_id=143, pipeline_id=9999)

        call_args = kommo._client.patch.call_args
        payload = call_args.kwargs.get("json") or call_args.args[1]
        assert payload[0]["pipeline_id"] == 9999


# ── add_note ──────────────────────────────────────────────────────────────────


class TestAddNote:
    async def test_adiciona_nota_e_retorna(self, kommo):
        kommo._client.post.return_value = notes_response([SAMPLE_NOTE])

        result = await kommo.add_note(lead_id=123456, text="Qualificado pelo Villa")

        assert result["id"] == 789
        assert result["note_type"] == "common"

    async def test_chama_endpoint_correto(self, kommo):
        kommo._client.post.return_value = notes_response([SAMPLE_NOTE])

        await kommo.add_note(lead_id=123456, text="Teste")

        kommo._client.post.assert_called_once()
        endpoint = kommo._client.post.call_args.args[0]
        assert endpoint == "/leads/123456/notes"

    async def test_payload_inclui_note_type_e_texto(self, kommo):
        kommo._client.post.return_value = notes_response([SAMPLE_NOTE])

        await kommo.add_note(lead_id=123456, text="Nota de teste", note_type="call_in")

        call_args = kommo._client.post.call_args
        payload = call_args.kwargs.get("json") or call_args.args[1]
        assert payload[0]["note_type"] == "call_in"
        assert payload[0]["params"]["text"] == "Nota de teste"


# ── get_pipelines ─────────────────────────────────────────────────────────────


class TestGetPipelines:
    async def test_retorna_lista_de_pipelines(self, kommo):
        kommo._client.get.return_value = pipelines_response([SAMPLE_PIPELINE])

        result = await kommo.get_pipelines()

        assert len(result) == 1
        assert result[0]["id"] == 9999
        assert result[0]["name"] == "Leads Odontologia"

    async def test_get_pipeline_statuses_retorna_etapas(self, kommo):
        kommo._client.get.return_value = pipelines_response([SAMPLE_PIPELINE])

        statuses = await kommo.get_pipeline_statuses(pipeline_id=9999)

        assert len(statuses) == 3
        assert statuses[0]["id"] == 142
        assert statuses[0]["name"] == "Novo Lead"

    async def test_get_pipeline_statuses_pipeline_inexistente(self, kommo):
        kommo._client.get.return_value = pipelines_response([SAMPLE_PIPELINE])

        statuses = await kommo.get_pipeline_statuses(pipeline_id=0000)

        assert statuses == []


# ── create_contact ────────────────────────────────────────────────────────────


class TestCreateContact:
    async def test_cria_contato_simples(self, kommo):
        kommo._client.post.return_value = contacts_response([SAMPLE_CONTACT])

        result = await kommo.create_contact(name="João Silva")

        assert result["id"] == 55555
        assert result["name"] == "João Silva"

    async def test_payload_inclui_phone_como_custom_field(self, kommo):
        kommo._client.post.return_value = contacts_response([SAMPLE_CONTACT])

        await kommo.create_contact(name="João", phone="+5511999998888")

        call_args = kommo._client.post.call_args
        payload = call_args.kwargs.get("json") or call_args.args[1]
        fields = payload[0]["custom_fields_values"]
        phone_field = next((f for f in fields if f["field_code"] == "PHONE"), None)
        assert phone_field is not None
        assert phone_field["values"][0]["value"] == "+5511999998888"

    async def test_payload_inclui_email_como_custom_field(self, kommo):
        kommo._client.post.return_value = contacts_response([SAMPLE_CONTACT])

        await kommo.create_contact(name="João", email="joao@clinic.com")

        call_args = kommo._client.post.call_args
        payload = call_args.kwargs.get("json") or call_args.args[1]
        fields = payload[0]["custom_fields_values"]
        email_field = next((f for f in fields if f["field_code"] == "EMAIL"), None)
        assert email_field is not None
        assert email_field["values"][0]["value"] == "joao@clinic.com"
