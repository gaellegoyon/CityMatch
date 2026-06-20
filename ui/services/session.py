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

import logging
import re
import uuid
from pathlib import Path
from typing import Any, Final

import streamlit as st

from config.settings import REPORTS_DIR
from graph.orchestrator import CityMatchOrchestrator


logger = logging.getLogger(__name__)

SESSION_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]{1,100}$")

DEFAULT_SESSION_VALUES: Final[dict[str, Any]] = {
    "chat_history": [],
    "top_cities": [],
    "analysis_complete": False,
    "report_path": "",
    "report_pdf_path": "",
    "report_markdown": "",
    "iteration": 1,
    "started": False,
    "agent_trace": [],
    "nb_villes": 0,
}


def init_session() -> None:
    """
    Initialise toutes les clés utilisées par l'interface.

    Si l'URL contient déjà `?session_id=...`, on restaure la session depuis la DB.
    Sinon, on crée une nouvelle session et on écrit son ID dans l'URL.
    """
    if "session_id" not in st.session_state or not st.session_state.session_id:
        session_id = _get_session_id_from_url() or _new_session_id()
        st.session_state.session_id = session_id

    _set_session_id_in_url(st.session_state.session_id)
    _ensure_defaults()

    if not st.session_state.get("_restore_checked", False):
        restored = restore_session_from_db(st.session_state.session_id)
        st.session_state["_restore_checked"] = True

        if restored:
            st.session_state.started = True

    if "orchestrator" not in st.session_state:
        with st.spinner("Initialisation du système CityMatch..."):
            st.session_state.orchestrator = CityMatchOrchestrator(use_memory=True)

    refresh_city_count()


def _ensure_defaults() -> None:
    """Crée les clés Streamlit manquantes sans écraser une session restaurée."""
    for key, value in DEFAULT_SESSION_VALUES.items():
        if key in st.session_state:
            continue

        if isinstance(value, list):
            st.session_state[key] = list(value)
        elif isinstance(value, dict):
            st.session_state[key] = dict(value)
        else:
            st.session_state[key] = value


def _new_session_id() -> str:
    """Crée un nouvel identifiant de session."""
    return str(uuid.uuid4())


def _sanitize_session_id(value: str | None) -> str | None:
    """Valide un identifiant de session venant de l'URL."""
    if not value:
        return None

    cleaned = str(value).strip()

    if not cleaned:
        return None

    if SESSION_ID_PATTERN.fullmatch(cleaned):
        return cleaned

    return None


def _get_session_id_from_url() -> str | None:
    """Lit session_id depuis les query params Streamlit."""
    try:
        value = st.query_params.get("session_id")
    except Exception:
        return None

    if isinstance(value, list):
        value = value[0] if value else None

    return _sanitize_session_id(value)


def _set_session_id_in_url(session_id: str) -> None:
    """Écrit session_id dans l'URL pour survivre aux refresh navigateur."""
    session_id = _sanitize_session_id(session_id)

    if not session_id:
        return

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
    - report_path / report_pdf_path / report_markdown ;
    - analysis_complete ;
    - iteration.
    """
    session_id = _sanitize_session_id(session_id)

    if not session_id:
        return False

    try:
        from db.models import City, CityScore, SearchSession, SessionLocal

        db = SessionLocal()

        try:
            session = db.query(SearchSession).filter_by(id=session_id).first()

            if not session:
                return False

            conversation_history = session.conversation_history or []
            st.session_state.chat_history = _conversation_to_chat_history(conversation_history)
            st.session_state.iteration = int(session.iteration or 1)
            st.session_state.started = True
            st.session_state.analysis_complete = session.state == "completed"

            top_city_rows = (
                db.query(CityScore, City)
                .join(City, CityScore.city_id == City.id)
                .filter(CityScore.session_id == session_id)
                .order_by(CityScore.rank.asc(), CityScore.total_score.desc())
                .all()
            )

            st.session_state.top_cities = [
                _score_city_to_dict(score, city)
                for score, city in top_city_rows
                if score is not None and city is not None
            ]

            if session.report_path:
                report_path = str(session.report_path)
                st.session_state.report_path = report_path
                st.session_state.report_pdf_path = report_path
                st.session_state.report_markdown = _load_report_markdown(report_path)

            if st.session_state.top_cities or st.session_state.report_markdown:
                st.session_state.analysis_complete = True

            return True

        finally:
            db.close()

    except Exception as exc:
        logger.exception("Erreur de restauration de session")
        trace = list(st.session_state.get("agent_trace", []))
        trace.append(f"Session restore error: {exc}")
        st.session_state.agent_trace = trace
        return False


def _conversation_to_chat_history(conversation: Any) -> list[dict[str, str]]:
    """Convertit l'historique DB LangChain en historique UI."""
    if not isinstance(conversation, list):
        return []

    chat_history: list[dict[str, str]] = []

    for message in conversation:
        role = ""
        content = ""

        if isinstance(message, dict):
            role = str(message.get("role") or message.get("type") or "")
            content = str(message.get("content") or "")
        else:
            role = str(getattr(message, "type", "") or getattr(message, "role", ""))
            content = str(getattr(message, "content", "") or "")

        role = role.strip().lower()
        content = content.strip()

        if not content:
            continue

        if role in {"human", "user"}:
            chat_history.append({"role": "user", "content": content})
        elif role in {"ai", "assistant"}:
            chat_history.append({"role": "assistant", "content": content})

    return chat_history


def _score_city_to_dict(score: Any, city: Any) -> dict[str, Any]:
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
    candidates = _report_markdown_candidates(report_path)

    for candidate in candidates:
        safe_candidate = _safe_report_path(candidate, expected_suffix=".md")

        if safe_candidate is None:
            continue

        try:
            return safe_candidate.read_text(encoding="utf-8")
        except OSError:
            continue

    return ""


def _report_markdown_candidates(report_path: str) -> list[Path]:
    """Construit les chemins markdown candidats à partir d'un chemin de rapport."""
    if not report_path:
        return []

    raw_path = Path(report_path)

    if raw_path.suffix.lower() == ".md":
        candidates = [raw_path]
    else:
        candidates = [raw_path.with_suffix(".md")]

    # Si la DB stocke seulement un nom de fichier, on le cherche dans REPORTS_DIR.
    candidates.extend(REPORTS_DIR / candidate.name for candidate in list(candidates))

    unique_candidates: list[Path] = []
    seen: set[str] = set()

    for candidate in candidates:
        key = str(candidate)

        if key not in seen:
            unique_candidates.append(candidate)
            seen.add(key)

    return unique_candidates


def _safe_report_path(path: Path, expected_suffix: str) -> Path | None:
    """Valide qu'un chemin de rapport reste dans REPORTS_DIR."""
    if not path:
        return None

    candidate = path

    if not candidate.is_absolute():
        candidate = REPORTS_DIR / candidate.name

    try:
        reports_root = REPORTS_DIR.resolve()
        resolved = candidate.resolve()
    except OSError:
        return None

    if resolved.suffix.lower() != expected_suffix.lower():
        return None

    if not resolved.exists():
        return None

    if resolved != reports_root and reports_root not in resolved.parents:
        return None

    return resolved


def refresh_city_count() -> None:
    """Met à jour le nombre de villes présentes en base."""
    try:
        from db.models import City, SessionLocal

        db = SessionLocal()

        try:
            st.session_state.nb_villes = int(db.query(City).count())
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

    new_session_id = _new_session_id()
    st.session_state.session_id = new_session_id
    _set_session_id_in_url(new_session_id)

    st.rerun()


def update_session_from_agent_result(result: dict[str, Any] | None) -> None:
    """Copie les champs retournés par l'orchestrateur dans st.session_state."""
    _ensure_defaults()

    if not result:
        _set_session_id_in_url(st.session_state.session_id)
        return

    result_session_id = _sanitize_session_id(str(result.get("session_id") or ""))

    if result_session_id:
        st.session_state.session_id = result_session_id

    response = str(result.get("response") or "").strip()

    if response:
        st.session_state.chat_history.append(
            {
                "role": "assistant",
                "content": response,
            }
        )

    if "top_cities" in result and result.get("top_cities") is not None:
        st.session_state.top_cities = result.get("top_cities") or []

    if result.get("analysis_complete"):
        st.session_state.analysis_complete = True

    report_path = result.get("report_path") or result.get("report_pdf_path")

    if report_path:
        st.session_state.report_path = str(report_path)
        st.session_state.report_pdf_path = str(report_path)

    if result.get("report_markdown"):
        st.session_state.report_markdown = str(result["report_markdown"])

    if result.get("agent_trace"):
        st.session_state.agent_trace = result["agent_trace"]

    if result.get("iteration"):
        try:
            st.session_state.iteration = int(result["iteration"])
        except (TypeError, ValueError):
            pass

    _set_session_id_in_url(st.session_state.session_id)