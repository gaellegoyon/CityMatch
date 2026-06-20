"""
graph/state.py
──────────────
Définition du State LangGraph partagé entre tous les agents.

Ce state est le bus de données central du système multi-agents CityMatch :
chaque agent lit certaines clés, en écrit d'autres, puis l'orchestrateur
route vers le nœud suivant.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class CityMatchState(TypedDict, total=False):
    """
    État global du graphe LangGraph.

    total=False permet aux agents de mettre à jour seulement les champs utiles
    sans devoir fournir toutes les clés à chaque invocation.

    Les champs listés ici correspondent au contrat partagé entre :
    - UserProfileAgent ;
    - DatabaseAgent ;
    - WebSearchAgent ;
    - ScoringAgent ;
    - RAGAgent ;
    - ReportAgent ;
    - Orchestrateur LangGraph.
    """

    # ─────────────────────────────────────────────────────────────────────
    # Session / contrôle global
    # ─────────────────────────────────────────────────────────────────────
    session_id: str
    iteration: int
    should_refine: bool
    analysis_complete: bool
    user_profile_complete: bool

    # ─────────────────────────────────────────────────────────────────────
    # Conversation
    # ─────────────────────────────────────────────────────────────────────
    messages: Annotated[list[BaseMessage], add_messages]
    user_input: str
    response: str
    final_response: str

    # ─────────────────────────────────────────────────────────────────────
    # Profil utilisateur / critères
    # ─────────────────────────────────────────────────────────────────────
    user_criteria: dict[str, Any]
    normalized_criteria: dict[str, Any]

    criteria_notes: list[str]
    criteres_non_disponibles: list[str]
    ignored_criteria: list[str]

    ville_reference: str
    reference_city_coords: tuple[float, float]
    rayon_reference_km: float

    # ─────────────────────────────────────────────────────────────────────
    # Données villes
    # ─────────────────────────────────────────────────────────────────────
    raw_city_data: list[dict[str, Any]]
    enriched_city_data: list[dict[str, Any]]

    # ─────────────────────────────────────────────────────────────────────
    # Scoring
    # ─────────────────────────────────────────────────────────────────────
    scored_cities: list[dict[str, Any]]
    top_cities: list[dict[str, Any]]

    # ─────────────────────────────────────────────────────────────────────
    # RAG
    # ─────────────────────────────────────────────────────────────────────
    rag_question: str
    rag_context: str
    rag_sources: list[dict[str, Any]]

    # ─────────────────────────────────────────────────────────────────────
    # Recherche web
    # ─────────────────────────────────────────────────────────────────────
    web_search_results: list[dict[str, Any]]
    web_search_queries: list[dict[str, Any]]

    # ─────────────────────────────────────────────────────────────────────
    # Rapport
    # ─────────────────────────────────────────────────────────────────────
    report_markdown: str
    report_pdf_path: str
    report_path: str

    # ─────────────────────────────────────────────────────────────────────
    # Erreurs / debug / audit
    # ─────────────────────────────────────────────────────────────────────
    error: str
    errors: list[str]
    warnings: list[str]
    agent_trace: list[str]