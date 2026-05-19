"""
Villa — Integração Google Drive
Upload, download e busca de arquivos.
Usado para relatórios PDF, criativos, documentos de clientes.
"""

from typing import Optional

import httpx

from core.config import settings


class GoogleDriveClient:
    """
    Cliente para Google Drive API via Service Account.
    Compartilha a mesma autenticação do Calendar.
    """

    BASE_URL = "https://www.googleapis.com/drive/v3"
    UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3"

    def __init__(self):
        self._token: Optional[str] = None
        self._token_expires: float = 0
        self._client = httpx.AsyncClient(timeout=60.0)

    async def _get_token(self) -> str:
        """Obtém access token via Service Account JWT."""
        import json, time
        from jose import jwt as jose_jwt

        if self._token and time.time() < self._token_expires:
            return self._token

        with open(settings.google_service_account_json) as f:
            creds = json.load(f)

        now = int(time.time())
        payload = {
            "iss": creds["client_email"],
            "scope": "https://www.googleapis.com/auth/drive",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now, "exp": now + 3600,
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

    async def _request(self, method: str, url: str, **kwargs) -> dict:
        token = await self._get_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        response = await self._client.request(method, url, headers=headers, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else {}

    async def search_files(
        self,
        query: str,
        folder_id: Optional[str] = None,
        mime_type: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        """
        Busca arquivos no Drive.
        
        Args:
            query: Texto de busca (nome do arquivo)
            folder_id: Buscar dentro de uma pasta específica
            mime_type: Filtrar por tipo (application/pdf, image/jpeg, etc.)
        """
        q_parts = [f"name contains '{query}'", "trashed = false"]
        if folder_id:
            q_parts.append(f"'{folder_id}' in parents")
        if mime_type:
            q_parts.append(f"mimeType = '{mime_type}'")

        params = {
            "q": " and ".join(q_parts),
            "fields": "files(id,name,mimeType,size,createdTime,modifiedTime,webViewLink)",
            "pageSize": limit,
            "orderBy": "modifiedTime desc",
        }
        data = await self._request("GET", f"{self.BASE_URL}/files", params=params)
        return data.get("files", [])

    async def upload_file(
        self,
        file_content: bytes,
        filename: str,
        mime_type: str,
        folder_id: Optional[str] = None,
    ) -> dict:
        """Upload de arquivo para o Drive."""
        import json
        metadata = {"name": filename, "mimeType": mime_type}
        if folder_id:
            metadata["parents"] = [folder_id]

        token = await self._get_token()
        response = await self._client.post(
            f"{self.UPLOAD_URL}/files?uploadType=multipart",
            headers={"Authorization": f"Bearer {token}"},
            files={
                "metadata": ("metadata", json.dumps(metadata), "application/json"),
                "file": (filename, file_content, mime_type),
            },
        )
        response.raise_for_status()
        return response.json()

    async def get_file_content(self, file_id: str) -> bytes:
        """Baixa o conteúdo de um arquivo."""
        token = await self._get_token()
        response = await self._client.get(
            f"{self.BASE_URL}/files/{file_id}",
            params={"alt": "media"},
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        return response.content

    async def create_folder(self, name: str, parent_id: Optional[str] = None) -> dict:
        """Cria uma pasta no Drive."""
        metadata = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
        if parent_id:
            metadata["parents"] = [parent_id]
        return await self._request("POST", f"{self.BASE_URL}/files", json=metadata)

    async def close(self):
        await self._client.aclose()


google_drive = GoogleDriveClient()
