"""
agents/database_agent.py
────────────────────────
Agent d'accès à la base SQLite.

Ce fichier orchestre la requête du DatabaseAgent :
1. nettoyage des critères utilisateur ;
2. résolution éventuelle d'une ville de référence depuis la BDD ;
3. construction du filtre SQL ;
4. requête des villes candidates ;
5. fallback progressif si trop peu de résultats ;
6. post-traitements métier ;
7. mise à jour de l'état LangGraph et journalisation.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from agents.common.criteria import filter_valid_criteria
from agents.database.candidate_postprocessing import (
    add_population_category,
    apply_budget_surface_estimate,
    apply_reference_city_filter,
)
from agents.database.fallback import apply_progressive_fallback
from agents.database.filters import build_sql_filter
from agents.database.repository import (
    find_city_coordinates_by_name,
    query_cities_by_filter,
    serialize_cities,
)
from db.models import AgentLog, SessionLocal
from graph.state import CityMatchState
from utils.serialization import to_python


logger = logging.getLogger(__name__)

DATABASE_AGENT_NAME = "DatabaseAgent"
DATABASE_AGENT_ACTION = "query_cities"
TARGET_MIN_CITIES = 10
MAX_AGENT_TRACE_ENTRIES = 200


def _append_agent_trace(
    state: CityMatchState,
    message: str,
) -> None:
    """Ajoute une trace courte dans l'état LangGraph."""
    trace = list(state.get("agent_trace") or [])
    trace.append(message)
    state["agent_trace"] = trace[-MAX_AGENT_TRACE_ENTRIES:]


def _add_user_note(
    user_criteria: dict[str, Any],
    note: str,
) -> None:
    """Ajoute une note utilisateur sans doublon."""
    existing_notes = user_criteria.get("notes", [])

    if isinstance(existing_notes, str):
        notes = [existing_notes]
    elif isinstance(existing_notes, list):
        notes = [str(item) for item in existing_notes if item]
    else:
        notes = []

    if note not in notes:
        notes.append(note)

    user_criteria["notes"] = notes


def _clean_user_criteria(
    user_criteria: dict[str, Any],
) -> tuple[dict[str, Any], set[str]]:
    """Filtre les critères LLM pour ne conserver que ceux autorisés."""
    raw_criteria = user_criteria.get("criteres", {})
    valid_criteria, ignored_criteria = filter_valid_criteria(raw_criteria)

    cleaned_criteria = {
        **user_criteria,
        "criteres": valid_criteria,
    }

    return cleaned_criteria, ignored_criteria


def _resolve_reference_coords(
    db,
    user_criteria: dict[str, Any],
) -> tuple[float, float] | None:
    """
    Résout la ville de référence depuis la BDD.

    Si la ville n'est pas trouvée, on ajoute une note pour éviter de faire croire
    que le filtre géographique a été appliqué.
    """
    reference_city = user_criteria.get("ville_reference")

    if not reference_city:
        return None

    reference_coords = find_city_coordinates_by_name(
        db=db,
        city_name=reference_city,
    )

    if reference_coords is None:
        note = (
            f"La ville de référence « {reference_city} » n'a pas été retrouvée "
            "dans la base de données. Le filtre de proximité n'a donc pas été "
            "appliqué pour cette recherche."
        )
        _add_user_note(user_criteria, note)

        logger.warning(
            "Ville de référence non résolue dans la BDD : %s",
            reference_city,
        )

    return reference_coords


def run_database_agent(state: CityMatchState) -> CityMatchState:
    """
    Nœud LangGraph : agent de requête base de données.

    Lit les critères utilisateur depuis l'état, interroge la base SQLite,
    applique les post-traitements nécessaires et écrit les villes candidates
    dans ``raw_city_data``.
    """
    start_time = time.perf_counter()

    user_criteria = dict(state.get("user_criteria") or {})
    session_id = str(state.get("session_id") or "unknown")

    db = SessionLocal()

    log_entry = AgentLog(
        session_id=session_id,
        agent_name=DATABASE_AGENT_NAME,
        action=DATABASE_AGENT_ACTION,
        input_data=to_python(user_criteria),
        success=False,
    )

    try:
        user_criteria_clean, ignored_criteria = _clean_user_criteria(user_criteria)

        if ignored_criteria:
            logger.warning(
                "Critères LLM ignorés car hors whitelist : %s",
                sorted(ignored_criteria),
            )

        reference_coords = _resolve_reference_coords(
            db=db,
            user_criteria=user_criteria_clean,
        )

        # Important : les agents suivants doivent recevoir les critères nettoyés,
        # pas la sortie brute du LLM.
        state["user_criteria"] = user_criteria_clean

        where_clause, params = build_sql_filter(
            criteria=user_criteria_clean,
            user_profile=user_criteria_clean,
            reference_coords=reference_coords,
        )

        cities = query_cities_by_filter(
            db=db,
            where_clause=where_clause,
            params=params,
        )

        pre_fallback_count = len(cities)

        cities = apply_progressive_fallback(
            db=db,
            current_cities=cities,
            user_profile=user_criteria_clean,
            target_min=TARGET_MIN_CITIES,
        )

        post_fallback_count = len(cities)

        city_dicts = serialize_cities(cities)

        city_dicts = apply_reference_city_filter(
            city_dicts=city_dicts,
            user_profile=user_criteria_clean,
            reference_coords=reference_coords,
        )

        city_dicts = apply_budget_surface_estimate(
            city_dicts=city_dicts,
            user_profile=user_criteria_clean,
        )

        city_dicts = add_population_category(city_dicts)

        state["raw_city_data"] = city_dicts

        duration_ms = int((time.perf_counter() - start_time) * 1000)

        log_entry.output_data = to_python(
            {
                "cities_count": len(city_dicts),
                "pre_fallback_count": pre_fallback_count,
                "post_fallback_count": post_fallback_count,
                "ignored_criteria": sorted(ignored_criteria),
                "reference_city_requested": bool(user_criteria_clean.get("ville_reference")),
                "reference_city_resolved": reference_coords is not None,
                "where_clause": where_clause,
                "params": params,
            }
        )
        log_entry.duration_ms = duration_ms
        log_entry.success = True

        _append_agent_trace(
            state,
            f"{DATABASE_AGENT_NAME}: {len(city_dicts)} villes chargées en {duration_ms} ms",
        )

    except Exception as exc:
        db.rollback()

        duration_ms = int((time.perf_counter() - start_time) * 1000)

        logger.exception("Erreur pendant l'exécution du DatabaseAgent")

        state["error"] = f"{DATABASE_AGENT_NAME}: {exc}"
        state["raw_city_data"] = []

        log_entry.duration_ms = duration_ms
        log_entry.success = False
        log_entry.error_message = str(exc)

        _append_agent_trace(
            state,
            f"{DATABASE_AGENT_NAME}: erreur après {duration_ms} ms",
        )

    finally:
        try:
            db.add(log_entry)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Impossible d'enregistrer le log du DatabaseAgent")
        finally:
            db.close()

    return state