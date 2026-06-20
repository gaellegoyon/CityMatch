"""
ui/components/chat.py
─────────────────────
Panneau conversationnel CityMatch.

Le bouton "Affiner" a été supprimé volontairement :
l'utilisateur peut simplement préciser sa demande dans le chat
("finalement je veux moins cher", "plus proche de la mer", etc.).
"""

from __future__ import annotations

import html
import logging
import uuid
from typing import Any, Final

import streamlit as st

from ui.services.session import update_session_from_agent_result


logger = logging.getLogger(__name__)

CHAT_CONTAINER_HEIGHT: Final[int] = 450

DEFAULT_ASSISTANT_ERROR_MESSAGE: Final[str] = (
    "Désolé, une erreur est survenue pendant l'analyse. "
    "Vous pouvez reformuler votre demande ou réessayer."
)


def render_chat_panel() -> None:
    """Affiche le chat et gère l'envoi des messages."""
    _ensure_chat_state()

    st.markdown("### 💬 Assistant CityMatch")
    render_chat_history()
    handle_chat_form()


def _ensure_chat_state() -> None:
    """Initialise les clés Streamlit nécessaires au panneau de chat."""
    st.session_state.setdefault("chat_history", [])
    st.session_state.setdefault("started", False)

    if "session_id" not in st.session_state or not st.session_state.session_id:
        st.session_state.session_id = str(uuid.uuid4())


def _city_count_text() -> str:
    """Retourne un texte lisible pour le nombre de villes analysées."""
    nb_villes = st.session_state.get("nb_villes", 0)

    try:
        count = int(nb_villes)
    except (TypeError, ValueError):
        count = 0

    if count > 0:
        return f"**{count:,} villes françaises**".replace(",", " ")

    return "**les villes françaises disponibles**"


def _safe_message_html(content: Any) -> str:
    """Échappe un message utilisateur/assistant et conserve les retours ligne."""
    safe_content = html.escape(str(content or ""))
    return safe_content.replace("\n", "<br>")


def _normalize_role(role: Any) -> str:
    """Normalise le rôle d'un message pour l'affichage."""
    normalized = str(role or "assistant").strip().lower()

    if normalized in {"user", "assistant"}:
        return normalized

    return "assistant"


def render_chat_history() -> None:
    """Affiche l'historique conversationnel."""
    chat_container = st.container(height=CHAT_CONTAINER_HEIGHT)

    with chat_container:
        if not st.session_state.chat_history:
            st.info(
                "👋 Bonjour ! Je suis CityMatch, votre assistant pour trouver la ville idéale.\n\n"
                "Dites-moi ce qui est important pour vous : famille, travail, budget, "
                "mer, montagne, climat, sécurité, fibre... "
                f"Je vais analyser {_city_count_text()} selon vos critères."
            )
            return

        for message in st.session_state.chat_history:
            role = _normalize_role(message.get("role", "assistant"))
            content = message.get("content", "")

            css_class = "user-msg" if role == "user" else "ai-msg"
            icon = "👤" if role == "user" else "🤖"
            safe_content = _safe_message_html(content)

            st.markdown(
                f'<div class="chat-message {css_class}">'
                f"<b>{icon}</b> {safe_content}"
                f"</div>",
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

        send_button = st.form_submit_button(
            "📨 Envoyer",
            use_container_width=True,
            type="primary",
        )

    if not send_button:
        return

    cleaned_input = user_input.strip()

    if not cleaned_input:
        st.warning("Veuillez saisir un message avant d'envoyer.")
        return

    submit_user_message(cleaned_input)


def _append_chat_message(role: str, content: str) -> None:
    """Ajoute un message à l'historique local Streamlit."""
    st.session_state.chat_history.append(
        {
            "role": role,
            "content": content,
        }
    )


def _get_orchestrator() -> Any:
    """Retourne l'orchestrateur stocké en session Streamlit."""
    orchestrator = st.session_state.get("orchestrator")

    if orchestrator is None:
        raise RuntimeError("Orchestrateur CityMatch introuvable dans st.session_state.")

    return orchestrator


def _call_orchestrator(user_input: str) -> dict[str, Any]:
    """Appelle l'orchestrateur selon l'état courant de la session."""
    orchestrator = _get_orchestrator()
    session_id = st.session_state.session_id

    if not st.session_state.started:
        result = orchestrator.start_session(
            user_message=user_input,
            session_id=session_id,
        )
        st.session_state.started = True
        return result or {}

    result = orchestrator.send_message(
        user_message=user_input,
        session_id=session_id,
    )

    return result or {}


def _apply_agent_result(result: dict[str, Any]) -> None:
    """
    Met à jour la session Streamlit avec le résultat agent.

    Si le service de session n'ajoute pas lui-même la réponse dans l'historique,
    on ajoute une réponse assistant de secours.
    """
    history_before = len(st.session_state.chat_history)

    update_session_from_agent_result(result)

    response = str(result.get("response") or "").strip()

    if response and len(st.session_state.chat_history) == history_before:
        _append_chat_message("assistant", response)


def submit_user_message(user_input: str) -> None:
    """Envoie un message à l'orchestrateur."""
    _ensure_chat_state()
    _append_chat_message("user", user_input)

    try:
        with st.spinner("🧠 Les agents analysent votre demande..."):
            result = _call_orchestrator(user_input)
            _apply_agent_result(result)

    except Exception as exc:
        logger.exception("Erreur pendant l'envoi du message CityMatch")
        _append_chat_message("assistant", DEFAULT_ASSISTANT_ERROR_MESSAGE)
        st.error(f"Erreur CityMatch : {exc}")

    st.rerun()