"""
Villa — tests/integration/test_meta_contract.py

Testes de contrato da integração Meta Ads API + CAPI.

O que está sendo testado:
    - get_campaign_insights() monta params corretos e retorna lista
    - get_account_insights() respeita level e fields
    - get_daily_insights() usa time_increment=1 (breakdown diário)
    - get_campaigns() filtra por status quando fornecido
    - send_conversion_event() hasheia email e phone em SHA256
    - send_conversion_event() monta payload CAPI corretamente
    - extract_leads_from_actions() extrai contagem de leads
    - extract_cpl() extrai CPL da lista cost_per_action_type
    - _hash() produz SHA256 correto
    - Erro HTTP (4xx/5xx) propaga httpx.HTTPStatusError
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

pytestmark = pytest.mark.integration


# ── Factories ─────────────────────────────────────────────────────────────────


def make_response(status: int, body: dict) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        content=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        request=httpx.Request("GET", "https://graph.facebook.com/v21.0/act_123/insights"),
    )


def insights_response(data: list[dict]) -> httpx.Response:
    return make_response(200, {"data": data, "paging": {}})


def campaigns_response(data: list[dict]) -> httpx.Response:
    return make_response(200, {"data": data})


SAMPLE_INSIGHT = {
    "campaign_id": "120210000000001",
    "campaign_name": "Implante | Awareness | Mai26",
    "spend": "500.00",
    "impressions": "20000",
    "clicks": "600",
    "ctr": "3.0",
    "reach": "18000",
    "frequency": "1.11",
    "actions": [
        {"action_type": "lead", "value": "10"},
        {"action_type": "link_click", "value": "600"},
    ],
    "cost_per_action_type": [
        {"action_type": "lead", "value": "50.00"},
    ],
    "date_start": "2026-05-01",
    "date_stop": "2026-05-07",
}

SAMPLE_CAMPAIGN = {
    "id": "120210000000001",
    "name": "Implante | Awareness | Mai26",
    "status": "ACTIVE",
    "objective": "LEAD_GENERATION",
    "daily_budget": "5000",
}


# ── Fixture: MetaAdsClient com httpx mockado ──────────────────────────────────


@pytest.fixture
def meta():
    """MetaAdsClient com settings fake e _client mockado."""
    with patch("integrations.meta_ads.settings") as mock_settings:
        mock_settings.meta_access_token = "fake-meta-token"
        mock_settings.meta_pixel_id = "123456789"
        mock_settings.meta_capi_token = "fake-capi-token"

        from integrations.meta_ads import MetaAdsClient

        client = MetaAdsClient()
        client._client = AsyncMock()
        yield client


# ── get_campaign_insights ─────────────────────────────────────────────────────


class TestGetCampaignInsights:
    async def test_retorna_lista_de_insights(self, meta):
        meta._client.get.return_value = insights_response([SAMPLE_INSIGHT])

        result = await meta.get_campaign_insights(
            "act_123456789",
            date(2026, 5, 1),
            date(2026, 5, 7),
        )

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["campaign_id"] == "120210000000001"

    async def test_chama_endpoint_da_conta(self, meta):
        meta._client.get.return_value = insights_response([])

        await meta.get_campaign_insights(
            "act_123456789",
            date(2026, 5, 1),
            date(2026, 5, 7),
        )

        endpoint = meta._client.get.call_args.args[0]
        assert "act_123456789" in endpoint
        assert "insights" in endpoint

    async def test_retorna_lista_vazia_sem_dados(self, meta):
        meta._client.get.return_value = make_response(200, {"data": []})

        result = await meta.get_campaign_insights(
            "act_123456789",
            date(2026, 5, 1),
            date(2026, 5, 7),
        )

        assert result == []

    async def test_propaga_erro_http(self, meta):
        response = make_response(403, {"error": {"message": "Invalid OAuth token"}})
        response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("403", request=MagicMock(), response=response)
        )
        meta._client.get.return_value = response

        with pytest.raises(httpx.HTTPStatusError):
            await meta.get_campaign_insights("act_123", date(2026, 5, 1), date(2026, 5, 7))


# ── get_account_insights ──────────────────────────────────────────────────────


class TestGetAccountInsights:
    async def test_usa_level_campaign_por_padrao(self, meta):
        meta._client.get.return_value = insights_response([SAMPLE_INSIGHT])

        await meta.get_account_insights("act_123", date(2026, 5, 1), date(2026, 5, 7))

        params = meta._client.get.call_args.kwargs.get("params", {})
        assert params.get("level") == "campaign"

    async def test_aceita_level_customizado(self, meta):
        meta._client.get.return_value = insights_response([])

        await meta.get_account_insights(
            "act_123",
            date(2026, 5, 1),
            date(2026, 5, 7),
            level="adset",
        )

        params = meta._client.get.call_args.kwargs.get("params", {})
        assert params.get("level") == "adset"

    async def test_inclui_time_range_nos_params(self, meta):
        meta._client.get.return_value = insights_response([])

        await meta.get_account_insights("act_123", date(2026, 5, 1), date(2026, 5, 7))

        params = meta._client.get.call_args.kwargs.get("params", {})
        assert "time_range" in params
        assert "2026-05-01" in params["time_range"]
        assert "2026-05-07" in params["time_range"]

    async def test_usa_fields_customizados(self, meta):
        meta._client.get.return_value = insights_response([])

        await meta.get_account_insights(
            "act_123",
            date(2026, 5, 1),
            date(2026, 5, 7),
            fields=["spend", "impressions"],
        )

        params = meta._client.get.call_args.kwargs.get("params", {})
        assert params["fields"] == "spend,impressions"


# ── get_daily_insights ────────────────────────────────────────────────────────


class TestGetDailyInsights:
    async def test_usa_time_increment_1(self, meta):
        """Breakdown diário requer time_increment=1."""
        meta._client.get.return_value = insights_response([])

        await meta.get_daily_insights("act_123", days=7)

        params = meta._client.get.call_args.kwargs.get("params", {})
        assert params.get("time_increment") == 1


# ── get_campaigns ─────────────────────────────────────────────────────────────


class TestGetCampaigns:
    async def test_retorna_lista_de_campanhas(self, meta):
        meta._client.get.return_value = campaigns_response([SAMPLE_CAMPAIGN])

        result = await meta.get_campaigns("act_123456789")

        assert len(result) == 1
        assert result[0]["id"] == "120210000000001"
        assert result[0]["status"] == "ACTIVE"

    async def test_inclui_status_filter_quando_fornecido(self, meta):
        meta._client.get.return_value = campaigns_response([])

        await meta.get_campaigns("act_123", status_filter=["ACTIVE", "PAUSED"])

        params = meta._client.get.call_args.kwargs.get("params", {})
        assert "effective_status" in params


# ── send_conversion_event / CAPI ──────────────────────────────────────────────


class TestSendConversionEvent:
    async def test_envia_evento_lead_com_sucesso(self, meta):
        meta._client.post.return_value = make_response(200, {"events_received": 1})

        result = await meta.send_conversion_event(
            event_name="Lead",
            email="joao@clinica.com",
        )

        assert result.get("events_received") == 1

    async def test_hasheia_email_em_sha256(self, meta):
        meta._client.post.return_value = make_response(200, {"events_received": 1})

        await meta.send_conversion_event(event_name="Lead", email="JOAO@CLINICA.COM")

        call_args = meta._client.post.call_args
        payload = call_args.kwargs.get("json") or call_args.args[1]
        email_hash = payload["data"][0]["user_data"]["em"][0]

        # Deve ser o SHA256 do email em minúsculas e sem espaços
        expected = hashlib.sha256(b"joao@clinica.com").hexdigest()
        assert email_hash == expected

    async def test_hasheia_phone_removendo_caracteres_especiais(self, meta):
        meta._client.post.return_value = make_response(200, {"events_received": 1})

        await meta.send_conversion_event(event_name="Lead", phone="+55 11 99999-8888")

        call_args = meta._client.post.call_args
        payload = call_args.kwargs.get("json") or call_args.args[1]
        phone_hash = payload["data"][0]["user_data"]["ph"][0]

        # +55 11 99999-8888 → 551199999888 após limpeza
        expected = hashlib.sha256(b"5511999998888").hexdigest()
        assert phone_hash == expected

    async def test_inclui_value_e_currency_quando_fornecidos(self, meta):
        meta._client.post.return_value = make_response(200, {"events_received": 1})

        await meta.send_conversion_event(
            event_name="Purchase",
            value=6000.0,
            currency="BRL",
        )

        call_args = meta._client.post.call_args
        payload = call_args.kwargs.get("json") or call_args.args[1]
        custom_data = payload["data"][0]["custom_data"]
        assert custom_data["value"] == 6000.0
        assert custom_data["currency"] == "BRL"

    async def test_nao_inclui_user_data_sem_email_ou_phone(self, meta):
        meta._client.post.return_value = make_response(200, {"events_received": 1})

        await meta.send_conversion_event(event_name="PageView")

        call_args = meta._client.post.call_args
        payload = call_args.kwargs.get("json") or call_args.args[1]
        user_data = payload["data"][0]["user_data"]
        assert "em" not in user_data
        assert "ph" not in user_data

    async def test_payload_inclui_access_token(self, meta):
        meta._client.post.return_value = make_response(200, {"events_received": 1})

        await meta.send_conversion_event(event_name="Lead")

        call_args = meta._client.post.call_args
        payload = call_args.kwargs.get("json") or call_args.args[1]
        assert payload["access_token"] == "fake-capi-token"

    async def test_chama_endpoint_do_pixel(self, meta):
        meta._client.post.return_value = make_response(200, {"events_received": 1})

        await meta.send_conversion_event(event_name="Lead")

        endpoint = meta._client.post.call_args.args[0]
        assert "123456789" in endpoint
        assert "events" in endpoint


# ── Métodos utilitários (puros — sem mock) ────────────────────────────────────


class TestUtilitarios:
    def test_hash_sha256_correto(self):
        from integrations.meta_ads import MetaAdsClient

        result = MetaAdsClient._hash("test@example.com")
        expected = hashlib.sha256(b"test@example.com").hexdigest()
        assert result == expected
        assert len(result) == 64  # SHA256 = 64 hex chars

    def test_extract_leads_from_actions_encontra_lead(self):
        from integrations.meta_ads import MetaAdsClient

        actions = [
            {"action_type": "link_click", "value": "300"},
            {"action_type": "lead", "value": "15"},
            {"action_type": "post_engagement", "value": "500"},
        ]
        result = MetaAdsClient.extract_leads_from_actions(actions)
        assert result == 15

    def test_extract_leads_from_actions_sem_lead_retorna_zero(self):
        from integrations.meta_ads import MetaAdsClient

        actions = [{"action_type": "link_click", "value": "300"}]
        result = MetaAdsClient.extract_leads_from_actions(actions)
        assert result == 0

    def test_extract_leads_from_actions_lista_vazia(self):
        from integrations.meta_ads import MetaAdsClient

        result = MetaAdsClient.extract_leads_from_actions([])
        assert result == 0

    def test_extract_cpl_encontra_valor(self):
        from integrations.meta_ads import MetaAdsClient

        cpa = [
            {"action_type": "post_engagement", "value": "1.50"},
            {"action_type": "lead", "value": "50.00"},
        ]
        result = MetaAdsClient.extract_cpl(cpa)
        assert result == 50.0

    def test_extract_cpl_sem_lead_retorna_none(self):
        from integrations.meta_ads import MetaAdsClient

        cpa = [{"action_type": "link_click", "value": "2.00"}]
        result = MetaAdsClient.extract_cpl(cpa)
        assert result is None

    def test_extract_cpl_lista_vazia_retorna_none(self):
        from integrations.meta_ads import MetaAdsClient

        result = MetaAdsClient.extract_cpl([])
        assert result is None
