"""
Villa — Integração Google Calendar
Leitura de disponibilidade e criação de eventos de consulta.
Usado pelo módulo M5 (Agendamento).
"""

from datetime import datetime, timedelta

import httpx

from core.config import settings


class GoogleCalendarClient:
    """
    Cliente para Google Calendar API via Service Account.
    
    Uso:
        cal = GoogleCalendarClient()
        slots = await cal.get_free_slots("calendar_id@google.com", date.today())
        event = await cal.create_event("calendar_id@google.com", start, end, "Consulta João")
    """

    BASE_URL = "https://www.googleapis.com/calendar/v3"

    def __init__(self):
        self._token: str | None = None
        self._token_expires: float = 0
        self._client = httpx.AsyncClient(timeout=30.0)

    async def _get_token(self) -> str:
        """Obtém access token via Service Account JWT."""
        import json
        import time

        from jose import jwt as jose_jwt

        if self._token and time.time() < self._token_expires:
            return self._token

        # Carregar credenciais da service account
        with open(settings.google_service_account_json) as f:
            creds = json.load(f)

        now = int(time.time())
        payload = {
            "iss": creds["client_email"],
            "scope": "https://www.googleapis.com/auth/calendar",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
        }

        signed = jose_jwt.encode(payload, creds["private_key"], algorithm="RS256")

        response = await self._client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": signed,
            },
        )
        response.raise_for_status()
        data = response.json()

        self._token = data["access_token"]
        self._token_expires = now + data.get("expires_in", 3600) - 60
        return self._token

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        """Requisição autenticada ao Calendar API."""
        token = await self._get_token()
        headers = {"Authorization": f"Bearer {token}"}
        response = await self._client.request(
            method, f"{self.BASE_URL}{path}", headers=headers, **kwargs
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    async def get_events(
        self,
        calendar_id: str,
        time_min: datetime,
        time_max: datetime,
    ) -> list[dict]:
        """Lista eventos em um período."""
        params = {
            "timeMin": time_min.isoformat() + "Z",
            "timeMax": time_max.isoformat() + "Z",
            "singleEvents": "true",
            "orderBy": "startTime",
        }
        data = await self._request("GET", f"/calendars/{calendar_id}/events", params=params)
        return data.get("items", [])

    async def get_free_slots(
        self,
        calendar_id: str,
        target_date: datetime,
        slot_duration_min: int = 30,
        day_start_hour: int = 8,
        day_end_hour: int = 18,
    ) -> list[dict]:
        """
        Retorna slots disponíveis em um dia.
        
        Returns:
            Lista de {"start": datetime, "end": datetime}
        """
        day_start = target_date.replace(hour=day_start_hour, minute=0, second=0)
        day_end = target_date.replace(hour=day_end_hour, minute=0, second=0)

        events = await self.get_events(calendar_id, day_start, day_end)

        busy = []
        for ev in events:
            start = ev.get("start", {}).get("dateTime", "")
            end = ev.get("end", {}).get("dateTime", "")
            if start and end:
                busy.append((
                    datetime.fromisoformat(start.replace("Z", "+00:00")),
                    datetime.fromisoformat(end.replace("Z", "+00:00")),
                ))

        # Gerar slots livres
        slots = []
        current = day_start
        slot_delta = timedelta(minutes=slot_duration_min)

        while current + slot_delta <= day_end:
            slot_end = current + slot_delta
            is_free = all(
                slot_end <= b_start or current >= b_end
                for b_start, b_end in busy
            )
            if is_free:
                slots.append({"start": current.isoformat(), "end": slot_end.isoformat()})
            current += slot_delta

        return slots

    async def create_event(
        self,
        calendar_id: str,
        start: datetime,
        end: datetime,
        summary: str,
        description: str | None = None,
        attendees: list[str] | None = None,
        reminders_minutes: list[int] | None = None,
    ) -> dict:
        """Cria um evento no calendário."""
        event = {
            "summary": summary,
            "start": {"dateTime": start.isoformat(), "timeZone": "America/Sao_Paulo"},
            "end": {"dateTime": end.isoformat(), "timeZone": "America/Sao_Paulo"},
        }
        if description:
            event["description"] = description
        if attendees:
            event["attendees"] = [{"email": e} for e in attendees]
        if reminders_minutes:
            event["reminders"] = {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": m} for m in reminders_minutes],
            }

        return await self._request("POST", f"/calendars/{calendar_id}/events", json=event)

    async def delete_event(self, calendar_id: str, event_id: str) -> None:
        """Remove um evento."""
        token = await self._get_token()
        await self._client.delete(
            f"{self.BASE_URL}/calendars/{calendar_id}/events/{event_id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    async def close(self):
        await self._client.aclose()


google_calendar = GoogleCalendarClient()
