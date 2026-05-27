"""
ClientOS — API principal de leitura e escrita do estado narrativo por cliente.

Cada método é independente, async, com try/except + logger conforme padrão do projeto.
A classe é instanciada uma vez por requisição (não há cache cross-request).

Fluxo típico:
    os_ = await ClientOS.for_slug(db, "ottoboni")
    text = await os_.narrative()                 # estado compilado em texto
    await os_.record_episode("campaign_launched", "...")
    await os_.observe_preference("approvals", "...")
    await os_.open_loop("Aguardar resposta", owner="caio")
"""
from __future__ import annotations

import uuid

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import and_, desc, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .exceptions import ClientNotFoundError, ClientOSError
from .schema import (
    ClientEpisode,
    ClientFact,
    ClientObjective,
    ClientPendingItem,
    ClientPreference,
    ClientStateRow,
)

logger = logging.getLogger(__name__)

# Valores válidos para campos enumerados
VALID_OUTCOMES = frozenset({"positive", "negative", "neutral", "pending"})
VALID_LOOP_CLOSE_STATUSES = frozenset({"resolved", "abandoned"})
VALID_OBJECTIVE_STATUSES = frozenset({"active", "paused", "achieved", "abandoned"})


class ClientOS:
    """
    Camada de estado narrativo vivo por cliente.

    Diferente do feedback_loop (RAG semântico em interações passadas), o ClientOS
    mantém estado *estruturado*: fatos, episódios, preferências, pendências e
    objetivos. Compila narrativa textual injetável em prompts via `.narrative()`.
    """

    def __init__(self, db: AsyncSession, client_id: uuid.UUID, client_slug: str):
        self.db = db
        self.client_id = client_id
        self.client_slug = client_slug

    # ---------- BOOTSTRAP ----------

    @classmethod
    async def for_slug(cls, db: AsyncSession, client_slug: str) -> ClientOS:
        """
        Resolve client_slug → ClientOS pronto pra usar.

        Raises:
            ClientNotFoundError: se o slug não existe na tabela clients
            ClientOSError: se core.models.Client não pôde ser importado
        """
        try:
            # Import local pra evitar circular se core.models importar de memory/*
            from core.models import Client  # type: ignore
        except ImportError as e:
            logger.error("client_os.for_slug: não foi possível importar core.models.Client: %s", e)
            raise ClientOSError("core.models.Client não encontrado") from e

        try:
            result = await db.execute(
                select(Client.id).where(Client.slug == client_slug)
            )
            client_id = result.scalar_one_or_none()
        except Exception as e:
            logger.error("client_os.for_slug: erro buscando cliente '%s': %s", client_slug, e)
            raise ClientOSError(f"Erro ao resolver cliente '{client_slug}'") from e

        if client_id is None:
            raise ClientNotFoundError(f"Cliente '{client_slug}' não encontrado")

        return cls(db=db, client_id=client_id, client_slug=client_slug)

    # ---------- FATOS ----------

    async def upsert_fact(
        self,
        category: str,
        key: str,
        value: Any,
        source: str = "manual",
        confidence: float = 1.0,
    ) -> None:
        """
        Insere ou atualiza um fato. (client_id, category, key) é a chave única.

        Args:
            category: agrupador semântico ("owner_profile", "budget", etc)
            key: identificador único dentro da category ("risk_tolerance", "monthly")
            value: qualquer JSON serializável (string, int, dict, list...)
            source: de onde veio ("manual", "inferred_from_M2", "kommo_sync"...)
            confidence: 0..1, quão confiável é o fato
        """
        if not 0 <= confidence <= 1:
            raise ValueError("confidence deve estar entre 0 e 1")

        try:
            stmt = (
                pg_insert(ClientFact)
                .values(
                    client_id=self.client_id,
                    category=category,
                    key=key,
                    value=value,
                    source=source,
                    confidence=confidence,
                )
                .on_conflict_do_update(
                    index_elements=["client_id", "category", "key"],
                    set_={
                        "value": value,
                        "source": source,
                        "confidence": confidence,
                        "updated_at": func.now(),
                    },
                )
            )
            await self.db.execute(stmt)
            await self._bump_version()
            logger.info(
                "client_os.upsert_fact: %s [%s.%s] conf=%.2f",
                self.client_slug, category, key, confidence,
            )
        except Exception as e:
            logger.error(
                "client_os.upsert_fact erro %s/%s/%s: %s",
                self.client_slug, category, key, e,
            )
            raise ClientOSError("Erro ao upsert fato") from e

    async def get_facts(
        self, category: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Lista fatos do cliente, opcionalmente filtrando por category."""
        try:
            stmt = select(ClientFact).where(ClientFact.client_id == self.client_id)
            if category:
                stmt = stmt.where(ClientFact.category == category)
            stmt = stmt.order_by(ClientFact.category, ClientFact.key)

            result = await self.db.execute(stmt)
            return [
                {
                    "category": f.category,
                    "key": f.key,
                    "value": f.value,
                    "source": f.source,
                    "confidence": f.confidence,
                    "updated_at": f.updated_at,
                }
                for f in result.scalars().all()
            ]
        except Exception as e:
            logger.error(
                "client_os.get_facts erro %s/%s: %s", self.client_slug, category, e
            )
            raise ClientOSError("Erro ao buscar fatos") from e

    # ---------- EPISÓDIOS ----------

    async def record_episode(
        self,
        episode_type: str,
        summary: str,
        details: Optional[dict] = None,
        outcome: str = "neutral",
        module_source: Optional[str] = None,
        linked_refs: Optional[dict] = None,
        occurred_at: Optional[datetime] = None,
    ) -> int:
        """
        Registra um episódio (algo que aconteceu) na timeline do cliente.

        Returns:
            ID do episódio criado
        """
        if outcome not in VALID_OUTCOMES:
            raise ValueError(
                f"outcome deve ser um de {sorted(VALID_OUTCOMES)}; recebeu '{outcome}'"
            )

        try:
            episode = ClientEpisode(
                client_id=self.client_id,
                episode_type=episode_type,
                summary=summary,
                details=details or {},
                outcome=outcome,
                module_source=module_source,
                linked_refs=linked_refs or {},
                occurred_at=occurred_at or datetime.now(timezone.utc),
            )
            self.db.add(episode)
            await self.db.flush()
            await self._bump_version()
            logger.info(
                "client_os.record_episode: %s [%s] %s",
                self.client_slug, episode_type, summary[:80],
            )
            return episode.id
        except Exception as e:
            logger.error(
                "client_os.record_episode erro %s/%s: %s",
                self.client_slug, episode_type, e,
            )
            raise ClientOSError("Erro ao registrar episódio") from e

    async def recent_episodes(
        self,
        limit: int = 10,
        episode_type: Optional[str] = None,
        since: Optional[datetime] = None,
    ) -> list[dict[str, Any]]:
        """Retorna episódios recentes, ordenados do mais novo pro mais antigo."""
        if limit <= 0:
            raise ValueError("limit deve ser > 0")

        try:
            stmt = select(ClientEpisode).where(
                ClientEpisode.client_id == self.client_id
            )
            if episode_type:
                stmt = stmt.where(ClientEpisode.episode_type == episode_type)
            if since:
                stmt = stmt.where(ClientEpisode.occurred_at >= since)
            stmt = stmt.order_by(desc(ClientEpisode.occurred_at)).limit(limit)

            result = await self.db.execute(stmt)
            return [
                {
                    "id": e.id,
                    "type": e.episode_type,
                    "summary": e.summary,
                    "details": e.details,
                    "outcome": e.outcome,
                    "module_source": e.module_source,
                    "linked_refs": e.linked_refs,
                    "occurred_at": e.occurred_at,
                }
                for e in result.scalars().all()
            ]
        except Exception as e:
            logger.error("client_os.recent_episodes erro %s: %s", self.client_slug, e)
            raise ClientOSError("Erro ao buscar episódios") from e

    # ---------- PREFERÊNCIAS ----------

    async def observe_preference(
        self,
        topic: str,
        pattern: str,
        confidence_increment: float = 0.1,
    ) -> None:
        """
        Registra observação de um padrão de comportamento.

        Se (client, topic, pattern) já existe, incrementa evidence_count e
        ajusta confidence (capped em 1.0). Senão, cria novo registro com
        confidence inicial 0.5.
        """
        if not 0 <= confidence_increment <= 1:
            raise ValueError("confidence_increment deve estar entre 0 e 1")

        try:
            result = await self.db.execute(
                select(ClientPreference).where(
                    and_(
                        ClientPreference.client_id == self.client_id,
                        ClientPreference.topic == topic,
                        ClientPreference.pattern == pattern,
                    )
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.evidence_count += 1
                existing.confidence = min(
                    1.0, existing.confidence + confidence_increment
                )
                existing.last_observed_at = datetime.now(timezone.utc)
            else:
                self.db.add(
                    ClientPreference(
                        client_id=self.client_id,
                        topic=topic,
                        pattern=pattern,
                        evidence_count=1,
                        confidence=0.5,
                    )
                )

            await self.db.flush()
            await self._bump_version()
            logger.info(
                "client_os.observe_preference: %s [%s] %s",
                self.client_slug, topic, pattern[:60],
            )
        except Exception as e:
            logger.error(
                "client_os.observe_preference erro %s/%s: %s",
                self.client_slug, topic, e,
            )
            raise ClientOSError("Erro ao observar preferência") from e

    async def get_preferences(
        self,
        topic: Optional[str] = None,
        min_confidence: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Lista preferências com confidence >= min_confidence."""
        try:
            stmt = select(ClientPreference).where(
                and_(
                    ClientPreference.client_id == self.client_id,
                    ClientPreference.confidence >= min_confidence,
                )
            )
            if topic:
                stmt = stmt.where(ClientPreference.topic == topic)
            stmt = stmt.order_by(desc(ClientPreference.confidence))

            result = await self.db.execute(stmt)
            return [
                {
                    "topic": p.topic,
                    "pattern": p.pattern,
                    "evidence_count": p.evidence_count,
                    "confidence": p.confidence,
                    "last_observed_at": p.last_observed_at,
                }
                for p in result.scalars().all()
            ]
        except Exception as e:
            logger.error(
                "client_os.get_preferences erro %s: %s", self.client_slug, e
            )
            raise ClientOSError("Erro ao buscar preferências") from e

    # ---------- PENDÊNCIAS (OPEN LOOPS) ----------

    async def open_loop(
        self,
        title: str,
        description: Optional[str] = None,
        owner: str = "villa",
        due_at: Optional[datetime] = None,
        module_source: Optional[str] = None,
    ) -> int:
        """Abre uma pendência. Retorna o ID."""
        try:
            item = ClientPendingItem(
                client_id=self.client_id,
                title=title,
                description=description,
                owner=owner,
                due_at=due_at,
                module_source=module_source,
            )
            self.db.add(item)
            await self.db.flush()
            await self._bump_version()
            logger.info(
                "client_os.open_loop: %s [%s] %s",
                self.client_slug, owner, title[:80],
            )
            return item.id
        except Exception as e:
            logger.error("client_os.open_loop erro %s: %s", self.client_slug, e)
            raise ClientOSError("Erro ao abrir pendência") from e

    async def close_loop(self, loop_id: int, status: str = "resolved") -> None:
        """Fecha uma pendência aberta (status='resolved' ou 'abandoned')."""
        if status not in VALID_LOOP_CLOSE_STATUSES:
            raise ValueError(
                f"status deve ser um de {sorted(VALID_LOOP_CLOSE_STATUSES)}; recebeu '{status}'"
            )

        try:
            result = await self.db.execute(
                update(ClientPendingItem)
                .where(
                    and_(
                        ClientPendingItem.id == loop_id,
                        ClientPendingItem.client_id == self.client_id,
                        ClientPendingItem.status == "open",
                    )
                )
                .values(status=status, resolved_at=func.now())
            )
            if result.rowcount == 0:
                # ID errado, cliente errado, ou já fechado
                raise ClientOSError(
                    f"Pendência {loop_id} não encontrada/aberta para {self.client_slug}"
                )
            await self._bump_version()
            logger.info(
                "client_os.close_loop: %s id=%s status=%s",
                self.client_slug, loop_id, status,
            )
        except ClientOSError:
            raise
        except Exception as e:
            logger.error(
                "client_os.close_loop erro %s id=%s: %s",
                self.client_slug, loop_id, e,
            )
            raise ClientOSError("Erro ao fechar pendência") from e

    async def open_loops(
        self, owner: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Lista pendências abertas, ordenadas por due_at (nulls last)."""
        try:
            stmt = select(ClientPendingItem).where(
                and_(
                    ClientPendingItem.client_id == self.client_id,
                    ClientPendingItem.status == "open",
                )
            )
            if owner:
                stmt = stmt.where(ClientPendingItem.owner == owner)
            stmt = stmt.order_by(
                ClientPendingItem.due_at.asc().nulls_last(),
                ClientPendingItem.created_at.asc(),
            )

            result = await self.db.execute(stmt)
            return [
                {
                    "id": i.id,
                    "title": i.title,
                    "description": i.description,
                    "owner": i.owner,
                    "due_at": i.due_at,
                    "created_at": i.created_at,
                    "module_source": i.module_source,
                }
                for i in result.scalars().all()
            ]
        except Exception as e:
            logger.error("client_os.open_loops erro %s: %s", self.client_slug, e)
            raise ClientOSError("Erro ao listar pendências") from e

    # ---------- OBJETIVOS ----------

    async def add_objective(
        self,
        title: str,
        description: Optional[str] = None,
        target_metric: Optional[str] = None,
        target_value: Optional[float] = None,
        deadline: Optional[datetime] = None,
    ) -> int:
        """Adiciona um objetivo ativo. Retorna o ID."""
        try:
            obj = ClientObjective(
                client_id=self.client_id,
                title=title,
                description=description,
                target_metric=target_metric,
                target_value=target_value,
                deadline=deadline,
                progress={},
                status="active",
            )
            self.db.add(obj)
            await self.db.flush()
            await self._bump_version()
            logger.info(
                "client_os.add_objective: %s [%s]", self.client_slug, title
            )
            return obj.id
        except Exception as e:
            logger.error("client_os.add_objective erro %s: %s", self.client_slug, e)
            raise ClientOSError("Erro ao adicionar objetivo") from e

    async def update_objective_progress(
        self, objective_id: int, progress_patch: dict
    ) -> None:
        """Faz merge de progress_patch no progress atual do objetivo."""
        if not isinstance(progress_patch, dict):
            raise ValueError("progress_patch deve ser dict")

        try:
            result = await self.db.execute(
                select(ClientObjective).where(
                    and_(
                        ClientObjective.id == objective_id,
                        ClientObjective.client_id == self.client_id,
                    )
                )
            )
            obj = result.scalar_one_or_none()
            if obj is None:
                raise ClientOSError(
                    f"Objetivo {objective_id} não encontrado para {self.client_slug}"
                )

            # SQLAlchemy detecta mudança em JSONB se atribuirmos novo dict
            obj.progress = {**(obj.progress or {}), **progress_patch}
            await self.db.flush()
            await self._bump_version()
            logger.info(
                "client_os.update_objective_progress %s id=%s",
                self.client_slug, objective_id,
            )
        except ClientOSError:
            raise
        except Exception as e:
            logger.error(
                "client_os.update_objective_progress erro %s/%s: %s",
                self.client_slug, objective_id, e,
            )
            raise ClientOSError("Erro ao atualizar progresso") from e

    async def set_objective_status(self, objective_id: int, status: str) -> None:
        """Muda status de um objetivo. Útil pra marcar 'achieved' ou 'abandoned'."""
        if status not in VALID_OBJECTIVE_STATUSES:
            raise ValueError(
                f"status deve ser um de {sorted(VALID_OBJECTIVE_STATUSES)}; recebeu '{status}'"
            )

        try:
            result = await self.db.execute(
                update(ClientObjective)
                .where(
                    and_(
                        ClientObjective.id == objective_id,
                        ClientObjective.client_id == self.client_id,
                    )
                )
                .values(status=status)
            )
            if result.rowcount == 0:
                raise ClientOSError(
                    f"Objetivo {objective_id} não encontrado para {self.client_slug}"
                )
            await self._bump_version()
            logger.info(
                "client_os.set_objective_status: %s id=%s -> %s",
                self.client_slug, objective_id, status,
            )
        except ClientOSError:
            raise
        except Exception as e:
            logger.error(
                "client_os.set_objective_status erro %s/%s: %s",
                self.client_slug, objective_id, e,
            )
            raise ClientOSError("Erro ao mudar status de objetivo") from e

    async def active_objectives(self) -> list[dict[str, Any]]:
        """Lista objetivos status='active', ordenados por deadline (nulls last)."""
        try:
            stmt = (
                select(ClientObjective)
                .where(
                    and_(
                        ClientObjective.client_id == self.client_id,
                        ClientObjective.status == "active",
                    )
                )
                .order_by(
                    ClientObjective.deadline.asc().nulls_last(),
                    ClientObjective.created_at.asc(),
                )
            )

            result = await self.db.execute(stmt)
            return [
                {
                    "id": o.id,
                    "title": o.title,
                    "description": o.description,
                    "target_metric": o.target_metric,
                    "target_value": o.target_value,
                    "deadline": o.deadline,
                    "progress": o.progress,
                    "created_at": o.created_at,
                }
                for o in result.scalars().all()
            ]
        except Exception as e:
            logger.error(
                "client_os.active_objectives erro %s: %s", self.client_slug, e
            )
            raise ClientOSError("Erro ao listar objetivos ativos") from e

    # ---------- SNAPSHOT + NARRATIVE ----------

    async def snapshot(self) -> dict[str, Any]:
        """Snapshot completo do estado pra inspeção / compilação narrativa."""
        try:
            return {
                "client_slug": self.client_slug,
                "client_id": self.client_id,
                "facts": await self.get_facts(),
                "recent_episodes": await self.recent_episodes(limit=15),
                "preferences": await self.get_preferences(min_confidence=0.5),
                "open_loops": await self.open_loops(),
                "objectives": await self.active_objectives(),
            }
        except ClientOSError:
            raise
        except Exception as e:
            logger.error("client_os.snapshot erro %s: %s", self.client_slug, e)
            raise ClientOSError("Erro ao compilar snapshot") from e

    async def narrative(self, *, max_episodes: int = 10) -> str:
        """
        Compila o estado em texto narrativo pra injeção em prompts.

        Delegado ao narrative.py pra manter separação entre acesso a dados e
        formatação. Retorna sempre uma string (mesmo que vazia/curta).
        """
        # Import local pra evitar acoplamento em tempo de import
        from .narrative import compile_narrative

        try:
            snap = await self.snapshot()
            return compile_narrative(snap, max_episodes=max_episodes)
        except ClientOSError:
            raise
        except Exception as e:
            logger.error("client_os.narrative erro %s: %s", self.client_slug, e)
            raise ClientOSError("Erro ao compilar narrativa") from e

    # ---------- PRIVATE ----------

    async def _bump_version(self) -> None:
        """
        Incrementa version do client_state e atualiza updated_at.
        Usa INSERT ... ON CONFLICT DO UPDATE pra evitar SELECT prévio.
        Falhas aqui são logadas mas não interrompem a operação principal —
        version é metadado, não dado crítico.
        """
        try:
            stmt = (
                pg_insert(ClientStateRow)
                .values(client_id=self.client_id, version=1)
                .on_conflict_do_update(
                    index_elements=["client_id"],
                    set_={
                        "version": ClientStateRow.__table__.c.version + 1,
                        "updated_at": func.now(),
                    },
                )
            )
            await self.db.execute(stmt)
        except Exception as e:
            logger.warning(
                "client_os._bump_version %s erro (não-crítico): %s",
                self.client_slug, e,
            )


__all__ = ["ClientOS"]
