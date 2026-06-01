"""
data/ingest/sources/climate.py

Indicateurs climatiques orientatifs par région.
"""

from __future__ import annotations

CLIMAT_PAR_REGION = {
    "Île-de-France": {"ensoleillement": 1800, "temp_moy": 11.5, "precipitations": 620},
    "Auvergne-Rhône-Alpes": {"ensoleillement": 2100, "temp_moy": 11.0, "precipitations": 900},
    "Bourgogne-Franche-Comté": {"ensoleillement": 1850, "temp_moy": 10.5, "precipitations": 780},
    "Bretagne": {"ensoleillement": 1900, "temp_moy": 12.0, "precipitations": 830},
    "Centre-Val de Loire": {"ensoleillement": 1900, "temp_moy": 11.5, "precipitations": 640},
    "Grand Est": {"ensoleillement": 1750, "temp_moy": 10.5, "precipitations": 700},
    "Hauts-de-France": {"ensoleillement": 1600, "temp_moy": 10.5, "precipitations": 700},
    "Normandie": {"ensoleillement": 1700, "temp_moy": 11.0, "precipitations": 800},
    "Nouvelle-Aquitaine": {"ensoleillement": 2100, "temp_moy": 13.5, "precipitations": 850},
    "Occitanie": {"ensoleillement": 2500, "temp_moy": 14.5, "precipitations": 700},
    "Pays de la Loire": {"ensoleillement": 2000, "temp_moy": 12.5, "precipitations": 700},
    "Provence-Alpes-Côte d'Azur": {"ensoleillement": 2800, "temp_moy": 15.0, "precipitations": 600},
    "Corse": {"ensoleillement": 2900, "temp_moy": 16.0, "precipitations": 700},
}



def get_climat(region: str) -> dict:
    base = CLIMAT_PAR_REGION.get(region)
    if not base:
        return {"ensoleillement_h_an": None, "temperature_moyenne": None, "precipitations_mm": None, "score_climat": None}

    score = (
        min(10, base["ensoleillement"] / 280) * 0.45
        + min(10, base["temp_moy"] / 1.8) * 0.35
        + max(0, min(10, 10 - base["precipitations"] / 120)) * 0.20
    )
    return {
        "ensoleillement_h_an": float(base["ensoleillement"]),
        "temperature_moyenne": float(base["temp_moy"]),
        "precipitations_mm": float(base["precipitations"]),
        "score_climat": round(float(score), 1),
    }
