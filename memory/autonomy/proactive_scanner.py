"""
Villa — Proactive Scanner (Autonomy Engine, Loop 1)

"O que o Villa deveria estar fazendo agora?"

Varre clientes ativos a cada ciclo, avalia um conjunto de condicoes
declarativas e, quando uma condicao dispara, gera uma acao (evento interno)
que e roteada via orchestrator.handle_event().

Design:
- Condicoes sao funcoes puras (estado -> bool + contexto). Faceis de testar,
  auditar e estender. Nenhuma decisao e "magica".
- O scanner NAO executa modulos diretamente — apenas dispara eventos. Mantem
  o orquestrador como unico ponto de execucao (separacao de responsabilidades).
- Totalmente defensivo: falha em um cliente nao interrompe a varredura dos
  demais; falha global nao derruba o scheduler.

As acoes disparadas alimentam o ReflectionLoop (Loop 2), que aprende com elas.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ── Estruturas de dados ──

@dataclass
class ScanAction:
    """Uma acao que o scanner decidiu disparar para um cliente."""

    client_slug: str
    event_type: str
    reason: str
    severity: str  # "info" | "opportunity" | "warning" | "critical"
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScanResult:
    """Resultado agregado de um ciclo de varredura."""

    clients_scanned: int = 0
    actions: list[ScanAction] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def actions_count(self) -> int:
        return len(self.actions)


# Assinatura de uma condicao: recebe o "client_view" (dict com estado do
# cliente) e retorna None (nao dispara) ou um ScanAction (dispara).
ConditionFn = Callable[[dict[str, Any]], "ScanAction | None"]


# ── Condicoes declarativas ──
# Cada condicao e uma funcao pura sobre o client_view. Adicionar uma nova
# autonomia = adicionar uma funcao aqui e registra-la em DEFAULT_CONDITIONS.

def _cond_cpl_spike(view: dict[str, Any]) -> ScanAction | None:
    """CPL subiu significativamente vs janela anterior → analisar campanhas."""
    cpl_now = view.get("cpl_current")
    cpl_prev = view.get("cpl_previous")
    if cpl_now is None or cpl_prev is None or cpl_prev <= 0:
        return None
    delta = (cpl_now - cpl_prev) / cpl_prev
    if delta >= 0.30:  # +30% ou mais
        return ScanAction(
            client_slug=view["slug"],
            event_type="autonomy_cpl_spike",
            reason=f"CPL subiu {delta:.0%} (de R${cpl_prev:.2f} para R${cpl_now:.2f})",
            severity="warning" if delta < 0.5 else "critical",
            payload={"cpl_current": cpl_now, "cpl_previous": cpl_prev, "delta": delta},
        )
    return None


def _cond_frequency_high(view: dict[str, Any]) -> ScanAction | None:
    """Frequencia alta → audience fatigue, renovar criativos."""
    freq = view.get("frequency")
    threshold = view.get("frequency_max", 3.0)
    if freq is None:
        return None
    if freq >= threshold:
        return ScanAction(
            client_slug=view["slug"],
            event_type="autonomy_frequency_high",
            reason=f"Frequencia {freq:.1f} >= limite {threshold:.1f} (audience fatigue)",
            severity="warning",
            payload={"frequency": freq, "threshold": threshold},
        )
    return None


def _cond_stale_creatives(view: dict[str, Any]) -> ScanAction | None:
    """Sem roteiro novo ha muitos dias → sugerir hipoteses."""
    days = view.get("days_since_last_roteiro")
    if days is None:
        return None
    if days >= 14:
        return ScanAction(
            client_slug=view["slug"],
            event_type="autonomy_stale_creatives",
            reason=f"Sem roteiro novo ha {days} dias",
            severity="opportunity",
            payload={"days_since_last_roteiro": days},
        )
    return None


def _cond_low_health_score(view: dict[str, Any]) -> ScanAction | None:
    """Health score baixo nas campanhas → analise aprofundada."""
    score = view.get("health_score")
    if score is None:
        return None
    if score < 50:
        return ScanAction(
            client_slug=view["slug"],
            event_type="autonomy_low_health",
            reason=f"Health score {score}/100 abaixo do aceitavel",
            severity="warning" if score >= 30 else "critical",
            payload={"health_score": score},
        )
    return None


DEFAULT_CONDITIONS: list[ConditionFn] = [
    _cond_cpl_spike,
    _cond_frequency_high,
    _cond_stale_creatives,
    _cond_low_health_score,
]


class ProactiveScanner:
    """
    Loop 1 do Autonomy Engine.

    Uso:
        scanner = ProactiveScanner()
        result = await scanner.scan_all(db, orchestrator)
    """

    def __init__(self, conditions: list[ConditionFn] | None = None):
        self.conditions = conditions if conditions is not None else DEFAULT_CONDITIONS

    async def scan_all(self, db: AsyncSession, orchestrator: Any | None = None) -> ScanResult:
        """
        Varre todos os clientes ativos, avalia condicoes e dispara acoes.

        Args:
            db: sessao do banco
            orchestrator: instancia do orquestrador para disparar eventos.
                          Se None, as acoes sao apenas retornadas (dry-run).

        Returns:
            ScanResult com acoes disparadas e erros por cliente.
        """
        result = ScanResult()

        try:
            views = await self._build_client_views(db)
        except Exception as e:
            logger.error("ProactiveScanner: falha ao montar client views: %s", e)
            result.errors.append(f"build_views: {e}")
            return result

        for view in views:
            result.clients_scanned += 1
            slug = view.get("slug", "?")
            try:
                for condition in self.conditions:
                    action = condition(view)
                    if action is not None:
                        result.actions.append(action)
                        if orchestrator is not None:
                            await self._dispatch(action, db, orchestrator)
            except Exception as e:
                logger.warning("ProactiveScanner: erro avaliando cliente '%s': %s", slug, e)
                result.errors.append(f"{slug}: {e}")

        logger.info(
            "ProactiveScanner: %d clientes, %d acoes, %d erros",
            result.clients_scanned,
            result.actions_count,
            len(result.errors),
        )
        return result

    async def _dispatch(
        self,
        action: ScanAction,
        db: AsyncSession,
        orchestrator: Any,
    ) -> None:
        """Dispara a acao via orchestrator.handle_event (defensivo)."""
        try:
            await orchestrator.handle_event(
                event_type=action.event_type,
                payload={
                    "client_slug": action.client_slug,
                    "reason": action.reason,
                    "severity": action.severity,
                    "source": "proactive_scanner",
                    **action.payload,
                },
                db=db,
            )
        except Exception as e:
            logger.warning(
                "ProactiveScanner: falha ao disparar '%s' para '%s': %s",
                action.event_type,
                action.client_slug,
                e,
            )

    async def _build_client_views(self, db: AsyncSession) -> list[dict[str, Any]]:
        """
        Monta uma "view" de estado por cliente ativo. Cada view e um dict
        plano consumido pelas condicoes.

        Defensivo: campos ausentes ficam como None, e as condicoes ja tratam
        None como "nao dispara". Isso evita acoplamento rigido ao schema.
        """
        from core.models import Client

        views: list[dict[str, Any]] = []

        result = await db.execute(select(Client))
        clients = result.scalars().all()

        for client in clients:
            # Apenas clientes ativos, se o modelo tiver o campo
            is_active = getattr(client, "is_active", True)
            if not is_active:
                continue
            # Pular o Client OS institucional — ele nao e cliente de verdade
            if getattr(client, "slug", None) == "webxp_agency":
                continue

            config = getattr(client, "config", None) or {}
            thresholds = config.get("thresholds", {}) if isinstance(config, dict) else {}

            view: dict[str, Any] = {
                "slug": client.slug,
                "name": getattr(client, "name", client.slug),
                "frequency_max": thresholds.get("frequency_max", 3.0),
                # Os campos abaixo sao preenchidos por coletores opcionais.
                # Ausentes => None => condicao nao dispara.
                "cpl_current": None,
                "cpl_previous": None,
                "frequency": None,
                "health_score": None,
                "days_since_last_roteiro": None,
            }

            # Enriquecer com dados disponiveis sem acoplar a coletores externos
            await self._enrich_view_from_db(view, client, db)
            views.append(view)

        return views

    async def _enrich_view_from_db(
        self,
        view: dict[str, Any],
        client: Any,
        db: AsyncSession,
    ) -> None:
        """
        Enriquece a view com dados ja disponiveis no banco (defensivo).
        Cada bloco e isolado: falha em um nao impede os demais.
        """
        # Health score e frequencia das campanhas ativas
        try:
            from core.models import Campaign

            result = await db.execute(
                select(Campaign)
                .where(Campaign.client_id == client.id)
                .where(Campaign.status == "active")
            )
            campaigns = result.scalars().all()
            if campaigns:
                scores = [c.health_score for c in campaigns if getattr(c, "health_score", None) is not None]
                if scores:
                    view["health_score"] = min(scores)  # pior caso entre campanhas
        except Exception as e:
            logger.debug("enrich health_score falhou para %s: %s", view["slug"], e)

        # Dias desde o ultimo roteiro
        try:
            from core.models import Roteiro

            result = await db.execute(
                select(Roteiro.created_at)
                .where(Roteiro.client_id == client.id)
                .order_by(Roteiro.created_at.desc())
                .limit(1)
            )
            last = result.scalar_one_or_none()
            if last is not None:
                now = datetime.now(timezone.utc)
                # normalizar naive -> utc
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                view["days_since_last_roteiro"] = (now - last).days
        except Exception as e:
            logger.debug("enrich days_since_last_roteiro falhou para %s: %s", view["slug"], e)


# ── Instancia global ──
proactive_scanner = ProactiveScanner()


__all__ = [
    "ProactiveScanner",
    "ScanAction",
    "ScanResult",
    "proactive_scanner",
    "DEFAULT_CONDITIONS",
]
