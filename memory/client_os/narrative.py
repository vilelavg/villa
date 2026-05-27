"""
Compilador narrativo do Client OS.

Recebe o snapshot estruturado retornado por `ClientOS.snapshot()` e devolve
texto markdown pronto pra ser injetado em prompts do Claude antes de qualquer
ação. Mantém esse texto curto, hierárquico e fácil de consumir pelo LLM.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _humanize_when(dt: datetime | None) -> str:
    """Formata datetime relativo ao agora: 'há 2h', 'há 5 dias', 'em 3 dias'."""
    if dt is None:
        return "data desconhecida"

    now = datetime.now(timezone.utc)
    # Normaliza naive datetimes pra UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    delta_seconds = (now - dt).total_seconds()

    if delta_seconds < 0:
        # Data no futuro
        future = abs(delta_seconds)
        if future < 3600:
            return f"em {int(future / 60)}min"
        if future < 86400:
            return f"em {int(future / 3600)}h"
        return f"em {int(future / 86400)} dias"

    # Data no passado
    if delta_seconds < 60:
        return "agora há pouco"
    if delta_seconds < 3600:
        return f"há {int(delta_seconds / 60)}min"
    if delta_seconds < 86400:
        return f"há {int(delta_seconds / 3600)}h"

    days = int(delta_seconds / 86400)
    if days < 7:
        return f"há {days} dias"
    if days < 60:
        weeks = days // 7
        return f"há {weeks} semana{'s' if weeks > 1 else ''}"
    months = days // 30
    return f"há {months} {'mês' if months == 1 else 'meses'}"


def _format_fact_value(value: Any) -> str:
    """Renderiza o value de um fato (JSONB) como string legível."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "sim" if value else "não"
    if isinstance(value, dict):
        if not value:
            return "—"
        return ", ".join(f"{k}={v}" for k, v in value.items())
    if isinstance(value, list):
        if not value:
            return "—"
        return ", ".join(str(x) for x in value)
    return str(value)


def _outcome_marker(outcome: str | None) -> str:
    """Marcador visual curto para o outcome de um episódio."""
    if outcome == "positive":
        return " ✓"
    if outcome == "negative":
        return " ✗"
    if outcome == "pending":
        return " ⋯"
    return ""


def compile_narrative(snapshot: dict, *, max_episodes: int = 10) -> str:
    """
    Recebe `ClientOS.snapshot()` e devolve texto narrativo pronto pra prompt.

    Args:
        snapshot: dict retornado por ClientOS.snapshot()
        max_episodes: limite de episódios renderizados (defaults a 10)

    Returns:
        Texto markdown. Sempre inclui pelo menos o header com o slug;
        se não houver estado, sinaliza explicitamente.
    """
    parts: list[str] = []
    slug = snapshot.get("client_slug", "?")
    parts.append(f"## Estado atual do cliente — {slug}")

    facts = snapshot.get("facts") or []
    preferences = snapshot.get("preferences") or []
    objectives = snapshot.get("objectives") or []
    loops = snapshot.get("open_loops") or []
    episodes = snapshot.get("recent_episodes") or []

    # 1. Fatos (agrupados por categoria)
    if facts:
        parts.append("\n### Fatos estabelecidos")
        by_cat: dict[str, list[dict]] = {}
        for f in facts:
            by_cat.setdefault(f["category"], []).append(f)
        for cat in sorted(by_cat.keys()):
            parts.append(f"\n**{cat}:**")
            for f in by_cat[cat]:
                conf_marker = (
                    ""
                    if f.get("confidence", 1.0) >= 0.9
                    else f" (confiança {int(f['confidence'] * 100)}%)"
                )
                parts.append(
                    f"- {f['key']}: {_format_fact_value(f.get('value'))}{conf_marker}"
                )

    # 2. Preferências observadas
    if preferences:
        parts.append("\n### Preferências e padrões observados")
        for p in preferences:
            ev = p.get("evidence_count", 1)
            ev_marker = "" if ev <= 1 else f" (observado {ev}x)"
            parts.append(f"- [{p['topic']}] {p['pattern']}{ev_marker}")

    # 3. Objetivos ativos
    if objectives:
        parts.append("\n### Objetivos ativos")
        for o in objectives:
            target = ""
            if o.get("target_metric") and o.get("target_value") is not None:
                target = f" → {o['target_metric']} = {o['target_value']}"
            deadline_str = ""
            if o.get("deadline"):
                deadline_str = f" (prazo: {_humanize_when(o['deadline'])})"
            parts.append(f"- {o['title']}{target}{deadline_str}")

    # 4. Pendências abertas
    if loops:
        parts.append("\n### Pendências abertas")
        for loop in loops[:10]:
            owner = loop.get("owner", "villa")
            due = (
                f" — vencimento {_humanize_when(loop['due_at'])}"
                if loop.get("due_at")
                else ""
            )
            parts.append(f"- [{owner}] {loop['title']}{due}")

    # 5. Histórico recente
    if episodes:
        parts.append("\n### Histórico recente")
        for e in episodes[:max_episodes]:
            when = _humanize_when(e.get("occurred_at"))
            outcome = _outcome_marker(e.get("outcome"))
            src = f" [{e.get('module_source')}]" if e.get("module_source") else ""
            parts.append(f"- {when}{src}: {e['summary']}{outcome}")

    # Se snapshot estiver totalmente vazio, sinaliza explicitamente
    has_content = any([facts, preferences, objectives, loops, episodes])
    if not has_content:
        parts.append("\n*Nenhum estado conhecido ainda para esse cliente.*")

    return "\n".join(parts)


__all__ = ["compile_narrative"]
