"""
Testes puros do narrative compiler (sem DB).

Cobre:
- _humanize_when: passado, futuro, edge cases
- _format_fact_value: tipos primitivos, dict, list, None, bool
- _outcome_marker: positive, negative, pending, neutral
- compile_narrative: estado vazio, com fatos, com episódios, marcadores de
  confidence baixa, marcadores de outcome, ordenação por categoria.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


from memory.client_os.narrative import (
    _format_fact_value,
    _humanize_when,
    _outcome_marker,
    compile_narrative,
)


# ---------- _humanize_when ----------

class TestHumanizeWhen:
    def test_none_returns_placeholder(self):
        assert _humanize_when(None) == "data desconhecida"

    def test_recent_seconds(self):
        now = datetime.now(timezone.utc)
        assert _humanize_when(now) == "agora há pouco"

    def test_minutes_ago(self):
        dt = datetime.now(timezone.utc) - timedelta(minutes=30)
        assert "min" in _humanize_when(dt)

    def test_hours_ago(self):
        dt = datetime.now(timezone.utc) - timedelta(hours=5)
        result = _humanize_when(dt)
        assert "5h" in result or "4h" in result  # tolerância de borda

    def test_days_ago(self):
        dt = datetime.now(timezone.utc) - timedelta(days=3, seconds=10)
        assert _humanize_when(dt) in ("há 2 dias", "há 3 dias")

    def test_weeks_ago(self):
        dt = datetime.now(timezone.utc) - timedelta(days=14)
        result = _humanize_when(dt)
        assert "semana" in result

    def test_months_ago(self):
        dt = datetime.now(timezone.utc) - timedelta(days=90)
        result = _humanize_when(dt)
        assert "meses" in result or "mês" in result

    def test_future_minutes(self):
        dt = datetime.now(timezone.utc) + timedelta(minutes=30)
        result = _humanize_when(dt)
        assert result.startswith("em ")
        assert "min" in result

    def test_future_hours(self):
        dt = datetime.now(timezone.utc) + timedelta(hours=5)
        result = _humanize_when(dt)
        assert result.startswith("em ")
        assert "h" in result

    def test_future_days(self):
        dt = datetime.now(timezone.utc) + timedelta(days=3, seconds=10)
        # tolerância de borda — truncamento por microssegundos
        assert _humanize_when(dt) in ("em 2 dias", "em 3 dias")

    def test_naive_datetime_normalized_to_utc(self):
        # Naive datetime não deve quebrar — código normaliza pra UTC
        naive = datetime.utcnow() - timedelta(days=2)
        result = _humanize_when(naive)
        assert "há" in result


# ---------- _format_fact_value ----------

class TestFormatFactValue:
    def test_none(self):
        assert _format_fact_value(None) == "—"

    def test_bool_true(self):
        assert _format_fact_value(True) == "sim"

    def test_bool_false(self):
        assert _format_fact_value(False) == "não"

    def test_string(self):
        assert _format_fact_value("conservative") == "conservative"

    def test_int(self):
        assert _format_fact_value(42) == "42"

    def test_float(self):
        assert _format_fact_value(3.14) == "3.14"

    def test_empty_dict(self):
        assert _format_fact_value({}) == "—"

    def test_dict_with_items(self):
        result = _format_fact_value({"amount": 5000, "currency": "BRL"})
        assert "amount=5000" in result
        assert "currency=BRL" in result

    def test_empty_list(self):
        assert _format_fact_value([]) == "—"

    def test_list_with_items(self):
        assert _format_fact_value(["a", "b", "c"]) == "a, b, c"


# ---------- _outcome_marker ----------

class TestOutcomeMarker:
    def test_positive(self):
        assert _outcome_marker("positive") == " ✓"

    def test_negative(self):
        assert _outcome_marker("negative") == " ✗"

    def test_pending(self):
        assert _outcome_marker("pending") == " ⋯"

    def test_neutral(self):
        assert _outcome_marker("neutral") == ""

    def test_none(self):
        assert _outcome_marker(None) == ""

    def test_unknown(self):
        assert _outcome_marker("weird") == ""


# ---------- compile_narrative ----------

class TestCompileNarrative:
    def test_empty_snapshot_signals_explicitly(self):
        snap = {
            "client_slug": "ottoboni",
            "facts": [],
            "recent_episodes": [],
            "preferences": [],
            "open_loops": [],
            "objectives": [],
        }
        text = compile_narrative(snap)
        assert "ottoboni" in text
        assert "Nenhum estado conhecido" in text

    def test_includes_client_slug_in_header(self):
        snap = {
            "client_slug": "test_clinic",
            "facts": [],
            "recent_episodes": [],
            "preferences": [],
            "open_loops": [],
            "objectives": [],
        }
        text = compile_narrative(snap)
        assert "test_clinic" in text
        assert text.startswith("## Estado atual do cliente")

    def test_facts_grouped_by_category(self):
        snap = {
            "client_slug": "x",
            "facts": [
                {
                    "category": "owner_profile",
                    "key": "risk",
                    "value": "conservative",
                    "confidence": 1.0,
                },
                {
                    "category": "budget",
                    "key": "monthly",
                    "value": 5000,
                    "confidence": 1.0,
                },
                {
                    "category": "owner_profile",
                    "key": "tone",
                    "value": "formal",
                    "confidence": 1.0,
                },
            ],
            "recent_episodes": [],
            "preferences": [],
            "open_loops": [],
            "objectives": [],
        }
        text = compile_narrative(snap)
        # owner_profile aparece como header de seção
        assert "**owner_profile:**" in text
        assert "**budget:**" in text
        # categorias ordenadas alfabeticamente
        assert text.index("**budget:**") < text.index("**owner_profile:**")

    def test_low_confidence_shows_marker(self):
        snap = {
            "client_slug": "x",
            "facts": [
                {
                    "category": "owner_profile",
                    "key": "tone",
                    "value": "formal",
                    "confidence": 0.6,
                },
            ],
            "recent_episodes": [],
            "preferences": [],
            "open_loops": [],
            "objectives": [],
        }
        text = compile_narrative(snap)
        assert "confiança 60%" in text

    def test_high_confidence_omits_marker(self):
        snap = {
            "client_slug": "x",
            "facts": [
                {
                    "category": "owner_profile",
                    "key": "tone",
                    "value": "formal",
                    "confidence": 1.0,
                },
            ],
            "recent_episodes": [],
            "preferences": [],
            "open_loops": [],
            "objectives": [],
        }
        text = compile_narrative(snap)
        assert "confiança" not in text

    def test_episode_outcome_marker_rendered(self):
        snap = {
            "client_slug": "x",
            "facts": [],
            "recent_episodes": [
                {
                    "type": "campaign_launched",
                    "summary": "Lançou X",
                    "outcome": "positive",
                    "occurred_at": datetime.now(timezone.utc) - timedelta(hours=2),
                    "module_source": "M4",
                },
                {
                    "type": "lead_lost",
                    "summary": "Perdeu lead Y",
                    "outcome": "negative",
                    "occurred_at": datetime.now(timezone.utc) - timedelta(days=1),
                    "module_source": None,
                },
            ],
            "preferences": [],
            "open_loops": [],
            "objectives": [],
        }
        text = compile_narrative(snap)
        assert "Lançou X ✓" in text
        assert "Perdeu lead Y ✗" in text
        assert "[M4]" in text

    def test_objective_with_target_and_deadline(self):
        snap = {
            "client_slug": "x",
            "facts": [],
            "recent_episodes": [],
            "preferences": [],
            "open_loops": [],
            "objectives": [
                {
                    "title": "Reduzir CPL",
                    "target_metric": "cpl",
                    "target_value": 40.0,
                    "deadline": datetime.now(timezone.utc) + timedelta(days=30),
                    "progress": {},
                }
            ],
        }
        text = compile_narrative(snap)
        assert "Reduzir CPL" in text
        assert "cpl" in text
        assert "40.0" in text
        assert "prazo" in text

    def test_objective_without_target_or_deadline(self):
        snap = {
            "client_slug": "x",
            "facts": [],
            "recent_episodes": [],
            "preferences": [],
            "open_loops": [],
            "objectives": [
                {
                    "title": "Crescer base de pacientes",
                    "target_metric": None,
                    "target_value": None,
                    "deadline": None,
                    "progress": {},
                }
            ],
        }
        text = compile_narrative(snap)
        assert "Crescer base de pacientes" in text
        # Não deve haver "→" ou "prazo:"
        assert "→" not in text
        assert "prazo" not in text

    def test_preferences_with_evidence_count(self):
        snap = {
            "client_slug": "x",
            "facts": [],
            "recent_episodes": [],
            "preferences": [
                {
                    "topic": "approvals",
                    "pattern": "demora 3 dias para aprovar",
                    "evidence_count": 5,
                    "confidence": 0.9,
                }
            ],
            "open_loops": [],
            "objectives": [],
        }
        text = compile_narrative(snap)
        assert "[approvals]" in text
        assert "demora 3 dias" in text
        assert "5x" in text

    def test_preferences_single_observation_no_count_marker(self):
        snap = {
            "client_slug": "x",
            "facts": [],
            "recent_episodes": [],
            "preferences": [
                {
                    "topic": "copy_style",
                    "pattern": "prefere emocional",
                    "evidence_count": 1,
                    "confidence": 0.5,
                }
            ],
            "open_loops": [],
            "objectives": [],
        }
        text = compile_narrative(snap)
        assert "[copy_style]" in text
        # marker de count é "(observado Nx)" — checa que não há esse padrão.
        # ("observados" no header da seção não conta)
        assert "(observado " not in text

    def test_open_loops_with_owner_and_due_date(self):
        snap = {
            "client_slug": "x",
            "facts": [],
            "recent_episodes": [],
            "preferences": [],
            "open_loops": [
                {
                    "title": "Aguardar resposta sobre criativo",
                    "owner": "caio",
                    "due_at": datetime.now(timezone.utc) + timedelta(days=2),
                }
            ],
            "objectives": [],
        }
        text = compile_narrative(snap)
        assert "[caio]" in text
        assert "Aguardar resposta sobre criativo" in text
        assert "vencimento" in text

    def test_max_episodes_truncates(self):
        episodes = [
            {
                "type": "x",
                "summary": f"ep{i}",
                "outcome": "neutral",
                "occurred_at": datetime.now(timezone.utc) - timedelta(hours=i),
                "module_source": None,
            }
            for i in range(20)
        ]
        snap = {
            "client_slug": "x",
            "facts": [],
            "recent_episodes": episodes,
            "preferences": [],
            "open_loops": [],
            "objectives": [],
        }
        text = compile_narrative(snap, max_episodes=3)
        # Só os 3 primeiros devem aparecer
        assert "ep0" in text
        assert "ep2" in text
        assert "ep5" not in text

    def test_handles_missing_keys_gracefully(self):
        # snapshot mínimo, sem chaves opcionais
        snap = {"client_slug": "x"}
        text = compile_narrative(snap)
        assert "x" in text
        assert "Nenhum estado conhecido" in text

    def test_handles_unknown_slug_placeholder(self):
        snap = {
            "facts": [],
            "recent_episodes": [],
            "preferences": [],
            "open_loops": [],
            "objectives": [],
        }
        text = compile_narrative(snap)
        # Mesmo sem slug, não quebra; usa '?'
        assert "?" in text


# ---------- Sanidade do import ----------

class TestPublicAPI:
    def test_compile_narrative_is_importable_from_package(self):
        from memory.client_os import compile_narrative as cn

        assert cn is compile_narrative
