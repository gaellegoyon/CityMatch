"""
data/ingest/sources/climate.py
──────────────────────────────
Indicateurs climatiques orientatifs par région.

Ces valeurs sont des ordres de grandeur régionaux utilisés pour enrichir
CityMatch lorsque l'utilisateur exprime une préférence climatique.

Important :
- ce module ne fournit pas une météo locale ;
- les valeurs ne doivent pas être présentées comme des mesures communales fines ;
- aucun critère "nature" n'est déduit automatiquement de ces données.
"""

from __future__ import annotations

import unicodedata
from typing import Final


ClimateValues = dict[str, float]
ClimateResult = dict[str, float | None]


CLIMAT_PAR_REGION: Final[dict[str, ClimateValues]] = {
    "Île-de-France": {
        "ensoleillement": 1800.0,
        "temp_moy": 11.5,
        "precipitations": 620.0,
    },
    "Auvergne-Rhône-Alpes": {
        "ensoleillement": 2100.0,
        "temp_moy": 11.0,
        "precipitations": 900.0,
    },
    "Bourgogne-Franche-Comté": {
        "ensoleillement": 1850.0,
        "temp_moy": 10.5,
        "precipitations": 780.0,
    },
    "Bretagne": {
        "ensoleillement": 1900.0,
        "temp_moy": 12.0,
        "precipitations": 830.0,
    },
    "Centre-Val de Loire": {
        "ensoleillement": 1900.0,
        "temp_moy": 11.5,
        "precipitations": 640.0,
    },
    "Grand Est": {
        "ensoleillement": 1750.0,
        "temp_moy": 10.5,
        "precipitations": 700.0,
    },
    "Hauts-de-France": {
        "ensoleillement": 1600.0,
        "temp_moy": 10.5,
        "precipitations": 700.0,
    },
    "Normandie": {
        "ensoleillement": 1700.0,
        "temp_moy": 11.0,
        "precipitations": 800.0,
    },
    "Nouvelle-Aquitaine": {
        "ensoleillement": 2100.0,
        "temp_moy": 13.5,
        "precipitations": 850.0,
    },
    "Occitanie": {
        "ensoleillement": 2500.0,
        "temp_moy": 14.5,
        "precipitations": 700.0,
    },
    "Pays de la Loire": {
        "ensoleillement": 2000.0,
        "temp_moy": 12.5,
        "precipitations": 700.0,
    },
    "Provence-Alpes-Côte d'Azur": {
        "ensoleillement": 2800.0,
        "temp_moy": 15.0,
        "precipitations": 600.0,
    },
    "Corse": {
        "ensoleillement": 2900.0,
        "temp_moy": 16.0,
        "precipitations": 700.0,
    },
}


REGION_ALIASES: Final[dict[str, str]] = {
    "ile de france": "Île-de-France",
    "idf": "Île-de-France",
    "auvergne rhone alpes": "Auvergne-Rhône-Alpes",
    "rhone alpes": "Auvergne-Rhône-Alpes",
    "bourgogne franche comte": "Bourgogne-Franche-Comté",
    "centre val de loire": "Centre-Val de Loire",
    "hauts de france": "Hauts-de-France",
    "haut de france": "Hauts-de-France",
    "nouvelle aquitaine": "Nouvelle-Aquitaine",
    "pays de la loire": "Pays de la Loire",
    "provence alpes cote d azur": "Provence-Alpes-Côte d'Azur",
    "paca": "Provence-Alpes-Côte d'Azur",
    "provence": "Provence-Alpes-Côte d'Azur",
    "cote d azur": "Provence-Alpes-Côte d'Azur",
}


EMPTY_CLIMATE_RESULT: Final[ClimateResult] = {
    "ensoleillement_h_an": None,
    "temperature_moyenne": None,
    "precipitations_mm": None,
    "score_climat": None,
}


def _normalize_text(value: str | None) -> str:
    """Normalise une chaîne pour comparaison robuste."""
    if not value:
        return ""

    text = str(value).strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("'", " ")
    text = text.replace("’", " ")
    text = text.replace("-", " ")

    return " ".join(text.split())


def _resolve_region_name(region: str | None) -> str | None:
    """Résout une région exacte ou un alias courant."""
    if not region:
        return None

    if region in CLIMAT_PAR_REGION:
        return region

    normalized = _normalize_text(region)

    if normalized in REGION_ALIASES:
        return REGION_ALIASES[normalized]

    for known_region in CLIMAT_PAR_REGION:
        if _normalize_text(known_region) == normalized:
            return known_region

    return None


def _clamp_score(value: float) -> float:
    """Borne un score entre 0 et 10."""
    return max(0.0, min(10.0, float(value)))


def _compute_climate_score(values: ClimateValues) -> float:
    """
    Calcule un score climatique indicatif sur 10.

    Pondération :
    - ensoleillement : 45 % ;
    - température moyenne : 35 % ;
    - faibles précipitations : 20 %.
    """
    sunshine_score = _clamp_score(values["ensoleillement"] / 280.0)
    temperature_score = _clamp_score(values["temp_moy"] / 1.8)
    precipitation_score = _clamp_score(10.0 - values["precipitations"] / 120.0)

    score = (
        sunshine_score * 0.45
        + temperature_score * 0.35
        + precipitation_score * 0.20
    )

    return round(score, 1)


def get_climat(region: str | None) -> ClimateResult:
    """
    Retourne les indicateurs climatiques orientatifs d'une région.

    Les clés retournées correspondent aux colonnes City :
    - ensoleillement_h_an ;
    - temperature_moyenne ;
    - precipitations_mm ;
    - score_climat.
    """
    resolved_region = _resolve_region_name(region)

    if resolved_region is None:
        return dict(EMPTY_CLIMATE_RESULT)

    values = CLIMAT_PAR_REGION[resolved_region]

    return {
        "ensoleillement_h_an": float(values["ensoleillement"]),
        "temperature_moyenne": float(values["temp_moy"]),
        "precipitations_mm": float(values["precipitations"]),
        "score_climat": _compute_climate_score(values),
    }