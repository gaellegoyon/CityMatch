"""
config/settings.py
─────────────────
Configuration centralisée du projet CityMatch.
Charge les variables d'environnement et expose les constantes globales.
"""

import os
from pathlib import Path

from dotenv import load_dotenv


# ─── Chemins du projet ────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_DIR = BASE_DIR / "db"
VECTORSTORE_DIR = BASE_DIR / "vectorstore"
REPORTS_DIR = BASE_DIR / "reports" / "output"
PDF_DIR = DATA_DIR / "pdfs"

# Créer les répertoires si nécessaire
for directory in [DATA_DIR, DB_DIR, VECTORSTORE_DIR, REPORTS_DIR, PDF_DIR]:
    directory.mkdir(parents=True, exist_ok=True)


# ─── Chargement .env ──────────────────────────────────────────────────────────
load_dotenv(BASE_DIR / ".env")


# ─── Clés API ─────────────────────────────────────────────────────────────────
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")


# ─── Modèles LLM ──────────────────────────────────────────────────────────────
PRIMARY_MODEL = os.getenv("PRIMARY_MODEL", "gemini-2.0-flash-exp")
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "llama-3.3-70b-versatile")


# ─── Base de données ──────────────────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_DIR}/cities.db")


# ─── Embeddings locaux ────────────────────────────────────────────────────────
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"


# ─── Identité de l'application ────────────────────────────────────────────────
APP_NAME = "CityMatch"
APP_VERSION = "1.0"


# ─── Paramètres de l'application ──────────────────────────────────────────────
MAX_CITIES_IN_REPORT = int(os.getenv("MAX_CITIES_IN_REPORT", "10"))
MIN_POPULATION = 5_000
MAX_POPULATION = 500_000


# ─── Critères disponibles avec métadonnées ────────────────────────────────────
# Important :
# - cette liste doit rester synchronisée avec agents/common/criteria.py ;
# - ne pas ajouter de critère sans colonne fiable en DB ou calcul fiable à la volée ;
# - les critères supprimés volontairement ne doivent pas apparaître ici
#   pour éviter que l'UI ou le LLM les propose.
AVAILABLE_CRITERIA = {
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


# ─── URLs Open Data ───────────────────────────────────────────────────────────
DATA_SOURCES = {
    "bpe": "https://www.insee.fr/fr/statistiques/fichier/8217527/DS_BPE_CSV_FR.zip",
    "chomage": "https://www.data.gouv.fr/fr/datasets/r/7df61db5-6a83-4c02-92fd-c8a51f8cef44",
    "dvf": "https://files.data.gouv.fr/geo-dvf/latest/csv/",
    "criminalite": "https://www.data.gouv.fr/fr/datasets/r/5d01a5f1-4d69-4fba-be7e-71b1d05db487",
    "insee_communes": "https://www.data.gouv.fr/fr/datasets/r/dbe8a621-a9c4-4bc3-9cae-be1699c5ff25",
}
