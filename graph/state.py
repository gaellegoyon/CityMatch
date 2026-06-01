"""
graph/state.py
──────────────
Définition du State LangGraph partagé entre tous les agents.

Ce state est le bus de données central du système multi-agents CityMatch :
chaque agent lit certaines clés, en écrit d'autres, puis l'orchestrateur
route vers le nœud suivant.
"""

from typing import Annotated, Optional, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class CityMatchState(TypedDict, total=False):
    """
    État global du graphe LangGraph.

    total=False permet aux agents de mettre à jour seulement les champs utiles
    sans devoir fournir toutes les clés à chaque invocation.
    """

    # Session
    session_id: str
    iteration: int

    # Conversation
    messages: Annotated[list[BaseMessage], add_messages]
    user_input: str

    # Profil utilisateur
    user_criteria: Optional[dict]
    user_profile_complete: bool

    # Données villes
    raw_city_data: Optional[list[dict]]
    enriched_city_data: Optional[list[dict]]

    # Scoring
    scored_cities: Optional[list[dict]]
    top_cities: Optional[list[dict]]

    # RAG
    rag_context: Optional[str]
    rag_question: Optional[str]

    # Recherche web
    web_search_results: Optional[list[dict]]
    web_search_queries: Optional[list[dict]]

    # Rapport
    report_markdown: Optional[str]
    report_pdf_path: Optional[str]

    # Contrôle du flux
    error: Optional[str]
    should_refine: bool
    analysis_complete: bool

    # Debug
    agent_trace: Optional[list[str]]