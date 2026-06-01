"""
graph/orchestrator.py
──────────────────────
Orchestrateur LangGraph principal — le cerveau du système CityMatch.

Définit le graphe d'états et les transitions entre agents :

  [START]
    │
    ▼
  user_profile ──(profil incomplet)──► [retour utilisateur]
    │
    │ (profil complet)
    ▼
  database_query
    │
    ▼
  web_search
    │
    ▼
  scoring
    │
    ▼
  report_generation
    │
    ▼
  [END] ──(should_refine=True)──► user_profile [boucle]

Le graphe supporte :
  - La persistance de l'état entre les appels (mémoire de session)
  - La boucle de raffinement des critères (l'utilisateur peut modifier ses préférences)
  - Le nœud RAG déclenché conditionnellement selon les questions
"""

from langgraph.graph import StateGraph, START, END

# LangGraph a déplacé SqliteSaver selon les versions :
# - <0.2.x  : langgraph.checkpoint.sqlite
# - >=0.2.x : langgraph_checkpoint_sqlite (paquet séparé)
# On essaie les deux avec un fallback mémoire si aucun n'est disponible.
try:
    from langgraph.checkpoint.sqlite import SqliteSaver
    _CHECKPOINT_BACKEND = "sqlite_builtin"
except ImportError:
    try:
        from langgraph_checkpoint_sqlite import SqliteSaver
        _CHECKPOINT_BACKEND = "sqlite_external"
    except ImportError:
        SqliteSaver = None
        _CHECKPOINT_BACKEND = "memory"

from graph.state import CityMatchState
from agents.user_profile_agent import run_user_profile_agent
from agents.database_agent import run_database_agent
from agents.web_search_agent import run_web_search_agent
from agents.scoring_agent import run_scoring_agent
from agents.rag_agent import run_rag_agent
from agents.report_agent import run_report_agent
from config.settings import DB_DIR
from rich.console import Console

console = Console()


# ─── Fonctions de routage conditionnel ────────────────────────────────────────

def route_after_profile(state: CityMatchState) -> str:
    """
    Après l'agent de profil :
    - Si profil incomplet → attendre la prochaine saisie utilisateur (END temporaire)
    - Si profil complet → continuer vers la BDD
    """
    if state.get("user_profile_complete"):
        console.print("[dim]Route → database_query[/dim]")
        return "database_query"
    console.print("[dim]Route → END (attente input utilisateur)[/dim]")
    return END


def route_after_scoring(state: CityMatchState) -> str:
    """
    Après le scoring :
    - Si une question RAG est détectée → passer par le RAG
    - Sinon → générer le rapport
    """
    user_input = state.get("user_input", "").lower()
    rag_triggers = ["comment", "pourquoi", "définition", "signifie", "source",
                    "méthodologie", "calculé", "indicateur", "données"]

    if any(t in user_input for t in rag_triggers):
        state["rag_question"] = user_input
        console.print("[dim]Route → rag_node[/dim]")
        return "rag_node"

    console.print("[dim]Route → report_generation[/dim]")
    return "report_generation"


def route_after_report(state: CityMatchState) -> str:
    """
    Après la génération du rapport :
    - Si l'utilisateur veut affiner → retour au profil
    - Sinon → fin
    """
    if state.get("should_refine"):
        state["should_refine"] = False
        state["user_profile_complete"] = False
        state["iteration"] = state.get("iteration", 1) + 1
        console.print("[dim]Route → user_profile (raffinement)[/dim]")
        return "user_profile"
    console.print("[dim]Route → END[/dim]")
    return END


# ─── Construction du graphe ────────────────────────────────────────────────────

def build_graph(use_memory: bool = True):
    """
    Construit et compile le graphe LangGraph CityMatch.

    Args:
        use_memory: Si True, active la persistance SQLite (reprise de session)

    Returns:
        CompiledGraph prêt à l'exécution
    """
    # Initialiser le builder
    builder = StateGraph(CityMatchState)

    # ── Ajout des nœuds ───────────────────────────────────────────────────────
    builder.add_node("user_profile", run_user_profile_agent)
    builder.add_node("database_query", run_database_agent)
    builder.add_node("web_search", run_web_search_agent)
    builder.add_node("scoring", run_scoring_agent)
    builder.add_node("rag_node", run_rag_agent)
    builder.add_node("report_generation", run_report_agent)

    # ── Connexions (edges) ────────────────────────────────────────────────────
    builder.add_edge(START, "user_profile")

    builder.add_conditional_edges(
        "user_profile",
        route_after_profile,
        {
            "database_query": "database_query",
            END: END,
        }
    )

    builder.add_edge("database_query", "web_search")
    builder.add_edge("web_search", "scoring")

    builder.add_conditional_edges(
        "scoring",
        route_after_scoring,
        {
            "rag_node": "rag_node",
            "report_generation": "report_generation",
        }
    )

    builder.add_edge("rag_node", "report_generation")

    builder.add_conditional_edges(
        "report_generation",
        route_after_report,
        {
            "user_profile": "user_profile",
            END: END,
        }
    )

    # ── Compilation avec mémoire persistante ──────────────────────────────────
    if use_memory and SqliteSaver is not None:
        try:
            memory_path = str(DB_DIR / "langgraph_checkpoints.db")
            try:
                memory = SqliteSaver.from_conn_string(memory_path)
            except AttributeError:
                import sqlite3
                conn = sqlite3.connect(memory_path, check_same_thread=False)
                memory = SqliteSaver(conn)
            graph = builder.compile(checkpointer=memory)
            console.print(f"[dim]Graphe compilé ({_CHECKPOINT_BACKEND}) : {memory_path}[/dim]")
        except Exception as e:
            console.print(f"[yellow]⚠️  SQLite checkpointer indisponible ({e}), bascule sur MemorySaver[/yellow]")
            from langgraph.checkpoint.memory import MemorySaver
            graph = builder.compile(checkpointer=MemorySaver())
    else:
        try:
            from langgraph.checkpoint.memory import MemorySaver
            graph = builder.compile(checkpointer=MemorySaver())
            console.print("[dim]Graphe compilé avec MemorySaver[/dim]")
        except Exception:
            graph = builder.compile()
            console.print("[dim]Graphe compilé sans persistance[/dim]")

    return graph


# ─── Interface simplifiée pour l'utilisation externe ──────────────────────────

class CityMatchOrchestrator:
    """
    Wrapper de haut niveau autour du graphe LangGraph.
    Fournit une API simple pour l'interface Streamlit et le CLI.
    """

    def __init__(self, use_memory: bool = True):
        self.graph = build_graph(use_memory=use_memory)
        self._current_state = None

    def start_session(self, user_message: str, session_id: str = None) -> dict:
        """
        Démarre ou reprend une session de recherche.

        Args:
            user_message: Premier message ou question de l'utilisateur
            session_id: ID de session existante (pour reprise)

        Returns:
            dict avec les clés : response, state, top_cities, report_path
        """
        import uuid
        sid = session_id or str(uuid.uuid4())
        config = {"configurable": {"thread_id": sid}}

        initial_state = {
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

    def send_message(self, user_message: str, session_id: str) -> dict:
        """
        Envoie un message dans une session existante.

        Args:
            user_message: Message de l'utilisateur
            session_id: ID de la session en cours

        Returns:
            dict avec les clés : response, state, top_cities, report_path
        """
        config = {"configurable": {"thread_id": session_id}}

        update = {"user_input": user_message}
        result = self.graph.invoke(update, config=config)
        self._current_state = result
        return self._extract_response(result)

    def request_refinement(self, session_id: str) -> dict:
        """
        Déclenche la boucle de raffinement :
        - Remet should_refine=True dans l'état persisté
        - Remet user_profile_complete=False pour re-collecter les critères
        - Incrémente le compteur d'itération
        L'état courant (top_cities, scoring) est conservé jusqu'au prochain scoring.
        """
        config = {"configurable": {"thread_id": session_id}}
        try:
            # Récupérer l'état courant pour incrémenter l'itération
            current = self.graph.get_state(config)
            iteration = (current.values.get("iteration", 1) if current else 1) + 1

            update = {
                "should_refine": True,
                "user_profile_complete": False,
                "analysis_complete": False,
                "iteration": iteration,
            }
            result = self.graph.invoke(update, config=config)
            self._current_state = result
            console.print(f"[dim]Raffinement demandé — itération {iteration}[/dim]")
            return self._extract_response(result)
        except Exception as e:
            console.print(f"[yellow]⚠️  request_refinement : {e}[/yellow]")
            return {}

    def _extract_response(self, state: dict) -> dict:
        """Extrait les informations clés de l'état pour retour à l'interface."""
        from langchain_core.messages import AIMessage

        # Dernière réponse de l'IA
        messages = state.get("messages", [])
        ai_response = ""
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                ai_response = msg.content
                break

        return {
            "response": ai_response,
            "profile_complete": state.get("user_profile_complete", False),
            "analysis_complete": state.get("analysis_complete", False),
            "top_cities": state.get("top_cities", []),
            "report_path": state.get("report_pdf_path", ""),
            "report_markdown": state.get("report_markdown", ""),
            "session_id": state.get("session_id", ""),
            "agent_trace": state.get("agent_trace", []),
            "iteration": state.get("iteration", 1),
        }