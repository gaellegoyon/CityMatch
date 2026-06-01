"""
agents/common/criteria.py
──────────────────────────
Critères CityMatch réellement disponibles dans db.models.City.

Ce module évite de dupliquer la whitelist entre user_profile_agent,
database_agent et scoring_agent.
"""

VALID_CRITERIA_KEYS = {
    "revenu_median", "taux_chomage", "age_median", "pct_moins_15_ans", "pct_plus_65_ans",
    "taux_natalite", "evolution_population_pct", "nb_entreprises", "entreprises_pour_1000",
    "prix_immo_m2", "taux_logements_vacants",
    "score_securite", "criminalite_pour_1000", "cambriolages_pour_1000", "violences_physiques_pour_1000",
    "distance_mer_km", "distance_montagne_km",
    "fibre_pct", "qualite_air_score", "ensoleillement_h_an", "temperature_moyenne", "precipitations_mm", "score_climat",
    "creches_pour_1000", "ecoles_pour_1000_enfants", "nb_lycees_pour_1000_ados",
    "medecins_pour_1000", "medecins_specialistes_pour_1000", "nb_pharmacies_pour_1000",
    "supermarches_pour_1000", "score_restauration", "transport_score",
}


def filter_valid_criteria(criteria: dict | None) -> tuple[dict, set[str]]:
    """Retourne (critères valides, critères ignorés)."""
    raw = criteria or {}
    valid = {k: v for k, v in raw.items() if k in VALID_CRITERIA_KEYS}
    ignored = set(raw) - VALID_CRITERIA_KEYS
    return valid, ignored
