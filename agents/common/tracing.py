"""
agents/common/tracing.py
────────────────────────
Helpers de traçabilité pour l'état partagé LangGraph.
"""

from __future__ import annotations

from typing import Any, Mapping


MAX_TRACE_ENTRIES = 200
MAX_TRACE_MESSAGE_LENGTH = 500


def trace_update(state: Mapping[str, Any], message: str) -> dict[str, list[str]]:
    """Retourne une mise à jour agent_trace sans modifier l'état en place."""
    cleaned_message = str(message).strip()

    if not cleaned_message:
        return {"agent_trace": list(state.get("agent_trace") or [])}

    if len(cleaned_message) > MAX_TRACE_MESSAGE_LENGTH:
        cleaned_message = f"{cleaned_message[:MAX_TRACE_MESSAGE_LENGTH]}..."

    trace = list(state.get("agent_trace") or [])
    trace.append(cleaned_message)

    return {"agent_trace": trace[-MAX_TRACE_ENTRIES:]}