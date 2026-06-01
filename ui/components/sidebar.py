"""
ui/components/sidebar.py
────────────────────────
Sidebar Streamlit : session, statistiques DB, critères disponibles, trace agents.
"""

from __future__ import annotations

import streamlit as st

from agents.database_agent import get_stats_summary
from config.settings import APP_VERSION, AVAILABLE_CRITERIA
from ui.services.session import reset_session


def render_sidebar() -> None:
    """Affiche la barre latérale."""
    with st.sidebar:
        st.markdown("## 🏙️ CityMatch")
        st.caption(f"v{APP_VERSION} — Master SICC")
        st.divider()

        st.markdown("### 📌 Session")
        st.code(st.session_state.session_id[:12] + "...", language=None)

        if st.button("🔄 Nouvelle session", use_container_width=True):
            reset_session()

        st.divider()
        render_database_stats()

        st.divider()
        render_available_criteria()

        st.divider()
        render_agent_trace()


def render_database_stats() -> None:
    """Affiche les statistiques basiques de la base."""
    st.markdown("### 📊 Base de données")
    try:
        stats = get_stats_summary()
        st.metric("Villes analysées", stats["total_cities"])
        st.metric("Régions couvertes", stats["total_regions"])
    except Exception:
        st.caption("Base de données non initialisée")
        st.caption("→ Lancer : `python data/ingest_real_data.py`")


def render_available_criteria() -> None:
    """Affiche la liste des critères actuellement exploitables."""
    with st.expander("📋 Critères disponibles"):
        for _, meta in AVAILABLE_CRITERIA.items():
            st.markdown(f"**{meta['label']}** ({meta['unit']})")
            st.caption(meta["description"])


def render_agent_trace() -> None:
    """Affiche la trace des agents si elle existe."""
    trace = st.session_state.get("agent_trace") or []
    if not trace:
        return

    with st.expander("🔧 Trace des agents", expanded=False):
        for item in trace:
            st.caption(f"• {item}")
