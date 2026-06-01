"""
data/ingest/config.py

Constantes de l'ingestion CityMatch.

Ce module ne contient pas de logique métier : uniquement les chemins,
années de référence, URLs et champs conservés.
"""

from pathlib import Path
from config.settings import DATA_DIR

CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

HTTP_TIMEOUT = 120
CURRENT_YEAR = 2026

BPE_YEAR = 2024
CRIME_YEAR = 2025
DVF_YEARS = [2025, 2024]
DNB_SESSION = 2025

ARCEP_LABEL = "T4-2025"
ARCEP_DATA_DATE = "2025-12-31"

# Départements sans DVF dans geo-dvf national.
DEPARTEMENTS_SANS_DVF = {"57", "67", "68", "97", "98", "976"}

# Champs conservés dans la table City.
# Tout champ non listé ici est explicitement exclu de l'ingestion pour éviter
# les critères trop estimés, saturés, non discriminants ou biaisés.
KEPT_CITY_FIELDS = {
    "code_insee", "nom", "departement", "region", "population", "latitude", "longitude",
    "taux_chomage", "revenu_median", "age_median", "pct_moins_15_ans", "pct_plus_65_ans",
    "taux_natalite", "evolution_population_pct", "nb_entreprises", "entreprises_pour_1000",
    "nb_creches", "nb_ecoles_primaires", "nb_colleges", "nb_lycees",
    "nb_medecins_generalistes", "nb_pharmacies", "nb_hopitaux", "nb_gares",
    "nb_piscines", "nb_bibliotheques", "nb_supermarches", "nb_restaurants",
    "nb_equipements_sportifs", "nb_cinemas", "nb_dentistes", "nb_ophtalmologues",
    "nb_pediatres", "nb_urgences",
    "creches_pour_1000", "medecins_pour_1000", "medecins_specialistes_pour_1000",
    "nb_pharmacies_pour_1000", "ecoles_pour_1000_enfants", "nb_lycees_pour_1000_ados",
    "supermarches_pour_1000", "score_restauration", "transport_score",
    "prix_immo_m2", "taux_logements_vacants",
    "criminalite_pour_1000", "cambriolages_pour_1000",
    "violences_physiques_pour_1000",
    "score_securite",
    "fibre_pct",
    "qualite_air_score",
    "ensoleillement_h_an",
    "temperature_moyenne",
    "precipitations_mm",
    "score_climat",
"distance_mer_km", "distance_montagne_km",
}
