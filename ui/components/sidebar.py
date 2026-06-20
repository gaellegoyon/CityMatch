"""
ui/components/sidebar.py
────────────────────────
Sidebar Streamlit : session, statistiques DB, critères disponibles, trace agents.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Final

import streamlit as st

from agents.database.repository import get_stats_summary
from config.settings import APP_VERSION, AVAILABLE_CRITERIA
from ui.services.session import reset_session


logger = logging.getLogger(__name__)

SESSION_ID_PREVIEW_LENGTH: Final[int] = 12
MAX_TRACE_ITEMS: Final[int] = 30


def render_sidebar() -> None:
    """Affiche la barre latérale."""
    _ensure_sidebar_state()

    with st.sidebar:
        st.markdown("## 🏙️ CityMatch")
        st.caption(f"v{APP_VERSION} — Master SICC")
        st.divider()

        render_session_block()

        st.divider()
        render_database_stats()

        st.divider()
        render_available_criteria()

        st.divider()
        render_agent_trace()


def _ensure_sidebar_state() -> None:
    """Initialise les clés Streamlit nécessaires à la sidebar."""
    if "session_id" not in st.session_state or not st.session_state.session_id:
        st.session_state.session_id = str(uuid.uuid4())

    st.session_state.setdefault("agent_trace", [])


def render_session_block() -> None:
    """Affiche les informations de session."""
    st.markdown("### 📌 Session")

    session_id = str(st.session_state.get("session_id", ""))
    preview = _short_session_id(session_id)

    st.code(preview, language=None)

    if st.button("🔄 Nouvelle session", use_container_width=True):
        reset_session()
        st.rerun()


def _short_session_id(session_id: str) -> str:
    """Retourne un identifiant de session raccourci."""
    if not session_id:
        return "session inconnue"

    if len(session_id) <= SESSION_ID_PREVIEW_LENGTH:
        return session_id

    return f"{session_id[:SESSION_ID_PREVIEW_LENGTH]}..."


def render_database_stats() -> None:
    """Affiche les statistiques basiques de la base."""
    st.markdown("### 📊 Base de données")

    try:
        stats = get_stats_summary()

        total_cities = int(stats.get("total_cities", 0) or 0)
        total_regions = int(stats.get("total_regions", 0) or 0)

        st.metric("Villes analysées", f"{total_cities:,}".replace(",", " "))
        st.metric("Régions couvertes", total_regions)

    except Exception as exc:
        logger.exception("Impossible de charger les statistiques de la base")
        st.caption("Base de données non initialisée")
        st.caption("→ Lancer : `python data/ingest_real_data.py`")

        with st.expander("Détail technique", expanded=False):
            st.code(str(exc), language="text")


def render_available_criteria() -> None:
    """Affiche la liste des critères actuellement exploitables."""
    with st.expander("📋 Critères disponibles"):
        if not AVAILABLE_CRITERIA:
            st.caption("Aucun critère configuré.")
            return

        for criterion_key, meta in AVAILABLE_CRITERIA.items():
            label = _criterion_meta(meta, "label", criterion_key)
            unit = _criterion_meta(meta, "unit", "")
            description = _criterion_meta(meta, "description", "Critère exploitable par le scoring.")

            if unit:
                st.markdown(f"**{label}** ({unit})")
            else:
                st.markdown(f"**{label}**")

            st.caption(description)


def _criterion_meta(meta: Any, key: str, default: str) -> str:
    """Lit une métadonnée de critère sans supposer son format."""
    if isinstance(meta, dict):
        value = meta.get(key, default)
        return str(value) if value is not None else default

    return default


def render_agent_trace() -> None:
    """Affiche la trace des agents si elle existe."""
    trace = st.session_state.get("agent_trace") or []

    if not trace:
        return

    with st.expander("🔧 Trace des agents", expanded=False):
        visible_trace = trace[-MAX_TRACE_ITEMS:]

        if len(trace) > MAX_TRACE_ITEMS:
            st.caption(f"Affichage des {MAX_TRACE_ITEMS} dernières entrées sur {len(trace)}.")

        for item in visible_trace:
            st.caption(f"• {item}")