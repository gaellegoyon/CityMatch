"""
agents/common/criteria.py
──────────────────────────
Critères CityMatch autorisés pour le filtrage, la recherche SQL et le scoring.

Cette whitelist centralisée évite de dupliquer les critères entre les agents
et limite l'influence du LLM à un périmètre métier contrôlé.
"""

from typing import Any, Final, Mapping


VALID_CRITERIA_KEYS: Final[frozenset[str]] = frozenset({
    "revenu_median", "taux_chomage", "age_median", "pct_moins_15_ans", "pct_plus_65_ans",
    "taux_natalite", "evolution_population_pct", "nb_entreprises", "entreprises_pour_1000",
    "prix_immo_m2", "taux_logements_vacants",
    "score_securite", "criminalite_pour_1000", "cambriolages_pour_1000", "violences_physiques_pour_1000",
    "distance_mer_km", "distance_montagne_km",
    "fibre_pct", "qualite_air_score", "ensoleillement_h_an", "temperature_moyenne", "precipitations_mm", "score_climat",
    "creches_pour_1000", "ecoles_pour_1000_enfants", "nb_lycees_pour_1000_ados",
    "medecins_pour_1000", "medecins_specialistes_pour_1000", "nb_pharmacies_pour_1000",
    "supermarches_pour_1000", "score_restauration", "transport_score",
})


def filter_valid_criteria(criteria: Mapping[str, Any] | None) -> tuple[dict[str, Any], set[str]]:
    """Retourne les critères autorisés et les critères ignorés."""
    raw = criteria or {}
    valid = {key: value for key, value in raw.items() if key in VALID_CRITERIA_KEYS}
    ignored = set(raw) - VALID_CRITERIA_KEYS
    return valid, ignored