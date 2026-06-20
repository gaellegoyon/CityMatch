"""
config/settings.py
──────────────────
Configuration centralisée du projet CityMatch.

Charge les variables d'environnement et expose les constantes globales :
- chemins projet ;
- clés API ;
- modèles LLM / embeddings ;
- base SQLite ;
- critères disponibles ;
- sources open data.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

from dotenv import load_dotenv


# ─────────────────────────────────────────────────────────────────────────────
# Chemins du projet
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR: Final[Path] = Path(__file__).resolve().parent.parent

DATA_DIR: Final[Path] = BASE_DIR / "data"
DB_DIR: Final[Path] = BASE_DIR / "db"
DOCS_DIR: Final[Path] = DATA_DIR / "docs"
PDF_DIR: Final[Path] = DATA_DIR / "pdfs"
VECTORSTORE_DIR: Final[Path] = BASE_DIR / "vectorstore"
REPORTS_DIR: Final[Path] = BASE_DIR / "reports" / "output"

DB_PATH: Final[Path] = DB_DIR / "cities.db"


def ensure_project_directories() -> None:
    """Crée les répertoires nécessaires au fonctionnement de l'application."""
    for directory in [DATA_DIR, DB_DIR, DOCS_DIR, PDF_DIR, VECTORSTORE_DIR, REPORTS_DIR]:
        directory.mkdir(parents=True, exist_ok=True)


ensure_project_directories()


# ─────────────────────────────────────────────────────────────────────────────
# Chargement .env
# ─────────────────────────────────────────────────────────────────────────────
load_dotenv(BASE_DIR / ".env")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers env
# ─────────────────────────────────────────────────────────────────────────────
def _get_env_int(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    """Lit un entier depuis l'environnement avec valeur par défaut robuste."""
    raw_value = os.getenv(name)

    try:
        value = int(raw_value) if raw_value not in (None, "") else default
    except ValueError:
        value = default

    if minimum is not None:
        value = max(minimum, value)

    if maximum is not None:
        value = min(maximum, value)

    return value


def _get_env_str(name: str, default: str = "") -> str:
    """Lit une chaîne depuis l'environnement."""
    return os.getenv(name, default).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Clés API
# ─────────────────────────────────────────────────────────────────────────────
GOOGLE_API_KEY: Final[str] = _get_env_str("GOOGLE_API_KEY")
GROQ_API_KEY: Final[str] = _get_env_str("GROQ_API_KEY")
TAVILY_API_KEY: Final[str] = _get_env_str("TAVILY_API_KEY")

PLACEHOLDER_API_KEYS: Final[frozenset[str]] = frozenset(
    {
        "",
        "your_google_api_key_here",
        "your_groq_api_key_here",
        "your_tavily_api_key_here",
        "changeme",
        "change_me",
        "none",
        "null",
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Modèles LLM
# ─────────────────────────────────────────────────────────────────────────────
# Ces constantes doivent être utilisées par les agents LLM pour éviter les
# modèles hardcodés dans le code métier.
GROQ_MODEL: Final[str] = _get_env_str("GROQ_MODEL", "llama-3.3-70b-versatile")
GEMINI_MODEL: Final[str] = _get_env_str("GEMINI_MODEL", "gemini-2.0-flash")

# Compatibilité avec d'anciens imports éventuels.
PRIMARY_MODEL: Final[str] = _get_env_str("PRIMARY_MODEL", GEMINI_MODEL)
FALLBACK_MODEL: Final[str] = _get_env_str("FALLBACK_MODEL", GROQ_MODEL)


# ─────────────────────────────────────────────────────────────────────────────
# Base de données
# ─────────────────────────────────────────────────────────────────────────────
DATABASE_URL: Final[str] = _get_env_str("DATABASE_URL", f"sqlite:///{DB_PATH}")


# ─────────────────────────────────────────────────────────────────────────────
# Embeddings locaux
# ─────────────────────────────────────────────────────────────────────────────
EMBEDDING_MODEL: Final[str] = _get_env_str(
    "EMBEDDING_MODEL",
    "paraphrase-multilingual-MiniLM-L12-v2",
)


# ─────────────────────────────────────────────────────────────────────────────
# Identité de l'application
# ─────────────────────────────────────────────────────────────────────────────
APP_NAME: Final[str] = "CityMatch"
APP_VERSION: Final[str] = "1.0"


# ─────────────────────────────────────────────────────────────────────────────
# Paramètres de l'application
# ─────────────────────────────────────────────────────────────────────────────
MAX_CITIES_IN_REPORT: Final[int] = _get_env_int(
    "MAX_CITIES_IN_REPORT",
    default=10,
    minimum=1,
    maximum=50,
)

MIN_POPULATION: Final[int] = _get_env_int(
    "MIN_POPULATION",
    default=5_000,
    minimum=0,
    maximum=2_000_000,
)

MAX_POPULATION: Final[int] = _get_env_int(
    "MAX_POPULATION",
    default=500_000,
    minimum=1_000,
    maximum=2_000_000,
)

if MIN_POPULATION > MAX_POPULATION:
    MIN_POPULATION, MAX_POPULATION = MAX_POPULATION, MIN_POPULATION


# ─────────────────────────────────────────────────────────────────────────────
# Critères disponibles avec métadonnées
# ─────────────────────────────────────────────────────────────────────────────
# Important :
# - cette liste doit rester synchronisée avec agents/common/criteria.py ;
# - ne pas ajouter de critère sans colonne fiable en DB ou calcul fiable à la volée ;
# - les critères supprimés volontairement ne doivent pas apparaître ici
#   pour éviter que l'UI ou le LLM les propose.
AVAILABLE_CRITERIA: Final[dict[str, dict[str, object]]] = {
    # INSEE — économie / démographie
    "revenu_median": {
        "label": "Niveau de vie médian",
        "unit": "€/an",
        "description": "Niveau de vie médian INSEE",
        "lower_is_better": False,
        "source": "INSEE FiLoSoFi / Comparateur",
    },
    "taux_chomage": {
        "label": "Taux de chômage",
        "unit": "%",
        "description": "Part des actifs au chômage",
        "lower_is_better": True,
        "source": "INSEE RP",
    },
    "age_median": {
        "label": "Âge médian",
        "unit": "ans",
        "description": "Âge médian estimé depuis les classes d'âge",
        "lower_is_better": True,
        "source": "INSEE RP",
    },
    "pct_moins_15_ans": {
        "label": "Moins de 15 ans",
        "unit": "%",
        "description": "Part de population jeune",
        "lower_is_better": False,
        "source": "INSEE RP",
    },
    "pct_plus_65_ans": {
        "label": "Plus de 65 ans",
        "unit": "%",
        "description": "Part de seniors",
        "lower_is_better": False,
        "source": "INSEE RP",
    },
    "taux_natalite": {
        "label": "Natalité",
        "unit": "‰",
        "description": "Naissances pour 1000 habitants",
        "lower_is_better": False,
        "source": "INSEE état civil",
    },
    "evolution_population_pct": {
        "label": "Évolution population",
        "unit": "%",
        "description": "Évolution récente de la population",
        "lower_is_better": False,
        "source": "INSEE RP",
    },
    "nb_entreprises": {
        "label": "Établissements actifs",
        "unit": "nb",
        "description": "Nombre d'établissements actifs",
        "lower_is_better": False,
        "source": "INSEE comparateur",
    },
    "entreprises_pour_1000": {
        "label": "Établissements / 1000 hab.",
        "unit": "pour 1000 hab.",
        "description": "Densité d'établissements actifs",
        "lower_is_better": False,
        "source": "INSEE comparateur",
    },

    # Logement
    "prix_immo_m2": {
        "label": "Prix immobilier",
        "unit": "€/m²",
        "description": "Prix médian au m²",
        "lower_is_better": True,
        "source": "DVF",
    },
    "taux_logements_vacants": {
        "label": "Logements vacants",
        "unit": "%",
        "description": "Part de logements vacants",
        "lower_is_better": True,
        "source": "INSEE RP",
    },

    # Sécurité
    "score_securite": {
        "label": "Sécurité",
        "unit": "/10",
        "description": "Score sécurité agrégé",
        "lower_is_better": False,
        "source": "SSMSI",
    },
    "criminalite_pour_1000": {
        "label": "Criminalité",
        "unit": "faits/1000 hab.",
        "description": "Faits enregistrés pour 1000 habitants",
        "lower_is_better": True,
        "source": "SSMSI",
    },
    "cambriolages_pour_1000": {
        "label": "Cambriolages",
        "unit": "pour 1000 hab.",
        "description": "Cambriolages pour 1000 habitants",
        "lower_is_better": True,
        "source": "SSMSI",
    },
    "violences_physiques_pour_1000": {
        "label": "Violences physiques",
        "unit": "pour 1000 hab.",
        "description": "Violences physiques pour 1000 habitants",
        "lower_is_better": True,
        "source": "SSMSI",
    },

    # Localisation
    "distance_mer_km": {
        "label": "Distance à la mer",
        "unit": "km",
        "description": "Distance au littoral le plus proche",
        "lower_is_better": True,
        "source": "Calcul GPS",
    },
    "distance_montagne_km": {
        "label": "Distance à la montagne",
        "unit": "km",
        "description": "Distance à une zone montagneuse de référence",
        "lower_is_better": True,
        "source": "Calcul GPS",
    },

    # Connectivité / air / climat
    "fibre_pct": {
        "label": "Fibre",
        "unit": "%",
        "description": "Taux fibre réel ARCEP si disponible",
        "lower_is_better": False,
        "source": "ARCEP",
    },
    "qualite_air_score": {
        "label": "Qualité de l'air",
        "unit": "/10",
        "description": "Score air communal si disponible",
        "lower_is_better": False,
        "source": "ATMO",
    },
    "ensoleillement_h_an": {
        "label": "Ensoleillement",
        "unit": "h/an",
        "description": "Ensoleillement annuel orientatif",
        "lower_is_better": False,
        "source": "Météo-France normales",
    },
    "temperature_moyenne": {
        "label": "Température moyenne",
        "unit": "°C",
        "description": "Température moyenne annuelle orientative",
        "lower_is_better": False,
        "source": "Météo-France normales",
    },
    "precipitations_mm": {
        "label": "Précipitations",
        "unit": "mm/an",
        "description": "Précipitations annuelles orientatives",
        "lower_is_better": True,
        "source": "Météo-France normales",
    },
    "score_climat": {
        "label": "Score climat",
        "unit": "/10",
        "description": "Score climat orientatif",
        "lower_is_better": False,
        "source": "Météo-France normales",
    },

    # Équipements / services — BPE INSEE
    "creches_pour_1000": {
        "label": "Crèches",
        "unit": "pour 1000 hab.",
        "description": "Accueil jeune enfant par habitant",
        "lower_is_better": False,
        "source": "BPE INSEE",
    },
    "ecoles_pour_1000_enfants": {
        "label": "Écoles primaires",
        "unit": "pour 1000 enfants",
        "description": "Écoles primaires par enfant",
        "lower_is_better": False,
        "source": "BPE INSEE",
    },
    "nb_lycees_pour_1000_ados": {
        "label": "Lycées",
        "unit": "pour 1000 ados",
        "description": "Lycées par adolescent estimé",
        "lower_is_better": False,
        "source": "BPE INSEE",
    },
    "medecins_pour_1000": {
        "label": "Médecins généralistes",
        "unit": "pour 1000 hab.",
        "description": "Médecins généralistes par habitant",
        "lower_is_better": False,
        "source": "BPE INSEE",
    },
    "medecins_specialistes_pour_1000": {
        "label": "Médecins spécialistes",
        "unit": "pour 1000 hab.",
        "description": "Dentistes, ophtalmologues, pédiatres",
        "lower_is_better": False,
        "source": "BPE INSEE",
    },
    "nb_pharmacies_pour_1000": {
        "label": "Pharmacies",
        "unit": "pour 1000 hab.",
        "description": "Pharmacies par habitant",
        "lower_is_better": False,
        "source": "BPE INSEE",
    },
    "supermarches_pour_1000": {
        "label": "Supermarchés",
        "unit": "pour 1000 hab.",
        "description": "Supermarchés/hypermarchés par habitant",
        "lower_is_better": False,
        "source": "BPE INSEE",
    },
    "score_restauration": {
        "label": "Restauration",
        "unit": "/10",
        "description": "Densité de restaurants",
        "lower_is_better": False,
        "source": "BPE INSEE",
    },
    "transport_score": {
        "label": "Transports",
        "unit": "/10",
        "description": "Score basé sur les gares BPE",
        "lower_is_better": False,
        "source": "BPE INSEE",
    },
}


AVAILABLE_CRITERIA_KEYS: Final[frozenset[str]] = frozenset(AVAILABLE_CRITERIA)


# ─────────────────────────────────────────────────────────────────────────────
# URLs Open Data
# ─────────────────────────────────────────────────────────────────────────────
DATA_SOURCES: Final[dict[str, str]] = {
    "bpe": "https://www.insee.fr/fr/statistiques/fichier/8217527/DS_BPE_CSV_FR.zip",
    "chomage": "https://www.data.gouv.fr/fr/datasets/r/7df61db5-6a83-4c02-92fd-c8a51f8cef44",
    "dvf": "https://files.data.gouv.fr/geo-dvf/latest/csv/",
    "criminalite": "https://www.data.gouv.fr/fr/datasets/r/5d01a5f1-4d69-4fba-be7e-71b1d05db487",
    "insee_communes": "https://www.data.gouv.fr/fr/datasets/r/dbe8a621-a9c4-4bc3-9cae-be1699c5ff25",
}