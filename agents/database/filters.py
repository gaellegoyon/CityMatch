"""
agents/database/filters.py
──────────────────────────
Construction du WHERE SQL pour présélectionner les villes candidates.
"""

from __future__ import annotations

import math
from typing import Any

from agents.common.criteria import filter_valid_criteria
from config.settings import MAX_POPULATION, MIN_POPULATION


DEFAULT_REFERENCE_RADIUS_KM = 80.0


# Seuils de pré-filtrage métier.
# Ils servent uniquement à réduire l'espace de recherche avant le scoring.
# Le classement final reste déterminé par le ScoringAgent.
MAX_CHOMAGE_PCT = 15.0
MAX_PRICE_PER_M2 = 4_000.0
MIN_FIBER_PCT = 90.0
MIN_SECURITY_SCORE = 4.0
MAX_CRIME_PER_1000 = 50.0


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _as_positive_float(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default

    return number if number > 0 else default


def add_population_filter(
    conditions: list[str],
    params: dict[str, Any],
    user_profile: dict[str, Any],
) -> None:
    pop_min = max(0, _as_int(user_profile.get("population_min"), MIN_POPULATION))
    pop_max = max(_as_int(user_profile.get("population_max"), MAX_POPULATION), 1_000)

    if pop_min > pop_max:
        pop_min, pop_max = pop_max, pop_min

    conditions.append("population >= :pop_min AND population <= :pop_max")
    params.update(pop_min=pop_min, pop_max=pop_max)


def add_region_filter(
    conditions: list[str],
    params: dict[str, Any],
    user_profile: dict[str, Any],
) -> None:
    regions = [region for region in user_profile.get("regions_preferees", []) if region]

    if not regions:
        return

    placeholders = ", ".join(f":region_{index}" for index in range(len(regions)))
    conditions.append(f"region IN ({placeholders})")

    for index, region in enumerate(regions):
        params[f"region_{index}"] = region


def add_budget_filter(
    conditions: list[str],
    params: dict[str, Any],
    user_profile: dict[str, Any],
) -> None:
    budget = _as_positive_float(user_profile.get("budget_immobilier"))
    surface_min = _as_positive_float(user_profile.get("surface_min_m2"))

    if budget is None or surface_min is None:
        return

    max_price = round((budget / surface_min) * 1.10, 0)

    conditions.append("prix_immo_m2 IS NOT NULL AND prix_immo_m2 <= :max_prix_budget")
    params["max_prix_budget"] = max_price


def add_priority_criteria_filters(
    conditions: list[str],
    params: dict[str, Any],
    criteria: dict[str, Any],
) -> None:
    if criteria.get("taux_chomage", 0) >= 4:
        conditions.append("taux_chomage IS NOT NULL AND taux_chomage < :max_chomage")
        params["max_chomage"] = MAX_CHOMAGE_PCT

    if criteria.get("prix_immo_m2", 0) >= 4:
        conditions.append("prix_immo_m2 IS NOT NULL AND prix_immo_m2 < :max_prix")
        params["max_prix"] = MAX_PRICE_PER_M2

    if criteria.get("creches_pour_1000", 0) >= 4:
        conditions.append("nb_creches > 0")

    if criteria.get("medecins_pour_1000", 0) >= 4:
        conditions.append("nb_medecins_generalistes > 0")

    if criteria.get("transport_score", 0) == 5:
        conditions.append("nb_gares > 0")

    if criteria.get("fibre_pct", 0) >= 5:
        conditions.append("fibre_pct IS NOT NULL AND fibre_pct >= :min_fibre")
        params["min_fibre"] = MIN_FIBER_PCT

    if criteria.get("score_securite", 0) >= 4:
        conditions.append(
            "("
            "(score_securite IS NOT NULL AND score_securite >= :min_secu) "
            "OR "
            "(criminalite_pour_1000 IS NOT NULL AND criminalite_pour_1000 < :max_crime)"
            ")"
        )
        params.update(min_secu=MIN_SECURITY_SCORE, max_crime=MAX_CRIME_PER_1000)


def add_geo_filters(
    conditions: list[str],
    params: dict[str, Any],
    criteria: dict[str, Any],
    user_profile: dict[str, Any],
    reference_coords: tuple[float, float] | None = None,
) -> None:
    if criteria.get("distance_mer_km", 0) >= 4:
        conditions.append("distance_mer_km IS NOT NULL AND distance_mer_km <= :max_mer")
        params["max_mer"] = 30.0
    elif criteria.get("distance_mer_km", 0) == 3:
        conditions.append("distance_mer_km IS NOT NULL AND distance_mer_km <= :max_mer")
        params["max_mer"] = 60.0

    if reference_coords is None:
        return

    radius = _as_positive_float(
        user_profile.get("rayon_km") or user_profile.get("rayon_reference_km"),
        default=DEFAULT_REFERENCE_RADIUS_KM,
    ) or DEFAULT_REFERENCE_RADIUS_KM

    ref_lat, ref_lon = reference_coords

    dlat = radius / 111.0
    dlon = radius / max(1e-6, 111.0 * math.cos(math.radians(ref_lat)))

    conditions.append(
        "latitude BETWEEN :ref_lat_min AND :ref_lat_max "
        "AND longitude BETWEEN :ref_lon_min AND :ref_lon_max"
    )

    params.update(
        ref_lat_min=ref_lat - dlat,
        ref_lat_max=ref_lat + dlat,
        ref_lon_min=ref_lon - dlon,
        ref_lon_max=ref_lon + dlon,
    )


def build_sql_filter(
    criteria: dict[str, Any] | None,
    user_profile: dict[str, Any],
    reference_coords: tuple[float, float] | None = None,
) -> tuple[str, dict[str, Any]]:
    conditions: list[str] = []
    params: dict[str, Any] = {}

    valid_criteria, _ignored = filter_valid_criteria((criteria or {}).get("criteres", {}))

    add_population_filter(conditions, params, user_profile)
    add_region_filter(conditions, params, user_profile)
    add_budget_filter(conditions, params, user_profile)
    add_priority_criteria_filters(conditions, params, valid_criteria)
    add_geo_filters(conditions, params, valid_criteria, user_profile, reference_coords)

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    return where_clause, params