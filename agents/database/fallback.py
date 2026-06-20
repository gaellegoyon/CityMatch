"""
agents/database/fallback.py
───────────────────────────
Relâchement progressif des filtres SQL lorsque trop peu de villes candidates
sont récupérées.

Le but est de conserver assez de villes pour permettre un scoring pertinent,
tout en relâchant les contraintes de façon contrôlée et explicable.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from config.settings import MAX_POPULATION, MIN_POPULATION
from db.models import City


logger = logging.getLogger(__name__)

DEFAULT_TARGET_MIN_CITIES = 10
DEFAULT_FINAL_LIMIT = 50
DEFAULT_MIN_POPULATION_FALLBACK = 10_000


def _as_int(value: Any, default: int) -> int:
    """Convertit une valeur en entier, ou retourne la valeur par défaut."""
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _query_with_conditions(
    db: Session,
    conditions: list[str],
    params: dict[str, Any],
) -> list[City]:
    """Exécute une requête City à partir d'une liste de conditions SQL."""
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    return db.query(City).filter(text(where_clause).bindparams(**params)).all()


def apply_progressive_fallback(
    db: Session,
    current_cities: list[City],
    user_profile: dict[str, Any],
    target_min: int = DEFAULT_TARGET_MIN_CITIES,
    final_limit: int = DEFAULT_FINAL_LIMIT,
) -> list[City]:
    """
    Relâche progressivement les filtres si trop peu de villes sont disponibles.

    Niveaux appliqués :
    1. élargissement de la distance à la mer à 50 km ;
    2. élargissement à 80 km et population minimale réduite ;
    3. suppression du filtre régional ;
    4. dernier recours : villes de plus de 10 000 habitants, limitées à 50.
    """
    if len(current_cities) >= target_min:
        return current_cities

    criteria = user_profile.get("criteres", {})
    preferred_regions = [region for region in user_profile.get("regions_preferees", []) if region]

    original_min_population = _as_int(
        user_profile.get("population_min"),
        MIN_POPULATION,
    )
    original_max_population = _as_int(
        user_profile.get("population_max"),
        MAX_POPULATION,
    )

    fallback_levels = [
        {
            "label": "distance_mer_50km",
            "max_mer": 50.0,
            "pop_min": original_min_population,
            "pop_max": original_max_population,
            "keep_regions": True,
        },
        {
            "label": "distance_mer_80km_population_10k",
            "max_mer": 80.0,
            "pop_min": DEFAULT_MIN_POPULATION_FALLBACK,
            "pop_max": original_max_population,
            "keep_regions": True,
        },
        {
            "label": "distance_mer_80km_sans_region",
            "max_mer": 80.0,
            "pop_min": DEFAULT_MIN_POPULATION_FALLBACK,
            "pop_max": original_max_population,
            "keep_regions": False,
        },
    ]

    for level in fallback_levels:
        conditions: list[str] = [
            "population >= :pop_min",
            "population <= :pop_max",
        ]

        params: dict[str, Any] = {
            "pop_min": level["pop_min"],
            "pop_max": level["pop_max"],
        }

        if criteria.get("distance_mer_km", 0) >= 3:
            conditions.append("distance_mer_km IS NOT NULL AND distance_mer_km <= :max_mer")
            params["max_mer"] = level["max_mer"]

        if level["keep_regions"] and preferred_regions:
            placeholders = ", ".join(
                f":fallback_region_{index}"
                for index in range(len(preferred_regions))
            )
            conditions.append(f"region IN ({placeholders})")

            for index, region in enumerate(preferred_regions):
                params[f"fallback_region_{index}"] = region

        fallback_cities = _query_with_conditions(
            db=db,
            conditions=conditions,
            params=params,
        )

        logger.info(
            "Fallback DatabaseAgent %s : %s villes",
            level["label"],
            len(fallback_cities),
        )

        if len(fallback_cities) >= target_min:
            return fallback_cities

    if len(current_cities) >= 5:
        return current_cities

    logger.info("Fallback DatabaseAgent dernier recours activé")

    return (
        db.query(City)
        .filter(text("population >= :min_population").bindparams(
            min_population=DEFAULT_MIN_POPULATION_FALLBACK,
        ))
        .limit(final_limit)
        .all()
    )