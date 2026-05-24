"""
Villa — Módulo M05: Agendamento Automático
Integra Google Calendar + Kommo + WhatsApp para agendar consultas.
Verifica disponibilidade, oferece horários, confirma e lembra D-1.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️  STAND_BY — Decisão reunião Caio+Thaís (19/05/2026)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Motivo: Depende de WhatsApp para envio de horários e
confirmações. Paused junto com M03 e M06 até que haja
segurança de operação sem risco à BM da Thaís.

Integração com Google Calendar permanece disponível
para uso manual. Apenas os envios por WhatsApp estão
bloqueados.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Appointment, Client, Lead, ModuleCode, User
from integrations.google_calendar import google_calendar
from integrations.kommo import kommo
from integrations.whatsapp import whatsapp
from memory.feedback_loop import FeedbackLoop
from modules.base import BaseModule

SYSTEM_PROMPT = """Você é o Villa, módulo de agendamento da WebXP Agency.

Sua função é agendar consultas entre leads qualificados e os profissionais dos clientes da WebXP.

## Regras
1. Sempre verificar disponibilidade no Google Calendar antes de oferecer horários
2. Oferecer 3 opções de horário (manhã, tarde, próximo dia útil)
3. Mensagens curtas no WhatsApp, tom acolhedor
4. Após confirmação: criar evento no Calendar, mover card no Kommo, enviar confirmação
5. Lembrete D-1 automático
6. Se lead não confirma em 2h, enviar follow-up
"""

SLOT_OFFER_PROMPT = """Gere uma mensagem de WhatsApp oferecendo horários para agendamento.

LEAD: {lead_name}
CLIENTE: {client_name}
ESPECIALIDADE: {specialty}
HORÁRIOS DISPONÍVEIS:
{available_slots}

A mensagem deve:
- Ser curta e acolhedora
- Apresentar os 3 melhores horários
- Facilitar a escolha (numerar)
- Sem ponto final no fim

Responda APENAS com o texto da mensagem.
"""


class M05Agendamento(BaseModule):
    """Módulo de agendamento automático."""

    code = ModuleCode.M05_AGENDAMENTO
    name = "Agendamento"
    description = (
        "Agenda consultas integrando Google Calendar, Kommo e WhatsApp. "
        "Verifica disponibilidade, oferece horários, confirma e envia lembrete D-1."
    )

    # ── STAND_BY (ver docstring do módulo) ──
    STAND_BY = True

    KEYWORDS = [
        "consulta", "consultas",
        "horário", "horario", "horários",
        "marcar", "remarcar", "cancelar",
        "disponibilidade", "disponível",
        "calendar", "calendário",
    ]

    async def can_handle(self, message: str, context: dict | None = None) -> float:
        msg_lower = message.lower()
        if context and "agendamento" in context.get("event_type", ""):
            return 0.9
        matches = sum(1 for kw in self.KEYWORDS if kw in msg_lower)
        if matches >= 2: return 0.85
        if matches >= 1: return 0.65
        return 0.0

    async def execute(
        self,
        message: str,
        db: AsyncSession,
        user: User | None = None,
        client_slug: str | None = None,
        context: dict | None = None,
    ) -> dict:
        if self.STAND_BY:
            return {
                "success": False,
                "message": (
                    "⏸️ M05 Agendamento está em STAND_BY (reunião Caio+Thaís, 19/05/2026). "
                    "Envio por WhatsApp pausado. Google Calendar continua disponível para uso manual."
                ),
                "actions_taken": ["stand_by_blocked"],
            }
        feedback_loop = FeedbackLoop(db)
        context = context or {}

        client = await self._resolve_client(db, client_slug, message)
        if not client:
            return {"success": False, "message": "Cliente não identificado.", "actions_taken": []}

        client_config = client.config or {}
        calendar_id = client_config.get("google_calendar_id")

        if not calendar_id:
            return {
                "success": False,
                "message": f"Google Calendar não configurado para {client.name}. Configure em clients.config.google_calendar_id.",
                "actions_taken": ["calendar_not_configured"],
            }

        # Buscar slots disponíveis para os próximos 3 dias úteis
        slots = []
        check_date = datetime.now()
        days_checked = 0

        while days_checked < 3:
            check_date += timedelta(days=1)
            if check_date.weekday() >= 5:  # Pular fim de semana
                continue

            day_slots = await google_calendar.get_free_slots(
                calendar_id=calendar_id,
                target_date=check_date,
                slot_duration_min=client_config.get("appointment_duration", 30),
                day_start_hour=client_config.get("day_start", 8),
                day_end_hour=client_config.get("day_end", 18),
            )
            for s in day_slots:
                slots.append({**s, "date": check_date.strftime("%d/%m")})
            days_checked += 1

        if not slots:
            return {
                "success": True,
                "message": f"Não há horários disponíveis nos próximos 3 dias úteis para {client.name}.",
                "actions_taken": ["no_slots_available"],
            }

        # Selecionar os 3 melhores horários (manhã, tarde, outro dia)
        best_slots = slots[:3] if len(slots) >= 3 else slots
        slots_text = "\n".join(
            f"{i+1}. {s['date']} às {s['start'].split('T')[1][:5]}"
            for i, s in enumerate(best_slots)
        )

        # Gerar mensagem de oferta
        response = await self.ask_claude(
            message=SLOT_OFFER_PROMPT.format(
                lead_name=context.get("lead_name", ""),
                client_name=client.name,
                specialty=client.specialty or "odontologia",
                available_slots=slots_text,
            ),
            db=db,
            system_override=SYSTEM_PROMPT,
            client_slug=client.slug,
        )

        await feedback_loop.record_decision(
            module=self.code, action="oferecer_horarios",
            input_data={"client": client.slug, "slots_available": len(slots)},
            output_data={"slots_offered": len(best_slots), "message": response["text"][:200]},
            reasoning=f"Encontrados {len(slots)} slots em 3 dias úteis.",
            client_slug=client.slug,
        )

        return {
            "success": True,
            "message": response["text"],
            "data": {
                "client": client.slug,
                "available_slots": best_slots,
                "total_slots": len(slots),
            },
            "actions_taken": ["slots_checked", "offer_generated"],
        }

    async def create_appointment(
        self,
        db: AsyncSession,
        lead: Lead,
        client: Client,
        slot_start: datetime,
        slot_end: datetime,
    ) -> dict:
        """Cria agendamento completo: Calendar + Kommo + WhatsApp + banco."""
        client_config = client.config or {}
        calendar_id = client_config.get("google_calendar_id", "")
        actions = []

        # Criar evento no Google Calendar
        event = {}
        if calendar_id:
            try:
                event = await google_calendar.create_event(
                    calendar_id=calendar_id,
                    start=slot_start,
                    end=slot_end,
                    summary=f"Consulta — {lead.name or 'Lead'}",
                    description=f"Lead: {lead.name}\nTelefone: {lead.phone}\nScore: {lead.qualification_score}",
                    reminders_minutes=[60, 15],
                )
                actions.append("calendar_event_created")
            except Exception:
                actions.append("calendar_event_failed")

        # Salvar no banco
        appointment = Appointment(
            id=str(uuid4()),
            lead_id=lead.id,
            client_id=client.id,
            scheduled_at=slot_start,
            duration_minutes=client_config.get("appointment_duration", 30),
            google_event_id=event.get("id"),
            status="scheduled",
        )
        db.add(appointment)
        await db.flush()
        actions.append("appointment_saved")

        # Enviar confirmação via WhatsApp
        if lead.phone:
            try:
                confirm_msg = (
                    f"Consulta confirmada! 📅\n"
                    f"{slot_start.strftime('%d/%m às %H:%M')}\n"
                    f"Te envio um lembrete amanhã"
                )
                await whatsapp.send_text(lead.phone, confirm_msg)
                actions.append("confirmation_sent")
            except Exception:
                actions.append("confirmation_failed")

        # Mover card no Kommo
        if lead.kommo_lead_id:
            scheduled_status = client_config.get("kommo_status_map", {}).get("scheduled")
            if scheduled_status:
                try:
                    await kommo.move_lead(lead.kommo_lead_id, int(scheduled_status))
                    await kommo.add_note(lead.kommo_lead_id, f"Consulta agendada para {slot_start.strftime('%d/%m %H:%M')} pelo Villa")
                    actions.append("kommo_updated")
                except Exception:
                    actions.append("kommo_update_failed")

        return {"appointment_id": appointment.id, "actions": actions}

    async def _resolve_client(self, db, slug, message):
        if slug:
            r = await db.execute(select(Client).where(Client.slug == slug))
            return r.scalar_one_or_none()
        r = await db.execute(select(Client))
        for c in r.scalars().all():
            if c.name.lower() in message.lower() or c.slug.lower() in message.lower():
                return c
        return None
