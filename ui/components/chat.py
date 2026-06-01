"""
ui/components/chat.py
─────────────────────
Panneau conversationnel CityMatch.

Le bouton "Affiner" a été supprimé volontairement :
l'utilisateur peut simplement préciser sa demande dans le chat
("finalement je veux moins cher", "plus proche de la mer", etc.).
"""

from __future__ import annotations

import streamlit as st

from ui.services.session import update_session_from_agent_result


def render_chat_panel() -> None:
    """Affiche le chat et gère l'envoi des messages."""
    st.markdown("### 💬 Assistant CityMatch")
    render_chat_history()
    handle_chat_form()


def render_chat_history() -> None:
    """Affiche l'historique conversationnel."""
    chat_container = st.container(height=450)

    with chat_container:
        if not st.session_state.chat_history:
            st.info(
                "👋 Bonjour ! Je suis CityMatch, votre assistant pour trouver la ville idéale.\n\n"
                "Dites-moi ce qui est important pour vous : famille, travail, budget, "
                "mer, montagne, climat, sécurité, fibre... "
                f"Je vais analyser **{st.session_state.get('nb_villes', 0)} villes françaises** "
                "selon vos critères."
            )

        for msg in st.session_state.chat_history:
            role = msg.get("role", "assistant")
            content = msg.get("content", "")
            css_class = "user-msg" if role == "user" else "ai-msg"
            icon = "👤" if role == "user" else "🤖"

            st.markdown(
                f'<div class="chat-message {css_class}"><b>{icon}</b> {content}</div>',
                unsafe_allow_html=True,
            )


def handle_chat_form() -> None:
    """Gère le formulaire d'envoi utilisateur."""
    with st.form("chat_form", clear_on_submit=True):
        user_input = st.text_area(
            "Votre message",
            placeholder=(
                "Ex : Couple sans enfants, ville proche de la mer, air correct, "
                "sécurisée, budget 250 000€..."
            ),
            height=80,
            label_visibility="collapsed",
        )

        send_btn = st.form_submit_button(
            "📨 Envoyer",
            use_container_width=True,
            type="primary",
        )

    if send_btn and user_input.strip():
        submit_user_message(user_input.strip())


def submit_user_message(user_input: str) -> None:
    """Envoie un message à l'orchestrateur."""
    st.session_state.chat_history.append({"role": "user", "content": user_input})

    with st.spinner("🧠 Les agents analysent votre demande..."):
        orchestrator = st.session_state.orchestrator

        if not st.session_state.started:
            result = orchestrator.start_session(
                user_message=user_input,
                session_id=st.session_state.session_id,
            )
            st.session_state.started = True
        else:
            result = orchestrator.send_message(
                user_message=user_input,
                session_id=st.session_state.session_id,
            )

        update_session_from_agent_result(result)

    st.rerun()
