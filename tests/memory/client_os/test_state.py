"""
Testes de integração do ClientOS.

Cobre:
- Resolução por slug (for_slug)
- Fatos (upsert, get, validação de confidence)
- Episódios (record, list, filtros, validação de outcome)
- Preferências (observe, lista, increment, min_confidence)
- Pendências/open loops (open, close, list, owner filter, validação de status)
- Objetivos (add, list, progress merge, set status)
- Snapshot agregado
- Bump de version

Requer fixture `db_session` (AsyncSession PostgreSQL) do conftest raiz do projeto.
Usa features PG-only (JSONB, ON CONFLICT) e não roda em SQLite.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from memory.client_os import ClientNotFoundError, ClientOS, ClientOSError
from memory.client_os.schema import ClientStateRow


pytestmark = pytest.mark.asyncio


# ---------- Resolução por slug ----------

class TestClientResolution:
    async def test_for_slug_resolves_existing(self, db_session, sample_client):
        os_ = await ClientOS.for_slug(db_session, sample_client.slug)
        assert os_.client_slug == sample_client.slug
        assert os_.client_id == sample_client.id

    async def test_for_slug_raises_for_missing(self, db_session):
        with pytest.raises(ClientNotFoundError):
            await ClientOS.for_slug(db_session, "nao_existe_12345")


# ---------- Fatos ----------

class TestFacts:
    async def test_upsert_simple_value(self, client_os):
        await client_os.upsert_fact("owner_profile", "risk_tolerance", "conservative")
        facts = await client_os.get_facts(category="owner_profile")
        assert len(facts) == 1
        assert facts[0]["key"] == "risk_tolerance"
        assert facts[0]["value"] == "conservative"
        assert facts[0]["confidence"] == 1.0

    async def test_upsert_dict_value(self, client_os):
        await client_os.upsert_fact(
            "budget", "monthly", {"amount": 5000, "currency": "BRL"}
        )
        facts = await client_os.get_facts(category="budget")
        assert len(facts) == 1
        assert facts[0]["value"] == {"amount": 5000, "currency": "BRL"}

    async def test_upsert_overwrites_existing(self, client_os):
        await client_os.upsert_fact("specialty_focus", "primary", "implantes")
        await client_os.upsert_fact(
            "specialty_focus", "primary", "ortodontia", confidence=0.7
        )
        facts = await client_os.get_facts(category="specialty_focus")
        assert len(facts) == 1
        assert facts[0]["value"] == "ortodontia"
        assert facts[0]["confidence"] == 0.7

    async def test_get_facts_without_category_returns_all(self, client_os):
        await client_os.upsert_fact("owner_profile", "risk_tolerance", "high")
        await client_os.upsert_fact("budget", "monthly", 3000)
        all_facts = await client_os.get_facts()
        assert len(all_facts) == 2
        categories = {f["category"] for f in all_facts}
        assert categories == {"owner_profile", "budget"}

    async def test_upsert_rejects_invalid_confidence(self, client_os):
        with pytest.raises(ValueError):
            await client_os.upsert_fact("c", "k", "v", confidence=1.5)
        with pytest.raises(ValueError):
            await client_os.upsert_fact("c", "k", "v", confidence=-0.1)


# ---------- Episódios ----------

class TestEpisodes:
    async def test_record_and_retrieve(self, client_os):
        eid = await client_os.record_episode(
            "campaign_launched", "Primeira campanha de implantes lançada"
        )
        assert isinstance(eid, int) and eid > 0

        eps = await client_os.recent_episodes()
        assert len(eps) == 1
        assert eps[0]["type"] == "campaign_launched"
        assert eps[0]["outcome"] == "neutral"

    async def test_ordering_is_desc_by_occurred_at(self, client_os):
        now = datetime.now(timezone.utc)
        await client_os.record_episode(
            "older", "antigo", occurred_at=now - timedelta(days=3)
        )
        await client_os.record_episode(
            "newer", "novo", occurred_at=now - timedelta(hours=1)
        )
        eps = await client_os.recent_episodes()
        assert [e["type"] for e in eps] == ["newer", "older"]

    async def test_filter_by_episode_type(self, client_os):
        await client_os.record_episode("campaign_launched", "x")
        await client_os.record_episode("lead_converted", "y")
        eps = await client_os.recent_episodes(episode_type="lead_converted")
        assert len(eps) == 1
        assert eps[0]["type"] == "lead_converted"

    async def test_filter_by_since(self, client_os):
        now = datetime.now(timezone.utc)
        await client_os.record_episode(
            "old", "antigo", occurred_at=now - timedelta(days=10)
        )
        await client_os.record_episode("recent", "recente", occurred_at=now)
        cutoff = now - timedelta(days=1)
        eps = await client_os.recent_episodes(since=cutoff)
        assert len(eps) == 1
        assert eps[0]["type"] == "recent"

    async def test_invalid_outcome_raises(self, client_os):
        with pytest.raises(ValueError):
            await client_os.record_episode("x", "y", outcome="weird")

    async def test_limit_zero_raises(self, client_os):
        with pytest.raises(ValueError):
            await client_os.recent_episodes(limit=0)

    async def test_details_and_linked_refs_stored(self, client_os):
        await client_os.record_episode(
            "lead_converted",
            "Lead virou paciente",
            details={"value": 2500, "procedure": "implante"},
            linked_refs={"lead_id": 42, "campaign_id": 7},
            outcome="positive",
            module_source="M3",
        )
        eps = await client_os.recent_episodes()
        assert eps[0]["details"] == {"value": 2500, "procedure": "implante"}
        assert eps[0]["linked_refs"] == {"lead_id": 42, "campaign_id": 7}
        assert eps[0]["outcome"] == "positive"
        assert eps[0]["module_source"] == "M3"


# ---------- Preferências ----------

class TestPreferences:
    async def test_first_observation_creates_with_confidence_05(self, client_os):
        await client_os.observe_preference("approvals", "aprova só após 3 opções")
        prefs = await client_os.get_preferences(min_confidence=0.0)
        assert len(prefs) == 1
        assert prefs[0]["confidence"] == 0.5
        assert prefs[0]["evidence_count"] == 1

    async def test_repeated_observation_increments(self, client_os):
        for _ in range(3):
            await client_os.observe_preference(
                "copy_style", "responde melhor a copy emocional"
            )
        prefs = await client_os.get_preferences(min_confidence=0.0)
        assert len(prefs) == 1
        assert prefs[0]["evidence_count"] == 3
        # 0.5 inicial + 0.1 * 2 incrementos (criação não incrementa) = 0.7
        assert prefs[0]["confidence"] == pytest.approx(0.7, abs=0.01)

    async def test_confidence_capped_at_one(self, client_os):
        for _ in range(20):
            await client_os.observe_preference("ch", "X", confidence_increment=0.5)
        prefs = await client_os.get_preferences(min_confidence=0.0)
        assert prefs[0]["confidence"] == 1.0

    async def test_min_confidence_filter(self, client_os):
        await client_os.observe_preference("topic_a", "low")  # confidence 0.5
        for _ in range(5):
            await client_os.observe_preference("topic_b", "high")  # vai pra 1.0
        prefs_high = await client_os.get_preferences(min_confidence=0.7)
        assert len(prefs_high) == 1
        assert prefs_high[0]["topic"] == "topic_b"

    async def test_filter_by_topic(self, client_os):
        await client_os.observe_preference("approvals", "x")
        await client_os.observe_preference("copy_style", "y")
        prefs = await client_os.get_preferences(
            topic="approvals", min_confidence=0.0
        )
        assert len(prefs) == 1
        assert prefs[0]["topic"] == "approvals"


# ---------- Open Loops ----------

class TestOpenLoops:
    async def test_open_creates_loop(self, client_os):
        lid = await client_os.open_loop(
            "Aguardar resposta sobre criativo", owner="caio"
        )
        loops = await client_os.open_loops()
        assert len(loops) == 1
        assert loops[0]["id"] == lid
        assert loops[0]["owner"] == "caio"

    async def test_close_resolves(self, client_os):
        lid = await client_os.open_loop("Revisar relatório", owner="villa")
        await client_os.close_loop(lid, status="resolved")
        loops = await client_os.open_loops()
        assert len(loops) == 0

    async def test_close_abandoned_also_removes_from_open(self, client_os):
        lid = await client_os.open_loop("Algo descartado")
        await client_os.close_loop(lid, status="abandoned")
        loops = await client_os.open_loops()
        assert len(loops) == 0

    async def test_filter_by_owner(self, client_os):
        await client_os.open_loop("p1", owner="caio")
        await client_os.open_loop("p2", owner="thais")
        await client_os.open_loop("p3", owner="caio")
        caio_loops = await client_os.open_loops(owner="caio")
        assert len(caio_loops) == 2
        assert all(loop["owner"] == "caio" for loop in caio_loops)

    async def test_close_invalid_status_raises(self, client_os):
        lid = await client_os.open_loop("x")
        with pytest.raises(ValueError):
            await client_os.close_loop(lid, status="kinda_done")

    async def test_close_nonexistent_raises_clientoserror(self, client_os):
        with pytest.raises(ClientOSError):
            await client_os.close_loop(99999)

    async def test_close_already_closed_raises(self, client_os):
        lid = await client_os.open_loop("y")
        await client_os.close_loop(lid)
        with pytest.raises(ClientOSError):
            await client_os.close_loop(lid)


# ---------- Objetivos ----------

class TestObjectives:
    async def test_add_and_list(self, client_os):
        oid = await client_os.add_objective(
            "Reduzir CPL em 20%", target_metric="cpl", target_value=40.0
        )
        objectives = await client_os.active_objectives()
        assert len(objectives) == 1
        assert objectives[0]["id"] == oid
        assert objectives[0]["target_metric"] == "cpl"
        assert objectives[0]["target_value"] == 40.0
        assert objectives[0]["progress"] == {}

    async def test_progress_merge(self, client_os):
        oid = await client_os.add_objective("Crescer base de pacientes")
        await client_os.update_objective_progress(oid, {"current": 50.0})
        await client_os.update_objective_progress(oid, {"trend": "improving"})
        objectives = await client_os.active_objectives()
        assert objectives[0]["progress"] == {
            "current": 50.0,
            "trend": "improving",
        }

    async def test_progress_overwrites_same_key(self, client_os):
        oid = await client_os.add_objective("X")
        await client_os.update_objective_progress(oid, {"current": 50.0})
        await client_os.update_objective_progress(oid, {"current": 70.0})
        objectives = await client_os.active_objectives()
        assert objectives[0]["progress"]["current"] == 70.0

    async def test_progress_patch_must_be_dict(self, client_os):
        oid = await client_os.add_objective("X")
        with pytest.raises(ValueError):
            await client_os.update_objective_progress(oid, ["nope"])  # type: ignore[arg-type]

    async def test_set_status_removes_from_active(self, client_os):
        oid = await client_os.add_objective("X")
        await client_os.set_objective_status(oid, "achieved")
        objectives = await client_os.active_objectives()
        assert len(objectives) == 0

    async def test_set_status_invalid_raises(self, client_os):
        oid = await client_os.add_objective("X")
        with pytest.raises(ValueError):
            await client_os.set_objective_status(oid, "weird_status")

    async def test_update_unknown_objective_raises(self, client_os):
        with pytest.raises(ClientOSError):
            await client_os.update_objective_progress(99999, {"x": 1})


# ---------- Snapshot ----------

class TestSnapshot:
    async def test_empty_snapshot(self, client_os):
        snap = await client_os.snapshot()
        assert snap["client_slug"] == client_os.client_slug
        assert snap["facts"] == []
        assert snap["recent_episodes"] == []
        assert snap["preferences"] == []
        assert snap["open_loops"] == []
        assert snap["objectives"] == []

    async def test_populated_snapshot(self, client_os):
        await client_os.upsert_fact("owner_profile", "risk", "conservative")
        await client_os.record_episode("report_sent", "Relatório enviado")
        await client_os.observe_preference("approvals", "demora 3 dias")
        await client_os.open_loop("Esperar Caio", owner="caio")
        await client_os.add_objective("Reduzir CPL", target_metric="cpl")

        snap = await client_os.snapshot()
        assert len(snap["facts"]) == 1
        assert len(snap["recent_episodes"]) == 1
        # preferences only show when >= 0.5 confidence (default); 1 obs = 0.5
        assert len(snap["preferences"]) == 1
        assert len(snap["open_loops"]) == 1
        assert len(snap["objectives"]) == 1


# ---------- Bump de version ----------

class TestVersionBumping:
    async def _get_version(self, db_session, client_id):
        result = await db_session.execute(
            select(ClientStateRow).where(ClientStateRow.client_id == client_id)
        )
        row = result.scalar_one_or_none()
        return row.version if row else None

    async def test_version_starts_at_one_after_first_write(self, db_session, client_os):
        await client_os.upsert_fact("c", "k", "v")
        version = await self._get_version(db_session, client_os.client_id)
        assert version == 1

    async def test_version_increments_on_subsequent_writes(self, db_session, client_os):
        await client_os.upsert_fact("c", "k1", "v1")
        await client_os.upsert_fact("c", "k2", "v2")
        await client_os.record_episode("test", "x")
        version = await self._get_version(db_session, client_os.client_id)
        # 1 (criação) + 2 increments
        assert version == 3


# ---------- Narrative integration ----------

class TestNarrativeIntegration:
    async def test_narrative_returns_string(self, client_os):
        text = await client_os.narrative()
        assert isinstance(text, str)
        assert client_os.client_slug in text

    async def test_narrative_empty_state_signals_explicitly(self, client_os):
        text = await client_os.narrative()
        assert "Nenhum estado conhecido" in text

    async def test_narrative_includes_facts_and_episodes(self, client_os):
        await client_os.upsert_fact("budget", "monthly", 5000)
        await client_os.record_episode("campaign_launched", "Campanha X")
        text = await client_os.narrative()
        assert "budget" in text
        assert "Campanha X" in text
