"""
ui/services/city_loader.py
──────────────────────────
Chargement des villes depuis la base pour l'onglet Explorer.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Final

import streamlit as st


logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS: Final[int] = 300

CITY_EXPLORER_FIELDS: Final[tuple[str, ...]] = (
    "code_insee",
    "nom",
    "region",
    "departement",
    "latitude",
    "longitude",
    "population",
    "distance_mer_km",
    "distance_montagne_km",
    "prix_immo_m2",
    "taux_chomage",
    "revenu_median",
    "age_median",
    "taux_logements_vacants",
    "score_securite",
    "criminalite_pour_1000",
    "score_climat",
    "ensoleillement_h_an",
    "temperature_moyenne",
    "precipitations_mm",
    "qualite_air_score",
    "fibre_pct",
    "transport_score",
    "score_restauration",
    "entreprises_pour_1000",
)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def load_all_cities() -> list[dict[str, Any]]:
    """
    Charge toutes les villes utiles à l'exploration.

    Le cache est court pour refléter une nouvelle ingestion sans redémarrer
    Streamlit trop souvent.

    Retour :
        Liste de dictionnaires sérialisables, avec uniquement les villes ayant
        des coordonnées exploitables.
    """
    from db.models import City, SessionLocal

    db = SessionLocal()

    try:
        columns = [getattr(City, field) for field in CITY_EXPLORER_FIELDS]

        rows = (
            db.query(*columns)
            .filter(City.latitude.isnot(None), City.longitude.isnot(None))
            .order_by(City.nom.asc())
            .all()
        )

        return [_serialize_city_row(row) for row in rows]

    except Exception as exc:
        logger.exception("Impossible de charger les villes pour l'onglet Explorer : %s", exc)
        return []

    finally:
        db.close()


def clear_city_cache() -> None:
    """Vide le cache Streamlit du chargement des villes."""
    load_all_cities.clear()


def _serialize_city_row(row: Any) -> dict[str, Any]:
    """Sérialise une ligne SQLAlchemy en dictionnaire compatible UI."""
    mapping = row._mapping if hasattr(row, "_mapping") else {}

    city = {
        "code_insee": str(mapping.get("code_insee") or ""),
        "nom": str(mapping.get("nom") or ""),
        "region": str(mapping.get("region") or ""),
        "departement": str(mapping.get("departement") or ""),
        "latitude": _to_float_or_none(mapping.get("latitude")),
        "longitude": _to_float_or_none(mapping.get("longitude")),
        "population": _to_int(mapping.get("population"), default=0),
    }

    for field in CITY_EXPLORER_FIELDS:
        if field in city:
            continue

        city[field] = _to_float_or_none(mapping.get(field))

    return city


def _to_float_or_none(value: Any) -> float | None:
    """Convertit une valeur en float JSON-compatible ou None."""
    if value is None:
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


def _to_int(value: Any, default: int = 0) -> int:
    """Convertit une valeur en int avec fallback."""
    if value is None:
        return default

    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return default

    return number