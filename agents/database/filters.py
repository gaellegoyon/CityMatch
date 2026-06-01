"""
agents/database/filters.py
──────────────────────────
Construction du WHERE SQL pour présélectionner les villes.
"""

import math
from rich.console import Console
from config.settings import MIN_POPULATION, MAX_POPULATION
from agents.common.criteria import filter_valid_criteria
from agents.common.geo import normalize_place_name, resolve_reference_city

console = Console()


def _as_int(value, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except Exception:
        return default


def add_population_filter(conditions: list[str], params: dict, user_profile: dict) -> None:
    pop_min = max(0, _as_int(user_profile.get("population_min"), MIN_POPULATION))
    pop_max = max(_as_int(user_profile.get("population_max"), MAX_POPULATION), 1000)
    if pop_min > pop_max:
        pop_min, pop_max = pop_max, pop_min
    conditions.append("population >= :pop_min AND population <= :pop_max")
    params.update(pop_min=pop_min, pop_max=pop_max)
    console.print(f"[dim]Filtre population : {pop_min:,} – {pop_max:,} habitants[/dim]")


def add_region_filter(conditions: list[str], params: dict, user_profile: dict) -> None:
    regions = [r for r in user_profile.get("regions_preferees", []) if r]
    if not regions:
        return
    placeholders = ", ".join(f":region_{i}" for i in range(len(regions)))
    conditions.append(f"region IN ({placeholders})")
    for i, region in enumerate(regions):
        params[f"region_{i}"] = region
    console.print(f"[dim]Filtre régional actif : {regions}[/dim]")


def add_budget_filter(conditions: list[str], params: dict, user_profile: dict) -> None:
    budget = user_profile.get("budget_immobilier")
    surface_min = user_profile.get("surface_min_m2")
    if not (budget and surface_min):
        return
    try:
        budget_float = float(budget)
        surface_float = max(float(surface_min), 1.0)
        params["max_prix_budget"] = round((budget_float / surface_float) * 1.10, 0)
        conditions.append("prix_immo_m2 IS NOT NULL AND prix_immo_m2 <= :max_prix_budget")
        console.print(f"[dim]Filtre budget : {budget_float:.0f}€ / {surface_float:.0f}m² → prix max {params['max_prix_budget']:.0f}€/m²[/dim]")
    except Exception:
        return


def add_priority_criteria_filters(conditions: list[str], params: dict, criteres: dict) -> None:
    if criteres.get("taux_chomage", 0) >= 4:
        conditions.append("taux_chomage IS NOT NULL AND taux_chomage < :max_chomage")
        params["max_chomage"] = 15.0
    if criteres.get("prix_immo_m2", 0) >= 4:
        conditions.append("prix_immo_m2 IS NOT NULL AND prix_immo_m2 < :max_prix")
        params["max_prix"] = 4000.0
    if criteres.get("creches_pour_1000", 0) >= 4:
        conditions.append("nb_creches > 0")
    if criteres.get("medecins_pour_1000", 0) >= 4:
        conditions.append("nb_medecins_generalistes > 0")
    if criteres.get("transport_score", 0) == 5:
        conditions.append("nb_gares > 0")
    if criteres.get("fibre_pct", 0) >= 5:
        conditions.append("fibre_pct IS NOT NULL AND fibre_pct >= :min_fibre")
        params["min_fibre"] = 90.0
    if criteres.get("score_securite", 0) >= 4:
        conditions.append("(score_securite IS NULL OR score_securite >= :min_secu OR criminalite_pour_1000 IS NULL OR criminalite_pour_1000 < :max_crime)")
        params.update(min_secu=4.0, max_crime=50.0)


def add_geo_filters(conditions: list[str], params: dict, criteres: dict, user_profile: dict) -> None:
    if criteres.get("distance_mer_km", 0) >= 4:
        conditions.append("distance_mer_km IS NOT NULL AND distance_mer_km <= :max_mer")
        params["max_mer"] = 30.0
    elif criteres.get("distance_mer_km", 0) == 3:
        conditions.append("distance_mer_km IS NOT NULL AND distance_mer_km <= :max_mer")
        params["max_mer"] = 60.0

    ville_ref = normalize_place_name(user_profile.get("ville_reference", ""))
    rayon_ref = user_profile.get("rayon_km") or user_profile.get("rayon_reference_km") or 80
    coords = resolve_reference_city(ville_ref)
    if not (ville_ref and coords):
        return

    ref_lat, ref_lon = coords
    try:
        rayon_float = float(rayon_ref)
    except Exception:
        rayon_float = 80.0
    dlat = rayon_float / 111.0
    dlon = rayon_float / max(1e-6, 111.0 * math.cos(math.radians(ref_lat)))
    conditions.append("latitude BETWEEN :ref_lat_min AND :ref_lat_max AND longitude BETWEEN :ref_lon_min AND :ref_lon_max")
    params.update(
        ref_lat_min=ref_lat - dlat,
        ref_lat_max=ref_lat + dlat,
        ref_lon_min=ref_lon - dlon,
        ref_lon_max=ref_lon + dlon,
    )
    console.print(f"[dim]Filtre proximité approximatif SQL : {ville_ref} ≤ {rayon_float:.0f} km[/dim]")


def build_sql_filter(criteria: dict, user_profile: dict) -> tuple[str, dict]:
    conditions: list[str] = []
    params: dict = {}
    criteres, ignored = filter_valid_criteria((criteria or {}).get("criteres", {}))
    if ignored:
        console.print(f"[yellow]⚠️  Critères ignorés (invalides) : {ignored}[/yellow]")

    add_population_filter(conditions, params, user_profile)
    add_region_filter(conditions, params, user_profile)
    add_budget_filter(conditions, params, user_profile)
    add_priority_criteria_filters(conditions, params, criteres)
    add_geo_filters(conditions, params, criteres, user_profile)

    return (" AND ".join(conditions) if conditions else "1=1"), params
