"""
graph/orchestrator.py
─────────────────────
Orchestrateur LangGraph principal — le cerveau du système CityMatch.

Définit le graphe d'états et les transitions entre agents :

    START
      ↓
    user_profile
      ↓ profil complet
    database_query
      ↓
    web_search
      ↓
    scoring
      ↓ question méthodologique éventuelle
    rag_node
      ↓
    report_generation
      ↓
    END

Le graphe supporte :
- la persistance de l'état entre les appels ;
- la reprise de session via thread_id ;
- la boucle de raffinement des critères ;
- le passage conditionnel par le RAG.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph
from rich.console import Console

from agents.database_agent import run_database_agent
from agents.rag_agent import run_rag_agent
from agents.report_agent import run_report_agent
from agents.scoring_agent import run_scoring_agent
from agents.user_profile_agent import run_user_profile_agent
from agents.web_search_agent import run_web_search_agent
from config.settings import DB_DIR
from graph.state import CityMatchState


logger = logging.getLogger(__name__)
console = Console()

CHECKPOINT_DB_NAME = "langgraph_checkpoints.db"
DEFAULT_RECURSION_LIMIT = 50

RAG_TRIGGERS = (
    "comment",
    "pourquoi",
    "définition",
    "definition",
    "signifie",
    "source",
    "méthodologie",
    "methodologie",
    "calculé",
    "calcule",
    "indicateur",
    "données",
    "donnees",
    "explique",
    "explication",
)


# LangGraph a changé l'emplacement de certains checkpointers selon les versions.
try:
    from langgraph.checkpoint.sqlite import SqliteSaver
    CHECKPOINT_BACKEND = "sqlite"
except ImportError:  # pragma: no cover - dépend de l'installation locale
    SqliteSaver = None
    CHECKPOINT_BACKEND = "memory"


try:
    from langgraph.checkpoint.memory import MemorySaver
except ImportError:  # pragma: no cover - très anciennes versions
    MemorySaver = None


# ─────────────────────────────────────────────────────────────────────────────
# Routage conditionnel
# ─────────────────────────────────────────────────────────────────────────────
def route_after_profile(state: CityMatchState) -> str:
    """
    Après l'agent de profil :
    - profil incomplet → fin temporaire, attente d'un nouveau message utilisateur ;
    - profil complet → requête base de données.
    """
    if state.get("user_profile_complete"):
        console.print("[dim]Route → database_query[/dim]")
        return "database_query"

    console.print("[dim]Route → END (attente input utilisateur)[/dim]")
    return END


def _should_use_rag_from_input(user_input: str | None) -> bool:
    """Détermine si la question utilisateur justifie le passage par le RAG."""
    text = str(user_input or "").lower()
    return any(trigger in text for trigger in RAG_TRIGGERS)


def route_after_scoring(state: CityMatchState) -> str:
    """
    Après le scoring :
    - si une question documentaire/méthodologique est détectée → RAG ;
    - sinon → rapport.
    """
    if _should_use_rag_from_input(state.get("user_input")):
        console.print("[dim]Route → rag_node[/dim]")
        return "rag_node"

    console.print("[dim]Route → report_generation[/dim]")
    return "report_generation"


def route_after_report(state: CityMatchState) -> str:
    """
    Après la génération du rapport :
    - should_refine=True → retour au profil ;
    - sinon → fin.
    """
    if state.get("should_refine"):
        console.print("[dim]Route → user_profile (raffinement)[/dim]")
        return "user_profile"

    console.print("[dim]Route → END[/dim]")
    return END


# ─────────────────────────────────────────────────────────────────────────────
# Nœuds wrapper
# ─────────────────────────────────────────────────────────────────────────────
def run_rag_node(state: CityMatchState) -> dict[str, Any]:
    """
    Prépare la question RAG sans muter l'état reçu par le routeur.

    Les routeurs LangGraph doivent rester aussi purs que possible ; la mise à jour
    de rag_question est donc faite dans ce nœud wrapper.
    """
    updated_state = dict(state)

    if not updated_state.get("rag_question"):
        updated_state["rag_question"] = str(updated_state.get("user_input", ""))

    return run_rag_agent(updated_state)


# ─────────────────────────────────────────────────────────────────────────────
# Checkpointers
# ─────────────────────────────────────────────────────────────────────────────
def _checkpoint_path() -> Path:
    """Retourne le chemin du fichier SQLite LangGraph."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    return DB_DIR / CHECKPOINT_DB_NAME


def _build_sqlite_checkpointer() -> Any | None:
    """
    Construit un checkpointer SQLite compatible avec les versions courantes.

    On évite `SqliteSaver.from_conn_string()` ici, car selon les versions il peut
    retourner un context manager plutôt qu'un objet directement compilable.
    """
    if SqliteSaver is None:
        return None

    memory_path = _checkpoint_path()

    try:
        connection = sqlite3.connect(
            str(memory_path),
            check_same_thread=False,
        )
        checkpointer = SqliteSaver(connection)
        console.print(f"[dim]Graphe compilé avec SQLite checkpointer : {memory_path}[/dim]")
        return checkpointer

    except Exception as exc:  # pragma: no cover - dépend de l'environnement
        logger.warning("SQLite checkpointer indisponible : %s", exc)
        console.print(f"[yellow]⚠️  SQLite checkpointer indisponible : {exc}[/yellow]")
        return None


def _build_memory_checkpointer() -> Any | None:
    """Construit un checkpointer mémoire si disponible."""
    if MemorySaver is None:
        return None

    console.print("[dim]Graphe compilé avec MemorySaver[/dim]")
    return MemorySaver()


# ─────────────────────────────────────────────────────────────────────────────
# Construction du graphe
# ─────────────────────────────────────────────────────────────────────────────
def build_graph(use_memory: bool = True) -> Any:
    """
    Construit et compile le graphe LangGraph CityMatch.

    Args:
        use_memory: Si True, active la persistance de session.

    Returns:
        Graphe compilé prêt à l'exécution.
    """
    builder = StateGraph(CityMatchState)

    builder.add_node("user_profile", run_user_profile_agent)
    builder.add_node("database_query", run_database_agent)
    builder.add_node("web_search", run_web_search_agent)
    builder.add_node("scoring", run_scoring_agent)
    builder.add_node("rag_node", run_rag_node)
    builder.add_node("report_generation", run_report_agent)

    builder.add_edge(START, "user_profile")

    builder.add_conditional_edges(
        "user_profile",
        route_after_profile,
        {
            "database_query": "database_query",
            END: END,
        },
    )

    builder.add_edge("database_query", "web_search")
    builder.add_edge("web_search", "scoring")

    builder.add_conditional_edges(
        "scoring",
        route_after_scoring,
        {
            "rag_node": "rag_node",
            "report_generation": "report_generation",
        },
    )

    builder.add_edge("rag_node", "report_generation")

    builder.add_conditional_edges(
        "report_generation",
        route_after_report,
        {
            "user_profile": "user_profile",
            END: END,
        },
    )

    if not use_memory:
        console.print("[dim]Graphe compilé sans persistance[/dim]")
        return builder.compile()

    checkpointer = _build_sqlite_checkpointer() or _build_memory_checkpointer()

    if checkpointer is not None:
        return builder.compile(checkpointer=checkpointer)

    console.print("[dim]Graphe compilé sans checkpointer[/dim]")
    return builder.compile()


# ─────────────────────────────────────────────────────────────────────────────
# Interface haut niveau
# ─────────────────────────────────────────────────────────────────────────────
class CityMatchOrchestrator:
    """
    Wrapper haut niveau autour du graphe LangGraph.

    Utilisable par l'interface Streamlit, le CLI ou des tests.
    """

    def __init__(self, use_memory: bool = True):
        self.graph = build_graph(use_memory=use_memory)
        self._current_state: dict[str, Any] | None = None

    @staticmethod
    def _config(session_id: str) -> dict[str, Any]:
        """Construit la configuration LangGraph pour une session."""
        return {
            "configurable": {
                "thread_id": session_id,
            },
            "recursion_limit": DEFAULT_RECURSION_LIMIT,
        }

    def start_session(
        self,
        user_message: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Démarre ou reprend une session de recherche.

        Args:
            user_message: premier message ou question de l'utilisateur.
            session_id: ID de session existante, sinon UUID généré.

        Returns:
            Dictionnaire prêt pour l'interface.
        """
        sid = session_id or str(uuid.uuid4())
        config = self._config(sid)

        initial_state: dict[str, Any] = {
            "user_input": user_message,
            "messages": [],
            "session_id": sid,
            "iteration": 1,
            "user_profile_complete": False,
            "analysis_complete": False,
            "should_refine": False,
            "agent_trace": [],
        }

        result = self.graph.invoke(initial_state, config=config)
        self._current_state = result

        return self._extract_response(result)

    def send_message(
        self,
        user_message: str,
        session_id: str,
    ) -> dict[str, Any]:
        """
        Envoie un message dans une session existante.

        Args:
            user_message: message utilisateur.
            session_id: ID de session en cours.

        Returns:
            Dictionnaire prêt pour l'interface.
        """
        config = self._config(session_id)

        update = {
            "user_input": user_message,
            "session_id": session_id,
        }

        result = self.graph.invoke(update, config=config)
        self._current_state = result

        return self._extract_response(result)

    def request_refinement(self, session_id: str) -> dict[str, Any]:
        """
        Déclenche la boucle de raffinement.

        L'état existant est conservé par le checkpointer LangGraph. On force
        seulement le retour vers une collecte de critères.
        """
        config = self._config(session_id)

        try:
            current = self.graph.get_state(config)
            current_values = getattr(current, "values", {}) or {}
            iteration = int(current_values.get("iteration", 1) or 1) + 1

            update = {
                "session_id": session_id,
                "should_refine": True,
                "user_profile_complete": False,
                "analysis_complete": False,
                "iteration": iteration,
                "user_input": "Je souhaite affiner mes critères.",
            }

            result = self.graph.invoke(update, config=config)
            self._current_state = result

            console.print(f"[dim]Raffinement demandé — itération {iteration}[/dim]")

            return self._extract_response(result)

        except Exception as exc:
            logger.exception("Erreur request_refinement")
            console.print(f"[yellow]⚠️  request_refinement : {exc}[/yellow]")
            return {
                "response": "Impossible de déclencher le raffinement pour le moment.",
                "profile_complete": False,
                "analysis_complete": False,
                "top_cities": [],
                "report_path": "",
                "report_markdown": "",
                "session_id": session_id,
                "agent_trace": [],
                "iteration": 1,
            }

    @staticmethod
    def _message_content(message: Any) -> str:
        """Extrait le contenu textuel d'un message LangChain ou dict."""
        content = getattr(message, "content", None)

        if isinstance(content, str):
            return content

        if isinstance(message, dict):
            raw_content = message.get("content")

            if isinstance(raw_content, str):
                return raw_content

        if isinstance(message, str):
            return message

        return ""

    @staticmethod
    def _is_ai_message(message: Any) -> bool:
        """Détecte un message IA sans dépendre strictement de LangChain."""
        message_type = getattr(message, "type", None)

        if message_type == "ai":
            return True

        class_name = message.__class__.__name__.lower()

        if "aimessage" in class_name:
            return True

        if isinstance(message, dict):
            role = str(message.get("role") or message.get("type") or "").lower()
            return role in {"ai", "assistant"}

        return False

    def _extract_response(self, state: dict[str, Any]) -> dict[str, Any]:
        """Extrait les informations clés de l'état pour retour à l'interface."""
        messages = state.get("messages", []) or []
        ai_response = ""

        for message in reversed(messages):
            if self._is_ai_message(message):
                ai_response = self._message_content(message)
                break

        if not ai_response:
            ai_response = str(state.get("final_response") or state.get("response") or "")

        return {
            "response": ai_response,
            "profile_complete": bool(state.get("user_profile_complete", False)),
            "analysis_complete": bool(state.get("analysis_complete", False)),
            "top_cities": state.get("top_cities", []) or [],
            "report_path": state.get("report_pdf_path") or state.get("report_path") or "",
            "report_markdown": state.get("report_markdown", "") or "",
            "session_id": state.get("session_id", ""),
            "agent_trace": state.get("agent_trace", []) or [],
            "iteration": state.get("iteration", 1) or 1,
        }


__all__ = [
    "CHECKPOINT_BACKEND",
    "CityMatchOrchestrator",
    "build_graph",
    "route_after_profile",
    "route_after_report",
    "route_after_scoring",
]
