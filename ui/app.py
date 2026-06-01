"""
ui/app.py
──────────
Point d'entrée Streamlit de CityMatch.

Interface centrée sur la recommandation :
- chat utilisateur ;
- résultats personnalisés ;
- carte / graphiques / rapport.

L'onglet Explorer a été supprimé pour simplifier l'expérience utilisateur.
"""

import sys
from pathlib import Path

import streamlit as st

# Permet les imports depuis la racine du projet quand Streamlit lance ui/app.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import APP_NAME
from ui.components.chat import render_chat_panel
from ui.components.results import render_results_panel
from ui.components.sidebar import render_sidebar
from ui.services.session import init_session


st.set_page_config(
    page_title=f"{APP_NAME} — Trouvez votre ville idéale",
    page_icon="🏙️",
    layout="wide",
    initial_sidebar_state="expanded",
)


CSS = """
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
</style>
"""


def render_header() -> None:
    """Affiche le titre principal de l'application."""
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown('<div class="main-title">🏙️ CityMatch</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="subtitle">Votre assistant IA pour trouver la ville idéale en France</div>',
        unsafe_allow_html=True,
    )


def main() -> None:
    """Lance l'interface Streamlit."""
    init_session()
    render_sidebar()
    render_header()

    chat_col, results_col = st.columns([1, 1], gap="large")

    with chat_col:
        render_chat_panel()

    with results_col:
        render_results_panel()


if __name__ == "__main__":
    main()
