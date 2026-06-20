"""
tests/test_candidate_postprocessing.py
──────────────────────────────────────
Tests des post-traitements appliqués aux villes candidates avant scoring.
"""

import pytest

from agents.database.candidate_postprocessing import (
    add_population_category,
    apply_budget_surface_estimate,
    apply_reference_city_filter,
)


LYON_COORDS = (45.748, 4.847)


def test_apply_reference_city_filter_keeps_cities_inside_radius() -> None:
    cities = [
        {"nom": "Lyon", "latitude": 45.748, "longitude": 4.847},
        {"nom": "Villeurbanne", "latitude": 45.766, "longitude": 4.880},
        {"nom": "Grenoble", "latitude": 45.188, "longitude": 5.724},
    ]

    user_profile = {
        "ville_reference": "Lyon",
        "rayon_km": 10,
    }

    result = apply_reference_city_filter(
        city_dicts=cities,
        user_profile=user_profile,
        reference_coords=LYON_COORDS,
    )

    names = [city["nom"] for city in result]

    assert names == ["Lyon", "Villeurbanne"]
    assert all("distance_reference_km" in city for city in result)
    assert result[0]["distance_reference_km"] <= result[1]["distance_reference_km"]


def test_apply_reference_city_filter_excludes_reference_city_when_requested() -> None:
    cities = [
        {"nom": "Lyon", "latitude": 45.748, "longitude": 4.847},
        {"nom": "Villeurbanne", "latitude": 45.766, "longitude": 4.880},
    ]

    user_profile = {
        "ville_reference": "Lyon",
        "rayon_km": 10,
        "exclure_ville_reference": True,
    }

    result = apply_reference_city_filter(
        city_dicts=cities,
        user_profile=user_profile,
        reference_coords=LYON_COORDS,
    )

    assert [city["nom"] for city in result] == ["Villeurbanne"]


def test_apply_reference_city_filter_ignores_cities_without_coordinates() -> None:
    cities = [
        {"nom": "Lyon", "latitude": 45.748, "longitude": 4.847},
        {"nom": "Ville sans latitude", "latitude": None, "longitude": 4.900},
        {"nom": "Ville sans longitude", "latitude": 45.800, "longitude": None},
    ]

    user_profile = {
        "ville_reference": "Lyon",
        "rayon_km": 20,
    }

    result = apply_reference_city_filter(
        city_dicts=cities,
        user_profile=user_profile,
        reference_coords=LYON_COORDS,
    )

    assert [city["nom"] for city in result] == ["Lyon"]


def test_apply_reference_city_filter_returns_original_list_when_no_reference_coords() -> None:
    cities = [
        {"nom": "Lyon", "latitude": 45.748, "longitude": 4.847},
    ]

    user_profile = {
        "ville_reference": "Ville inconnue",
        "rayon_km": 20,
    }

    result = apply_reference_city_filter(
        city_dicts=cities,
        user_profile=user_profile,
        reference_coords=None,
    )

    assert result == cities


def test_apply_reference_city_filter_uses_default_radius_when_radius_is_invalid() -> None:
    cities = [
        {"nom": "Lyon", "latitude": 45.748, "longitude": 4.847},
        {"nom": "Villeurbanne", "latitude": 45.766, "longitude": 4.880},
        {"nom": "Grenoble", "latitude": 45.188, "longitude": 5.724},
    ]

    user_profile = {
        "ville_reference": "Lyon",
        "rayon_km": "invalid",
    }

    result = apply_reference_city_filter(
        city_dicts=cities,
        user_profile=user_profile,
        reference_coords=LYON_COORDS,
    )

    names = [city["nom"] for city in result]

    assert "Lyon" in names
    assert "Villeurbanne" in names
    assert "Grenoble" not in names


def test_apply_budget_surface_estimate_adds_surface_when_budget_and_price_exist() -> None:
    cities = [
        {"nom": "Ville abordable", "prix_immo_m2": 2_000},
        {"nom": "Ville chère", "prix_immo_m2": 4_000},
    ]

    user_profile = {
        "budget_immobilier": 200_000,
    }

    result = apply_budget_surface_estimate(cities, user_profile)

    assert result[0]["surface_estimable_m2"] == 100.0
    assert result[1]["surface_estimable_m2"] == 50.0


def test_apply_budget_surface_estimate_sets_none_when_price_is_missing_or_invalid() -> None:
    cities = [
        {"nom": "Prix manquant", "prix_immo_m2": None},
        {"nom": "Prix zéro", "prix_immo_m2": 0},
        {"nom": "Prix invalide", "prix_immo_m2": "abc"},
    ]

    user_profile = {
        "budget_immobilier": 200_000,
    }

    result = apply_budget_surface_estimate(cities, user_profile)

    assert result[0]["surface_estimable_m2"] is None
    assert result[1]["surface_estimable_m2"] is None
    assert result[2]["surface_estimable_m2"] is None


def test_apply_budget_surface_estimate_returns_original_list_when_budget_is_missing() -> None:
    cities = [
        {"nom": "Ville", "prix_immo_m2": 2_000},
    ]

    result = apply_budget_surface_estimate(cities, user_profile={})

    assert result == cities


def test_apply_budget_surface_estimate_returns_original_list_when_budget_is_invalid() -> None:
    cities = [
        {"nom": "Ville", "prix_immo_m2": 2_000},
    ]

    result = apply_budget_surface_estimate(
        city_dicts=cities,
        user_profile={"budget_immobilier": "not-a-number"},
    )

    assert result == cities


@pytest.mark.parametrize(
    ("population", "expected_category"),
    [
        (5_000, "village / très petite ville"),
        (10_000, "petite ville"),
        (49_999, "petite ville"),
        (50_000, "ville moyenne"),
        (149_999, "ville moyenne"),
        (150_000, "grande ville"),
        (1_000_000, "grande ville"),
        (None, "village / très petite ville"),
        ("invalid", "village / très petite ville"),
    ],
)
def test_add_population_category(population, expected_category: str) -> None:
    cities = [
        {
            "nom": "Ville test",
            "population": population,
        }
    ]

    result = add_population_category(cities)

    assert result[0]["taille_ville"] == expected_category


def test_postprocessing_functions_do_not_mutate_input_dicts() -> None:
    cities = [
        {
            "nom": "Lyon",
            "latitude": 45.748,
            "longitude": 4.847,
            "prix_immo_m2": 2_000,
            "population": 500_000,
        }
    ]

    original_city = cities[0].copy()

    filtered = apply_reference_city_filter(
        city_dicts=cities,
        user_profile={"ville_reference": "Lyon", "rayon_km": 10},
        reference_coords=LYON_COORDS,
    )

    with_surface = apply_budget_surface_estimate(
        city_dicts=cities,
        user_profile={"budget_immobilier": 200_000},
    )

    with_category = add_population_category(cities)

    assert cities[0] == original_city
    assert filtered[0] is not cities[0]
    assert with_surface[0] is not cities[0]
    assert with_category[0] is not cities[0]