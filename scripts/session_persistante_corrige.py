"""
ui/services/session.py
──────────────────────
Gestion de l'état Streamlit CityMatch.

Important :
Streamlit perd naturellement `st.session_state` lors d'un refresh navigateur.
Pour conserver la session utilisateur, on stocke l'identifiant de session dans
l'URL (`?session_id=...`) puis on restaure l'historique et les résultats depuis
SQLite au rechargement.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import streamlit as st

from graph.orchestrator import CityMatchOrchestrator


def init_session() -> None:
    """
    Initialise toutes les clés utilisées par l'interface.

    Si l'URL contient déjà `?session_id=...`, on restaure la session depuis la DB.
    Sinon, on crée une nouvelle session et on écrit son ID dans l'URL.
    """
    restored_from_db = False

    if "session_id" not in st.session_state:
        session_id = _get_session_id_from_url() or str(uuid.uuid4())
        st.session_state.session_id = session_id
        _set_session_id_in_url(session_id)
        restored_from_db = restore_session_from_db(session_id)

    if "orchestrator" not in st.session_state:
        with st.spinner("Initialisation du système CityMatch..."):
            st.session_state.orchestrator = CityMatchOrchestrator(use_memory=True)

    _ensure_defaults()

    if not restored_from_db and not st.session_state.get("_restore_checked"):
        restore_session_from_db(st.session_state.session_id)

    st.session_state["_restore_checked"] = True
    refresh_city_count()


def _ensure_defaults() -> None:
    """Crée les clés Streamlit manquantes sans écraser une session restaurée."""
    defaults = {
        "chat_history": [],
        "top_cities": [],
        "analysis_complete": False,
        "report_path": "",
        "report_markdown": "",
        "iteration": 1,
        "started": False,
        "agent_trace": [],
        "nb_villes": 0,
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _get_session_id_from_url() -> str | None:
    """Lit session_id depuis les query params Streamlit."""
    try:
        value = st.query_params.get("session_id")
    except Exception:
        return None

    if isinstance(value, list):
        value = value[0] if value else None

    if not value:
        return None

    value = str(value).strip()
    return value or None


def _set_session_id_in_url(session_id: str) -> None:
    """Écrit session_id dans l'URL pour survivre aux refresh navigateur."""
    try:
        if st.query_params.get("session_id") != session_id:
            st.query_params["session_id"] = session_id
    except Exception:
        # Non bloquant : l'app peut fonctionner sans query params,
        # mais la session ne survivra pas toujours au refresh.
        pass


def restore_session_from_db(session_id: str) -> bool:
    """
    Restaure l'état UI depuis SQLite.

    Restaure :
    - chat_history ;
    - top_cities depuis city_scores + cities ;
    - report_path / report_markdown ;
    - analysis_complete ;
    - iteration.
    """
    try:
        from db.models import City, CityScore, SearchSession, SessionLocal

        db = SessionLocal()
        try:
            session = db.query(SearchSession).filter_by(id=session_id).first()
            if not session:
                return False

            st.session_state.chat_history = _conversation_to_chat_history(
                session.conversation_history or []
            )
            st.session_state.iteration = session.iteration or 1
            st.session_state.started = True
            st.session_state.analysis_complete = session.state == "completed"

            top_cities = (
                db.query(CityScore, City)
                .join(City, CityScore.city_id == City.id)
                .filter(CityScore.session_id == session_id)
                .order_by(CityScore.rank.asc())
                .all()
            )
            st.session_state.top_cities = [
                _score_city_to_dict(score, city)
                for score, city in top_cities
                if score and city
            ]

            if session.report_path:
                st.session_state.report_path = session.report_path
                st.session_state.report_markdown = _load_report_markdown(session.report_path)

            return True
        finally:
            db.close()

    except Exception as exc:
        st.session_state.agent_trace = list(st.session_state.get("agent_trace", []))
        st.session_state.agent_trace.append(f"Session restore error: {exc}")
        return False


def _conversation_to_chat_history(conversation: list[dict]) -> list[dict]:
    """Convertit l'historique DB LangChain en historique UI."""
    chat_history = []

    for message in conversation:
        role = message.get("role", "")
        content = message.get("content", "")

        if not content:
            continue

        if role in {"human", "user"}:
            chat_history.append({"role": "user", "content": content})
        elif role in {"ai", "assistant"}:
            chat_history.append({"role": "assistant", "content": content})

    return chat_history


def _score_city_to_dict(score: Any, city: Any) -> dict:
    """Reconstruit une ville recommandée à partir de CityScore + City."""
    data = city.to_dict() if hasattr(city, "to_dict") else {}

    data.update(
        {
            "total_score": score.total_score,
            "rank": score.rank,
            "score_details": score.score_details or {},
        }
    )

    return data


def _load_report_markdown(report_path: str) -> str:
    """
    Recharge le Markdown du rapport.

    Si le chemin stocké pointe vers le PDF, on cherche le .md du même nom.
    """
    path = Path(report_path)

    candidates = []
    if path.suffix == ".md":
        candidates.append(path)
    else:
        candidates.append(path.with_suffix(".md"))

    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
        except Exception:
            pass

    return ""


def refresh_city_count() -> None:
    """Met à jour le nombre de villes présentes en base."""
    try:
        from db.models import City, SessionLocal

        db = SessionLocal()
        try:
            st.session_state.nb_villes = db.query(City).count()
        finally:
            db.close()
    except Exception:
        st.session_state.nb_villes = 0


def reset_session() -> None:
    """
    Réinitialise complètement la session UI et crée une nouvelle session_id.

    Le paramètre d'URL est aussi remplacé pour éviter de restaurer l'ancienne
    session au refresh suivant.
    """
    for key in list(st.session_state.keys()):
        del st.session_state[key]

    new_session_id = str(uuid.uuid4())
    st.session_state.session_id = new_session_id
    _set_session_id_in_url(new_session_id)

    st.rerun()


def update_session_from_agent_result(result: dict) -> None:
    """Copie les champs retournés par l'orchestrateur dans st.session_state."""
    if result.get("response"):
        st.session_state.chat_history.append(
            {"role": "assistant", "content": result["response"]}
        )

    if result.get("top_cities"):
        st.session_state.top_cities = result["top_cities"]

    if result.get("analysis_complete"):
        st.session_state.analysis_complete = True

    if result.get("report_path"):
        st.session_state.report_path = result["report_path"]

    if result.get("report_markdown"):
        st.session_state.report_markdown = result["report_markdown"]

    if result.get("agent_trace"):
        st.session_state.agent_trace = result["agent_trace"]

    if result.get("iteration"):
        st.session_state.iteration = result["iteration"]

    _set_session_id_in_url(st.session_state.session_id)
