"""
agents/common/candidate_postprocessing.py
────────────────────────────────────────
Post-traitements des villes candidates avant scoring ou génération de rapport.

Ces fonctions enrichissent les dictionnaires de villes en mémoire, sans modifier
la base de données.
"""

from __future__ import annotations

from typing import Any

from agents.common.geo import haversine_km, normalize_place_name


DEFAULT_REFERENCE_RADIUS_KM = 80.0


def _to_positive_float(value: Any) -> float | None:
    """Convertit une valeur en float positif, ou None si impossible."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    return number if number > 0 else None


def apply_reference_city_filter(
    city_dicts: list[dict[str, Any]],
    user_profile: dict[str, Any],
    reference_coords: tuple[float, float] | None,
) -> list[dict[str, Any]]:
    """
    Filtre précisément les villes selon une distance Haversine à une ville de référence.

    La résolution de la ville de référence doit être faite en amont depuis la BDD.
    Cette fonction ne dépend donc pas d'une liste de coordonnées codée en dur.
    """
    if reference_coords is None:
        return city_dicts

    radius = _to_positive_float(
        user_profile.get("rayon_km") or user_profile.get("rayon_reference_km")
    )

    if radius is None:
        radius = DEFAULT_REFERENCE_RADIUS_KM

    reference_city_name = normalize_place_name(user_profile.get("ville_reference", ""))
    exclude_reference_city = bool(user_profile.get("exclure_ville_reference", False))

    ref_lat, ref_lon = reference_coords
    filtered: list[dict[str, Any]] = []

    for city in city_dicts:
        lat = _to_positive_float(city.get("latitude"))
        lon = _to_positive_float(city.get("longitude"))

        if lat is None or lon is None:
            continue

        city_name = normalize_place_name(city.get("nom", ""))

        if exclude_reference_city and city_name == reference_city_name:
            continue

        distance_km = haversine_km(ref_lat, ref_lon, lat, lon)

        if distance_km <= radius:
            enriched_city = {
                **city,
                "distance_reference_km": round(distance_km, 1),
            }
            filtered.append(enriched_city)

    return sorted(
        filtered,
        key=lambda city: city.get("distance_reference_km", float("inf")),
    )


def apply_budget_surface_estimate(
    city_dicts: list[dict[str, Any]],
    user_profile: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Ajoute une estimation indicative de surface achetable selon le budget utilisateur.

    L'estimation est volontairement simple : budget / prix au m².
    Elle ne tient pas compte des frais de notaire, travaux ou frais d'agence.
    """
    budget = _to_positive_float(user_profile.get("budget_immobilier"))

    if budget is None:
        return city_dicts

    enriched_cities: list[dict[str, Any]] = []

    for city in city_dicts:
        price_per_m2 = _to_positive_float(city.get("prix_immo_m2"))

        surface = round(budget / price_per_m2, 1) if price_per_m2 else None

        enriched_cities.append({
            **city,
            "surface_estimable_m2": surface,
        })

    return enriched_cities


def add_population_category(city_dicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ajoute une catégorie lisible de taille de ville aux résultats."""
    enriched_cities: list[dict[str, Any]] = []

    for city in city_dicts:
        population = _to_positive_float(city.get("population")) or 0

        if population < 10_000:
            category = "village / très petite ville"
        elif population < 50_000:
            category = "petite ville"
        elif population < 150_000:
            category = "ville moyenne"
        else:
            category = "grande ville"

        enriched_cities.append({
            **city,
            "taille_ville": category,
        })

    return enriched_cities