"""
data/ingest/config.py
─────────────────────
Constantes de l'ingestion CityMatch.

Ce module ne contient pas de logique métier : uniquement les chemins,
années de référence, URLs, sources disponibles et champs conservés.
"""

from __future__ import annotations

from typing import Final

from config.settings import DATA_DIR


# ─────────────────────────────────────────────────────────────────────────────
# Chemins
# ─────────────────────────────────────────────────────────────────────────────
CACHE_DIR: Final = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Paramètres généraux
# ─────────────────────────────────────────────────────────────────────────────
HTTP_TIMEOUT: Final[int] = 120
CURRENT_YEAR: Final[int] = 2026

INGEST_SOURCE_CHOICES: Final[tuple[str, ...]] = (
    "all",
    "bpe",
    "crime",
    "dvf",
    "insee",
    "arcep",
    "air",
    "climat",
)


# ─────────────────────────────────────────────────────────────────────────────
# Millésimes de données
# ─────────────────────────────────────────────────────────────────────────────
BPE_YEAR: Final[int] = 2024
CRIME_YEAR: Final[int] = 2025
DVF_YEARS: Final[tuple[int, ...]] = (2025, 2024)

# À conserver uniquement si ton pipeline ingère réellement les résultats DNB.
DNB_SESSION: Final[int] = 2025

ARCEP_LABEL: Final[str] = "T4-2025"
ARCEP_DATA_DATE: Final[str] = "2025-12-31"


# ─────────────────────────────────────────────────────────────────────────────
# Particularités sources
# ─────────────────────────────────────────────────────────────────────────────
# Départements sans DVF dans geo-dvf national.
DEPARTEMENTS_SANS_DVF: Final[frozenset[str]] = frozenset(
    {
        "57",
        "67",
        "68",
        "97",
        "98",
        "976",
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Champs conservés dans la table City
# ─────────────────────────────────────────────────────────────────────────────
# Tout champ non listé ici est explicitement exclu de l'ingestion pour éviter
# les critères trop estimés, saturés, non discriminants ou biaisés.
#
# Important :
# - cette liste doit rester synchronisée avec db.models.City ;
# - les champs bruts peuvent être conservés même s'ils ne sont pas scorés ;
# - seuls les critères présents dans config.settings.AVAILABLE_CRITERIA doivent
#   être proposés au LLM, à l'UI ou au ScoringAgent.
KEPT_CITY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        # Identité / géographie
        "code_insee",
        "nom",
        "departement",
        "region",
        "population",
        "latitude",
        "longitude",

        # INSEE — économie / démographie
        "taux_chomage",
        "revenu_median",
        "age_median",
        "pct_moins_15_ans",
        "pct_plus_65_ans",
        "taux_natalite",
        "evolution_population_pct",
        "nb_entreprises",
        "entreprises_pour_1000",

        # BPE — champs bruts
        "nb_creches",
        "nb_ecoles_primaires",
        "nb_colleges",
        "nb_lycees",
        "nb_medecins_generalistes",
        "nb_pharmacies",
        "nb_hopitaux",
        "nb_gares",
        "nb_piscines",
        "nb_bibliotheques",
        "nb_supermarches",
        "nb_restaurants",
        "nb_equipements_sportifs",
        "nb_cinemas",
        "nb_dentistes",
        "nb_ophtalmologues",
        "nb_pediatres",
        "nb_urgences",

        # BPE — ratios / scores utilisés ou exploitables
        "creches_pour_1000",
        "medecins_pour_1000",
        "medecins_specialistes_pour_1000",
        "nb_pharmacies_pour_1000",
        "ecoles_pour_1000_enfants",
        "nb_lycees_pour_1000_ados",
        "supermarches_pour_1000",
        "score_restauration",
        "transport_score",

        # Immobilier
        "prix_immo_m2",
        "taux_logements_vacants",

        # Sécurité
        "criminalite_pour_1000",
        "cambriolages_pour_1000",
        "violences_physiques_pour_1000",
        "score_securite",

        # Connectivité
        "fibre_pct",

        # Air / climat
        "qualite_air_score",
        "ensoleillement_h_an",
        "temperature_moyenne",
        "precipitations_mm",
        "score_climat",

        # Distances calculées
        "distance_mer_km",
        "distance_montagne_km",
    }
)