"""
ui/app.py
─────────
Point d'entrée Streamlit de CityMatch.

Interface centrée sur la recommandation :
- chat utilisateur ;
- résultats personnalisés ;
- carte / graphiques / rapport.

L'onglet Explorer a été supprimé pour simplifier l'expérience utilisateur.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Final

import streamlit as st


# Permet les imports depuis la racine du projet quand Streamlit lance ui/app.py.
PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from config.settings import APP_NAME
from ui.components.chat import render_chat_panel
from ui.components.results import render_results_panel
from ui.components.sidebar import render_sidebar
from ui.services.session import init_session


logger = logging.getLogger(__name__)


PAGE_TITLE: Final[str] = f"{APP_NAME} — Trouvez votre ville idéale"
PAGE_ICON: Final[str] = "🏙️"

CSS: Final[str] = """
<style>
    .main-title {
        font-size: 2.5rem;
        font-weight: 700;
        color: #1a365d;
        margin-bottom: 0;
    }

    .subtitle {
        font-size: 1.1rem;
        color: #4a5568;
        margin-bottom: 2rem;
    }

    .chat-message {
        padding: 1rem;
        border-radius: 10px;
        margin: 0.5rem 0;
        line-height: 1.45;
    }

    .user-msg {
        background: #ebf8ff;
        border-left: 4px solid #4299e1;
    }

    .ai-msg {
        background: #f0fff4;
        border-left: 4px solid #48bb78;
    }

    .stButton button {
        border-radius: 8px;
        font-weight: 600;
    }

    .block-container {
        padding-top: 2rem;
    }
</style>
"""


def configure_page() -> None:
    """Configure la page Streamlit."""
    st.set_page_config(
        page_title=PAGE_TITLE,
        page_icon=PAGE_ICON,
        layout="wide",
        initial_sidebar_state="expanded",
    )


def render_header() -> None:
    """Affiche le titre principal de l'application."""
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="main-title">🏙️ CityMatch</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="subtitle">'
        "Votre assistant IA pour trouver la ville idéale en France"
        "</div>",
        unsafe_allow_html=True,
    )


def render_main_layout() -> None:
    """Affiche la mise en page principale."""
    chat_col, results_col = st.columns([1, 1], gap="large")

    with chat_col:
        render_chat_panel()

    with results_col:
        render_results_panel()


def main() -> None:
    """Lance l'interface Streamlit."""
    configure_page()

    try:
        init_session()
        render_sidebar()
        render_header()
        render_main_layout()

    except Exception as exc:
        logger.exception("Erreur critique dans l'application Streamlit CityMatch")
        st.error("Une erreur critique empêche le chargement de CityMatch.")
        st.exception(exc)


if __name__ == "__main__":
    main()