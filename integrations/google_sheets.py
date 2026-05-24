"""
Villa — Integração Google Sheets
Leitura e escrita em planilhas (relatórios, BI, planilha mestre).
"""

import httpx

from core.config import settings


class GoogleSheetsClient:
    """
    Cliente para Google Sheets API via Service Account.

    Uso:
        sheets = GoogleSheetsClient()
        data = await sheets.read("SPREADSHEET_ID", "Dados!A1:Z100")
        await sheets.write("SPREADSHEET_ID", "Relatorio!A1", [["CPL", "CTR"], [25.0, 1.8]])
    """

    BASE_URL = "https://sheets.googleapis.com/v4/spreadsheets"

    def __init__(self):
        self._token: str | None = None
        self._token_expires: float = 0
        self._client = httpx.AsyncClient(timeout=30.0)

    async def _get_token(self) -> str:
        import json
        import time

        from jose import jwt as jose_jwt

        if self._token and time.time() < self._token_expires:
            return self._token

        with open(settings.google_service_account_json) as f:
            creds = json.load(f)

        now = int(time.time())
        payload = {
            "iss": creds["client_email"],
            "scope": "https://www.googleapis.com/auth/spreadsheets",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
        }
        signed = jose_jwt.encode(payload, creds["private_key"], algorithm="RS256")
        response = await self._client.post(
            "https://oauth2.googleapis.com/token",
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": signed},
        )
        response.raise_for_status()
        data = response.json()
        self._token = data["access_token"]
        self._token_expires = now + data.get("expires_in", 3600) - 60
        return self._token

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        response = await self._client.request(
            method, f"{self.BASE_URL}{path}", headers=headers, **kwargs
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    async def read(
        self,
        spreadsheet_id: str,
        range_notation: str,
    ) -> list[list]:
        """
        Lê dados de uma planilha.

        Args:
            spreadsheet_id: ID da planilha no Google Sheets
            range_notation: Ex: "Sheet1!A1:Z100" ou "Dados!A:D"

        Returns:
            Lista de linhas (cada linha é uma lista de valores)
        """
        data = await self._request(
            "GET",
            f"/{spreadsheet_id}/values/{range_notation}",
        )
        return data.get("values", [])

    async def write(
        self,
        spreadsheet_id: str,
        range_notation: str,
        values: list[list],
        value_input_option: str = "USER_ENTERED",
    ) -> dict:
        """
        Escreve dados em uma planilha.

        Args:
            spreadsheet_id: ID da planilha
            range_notation: Ex: "Relatorio!A1"
            values: Dados como lista de listas [["header1","header2"],["val1","val2"]]
        """
        return await self._request(
            "PUT",
            f"/{spreadsheet_id}/values/{range_notation}",
            params={"valueInputOption": value_input_option},
            json={"range": range_notation, "values": values},
        )

    async def append(
        self,
        spreadsheet_id: str,
        range_notation: str,
        values: list[list],
    ) -> dict:
        """Adiciona linhas ao final de uma planilha."""
        return await self._request(
            "POST",
            f"/{spreadsheet_id}/values/{range_notation}:append",
            params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
            json={"range": range_notation, "values": values},
        )

    async def clear(self, spreadsheet_id: str, range_notation: str) -> dict:
        """Limpa dados de um range."""
        return await self._request(
            "POST",
            f"/{spreadsheet_id}/values/{range_notation}:clear",
        )

    async def close(self):
        await self._client.aclose()


google_sheets = GoogleSheetsClient()
