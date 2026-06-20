"""
agents/database/repository.py
─────────────────────────────
Fonctions d'accès à la base de données CityMatch.

Ce module centralise les requêtes SQLAlchemy utilisées par le DatabaseAgent
et par l'interface Streamlit.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from agents.common.geo import normalize_place_name
from db.models import City, SessionLocal


def query_cities_by_filter(
    db: Session,
    where_clause: str,
    params: dict[str, Any],
) -> list[City]:
    """
    Exécute une requête de sélection de villes à partir d'un WHERE SQL paramétré.

    Le WHERE est construit en amont par agents.database.filters.build_sql_filter().
    """
    return db.query(City).filter(text(where_clause).bindparams(**params)).all()


def serialize_cities(cities: list[City]) -> list[dict[str, Any]]:
    """
    Convertit les objets City SQLAlchemy en dictionnaires.

    Les valeurs NULL sont conservées telles quelles. Elles ne doivent pas être
    remplacées par 0, car le scoring gère les valeurs manquantes avec une
    stratégie neutre.
    """
    return [city.to_dict() for city in cities]


def find_city_coordinates_by_name(
    db: Session,
    city_name: str | None,
) -> tuple[float, float] | None:
    """
    Retourne les coordonnées GPS d'une ville depuis la base.

    La recherche tente d'abord une correspondance exacte normalisée, puis une
    correspondance partielle. Cette stratégie est acceptable ici car la base
    contient un volume limité de communes.
    """
    normalized_target = normalize_place_name(city_name)

    if not normalized_target:
        return None

    candidates = db.query(City).filter(
        City.nom.isnot(None),
        City.latitude.isnot(None),
        City.longitude.isnot(None),
    ).all()

    for city in candidates:
        if normalize_place_name(city.nom) == normalized_target:
            return float(city.latitude), float(city.longitude)

    for city in candidates:
        normalized_city_name = normalize_place_name(city.nom)
        if normalized_target in normalized_city_name or normalized_city_name in normalized_target:
            return float(city.latitude), float(city.longitude)

    return None


def get_city_details_by_name(city_name: str) -> dict[str, Any] | None:
    """
    Retourne les détails complets d'une ville par son nom.

    Utilisé par l'interface Streamlit pour l'affichage de détails.
    """
    normalized_target = normalize_place_name(city_name)

    if not normalized_target:
        return None

    db = SessionLocal()

    try:
        cities = db.query(City).filter(City.nom.isnot(None)).all()

        for city in cities:
            if normalize_place_name(city.nom) == normalized_target:
                return city.to_dict()

        for city in cities:
            normalized_city_name = normalize_place_name(city.nom)
            if normalized_target in normalized_city_name or normalized_city_name in normalized_target:
                return city.to_dict()

        return None

    finally:
        db.close()


def get_all_regions() -> list[str]:
    """Retourne la liste unique des régions disponibles dans la base."""
    db = SessionLocal()

    try:
        regions = db.query(City.region).distinct().all()
        return sorted(region for (region,) in regions if region)

    finally:
        db.close()


def get_stats_summary() -> dict[str, int]:
    """Retourne des statistiques globales sur la base de données."""
    db = SessionLocal()

    try:
        total_cities = db.query(City).count()
        total_regions = db.query(func.count(func.distinct(City.region))).scalar() or 0

        return {
            "total_cities": total_cities,
            "total_regions": total_regions,
        }

    finally:
        db.close()