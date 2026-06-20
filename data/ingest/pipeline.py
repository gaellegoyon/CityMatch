"""
data/ingest/pipeline.py
───────────────────────
Assemblage des sources et construction d'un dictionnaire City par commune.
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from data.ingest.config import KEPT_CITY_FIELDS
from data.ingest.geo import distance_to_mountain_km, distance_to_sea_km
from data.ingest.sources.arcep import extract_fibre_pct
from data.ingest.sources.atmo import extract_qualite_air_score
from data.ingest.sources.bpe import extract_bpe_for_commune
from data.ingest.sources.climate import get_climat
from data.ingest.sources.crime import extract_criminalite
from data.ingest.sources.dvf import compute_prix_immo
from data.ingest.sources.insee import extract_demo_indicators, extract_rp_indicators


IngestResult = tuple[str, str, dict[str, Any] | None, str | None]

DEFAULT_POPULATION_FALLBACK = 50_000
DEFAULT_CHILDREN_PCT = 0.15
DEFAULT_TEEN_PCT = 0.05


def _to_float(value: Any) -> float | None:
    """Convertit une valeur en float fini."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


def _to_int(value: Any) -> int | None:
    """Convertit une valeur en entier positif ou nul."""
    number = _to_float(value)

    if number is None:
        return None

    return max(0, int(number))


def _round_or_none(value: Any, digits: int = 1) -> float | None:
    """Arrondit une valeur numérique si elle est valide."""
    number = _to_float(value)

    if number is None:
        return None

    return round(number, digits)


def _safe_ratio(
    numerator: Any,
    denominator: Any,
    multiplier: float = 1_000.0,
    digits: int = 3,
) -> float | None:
    """Calcule un ratio robuste."""
    num = _to_float(numerator)
    den = _to_float(denominator)

    if num is None or den is None or den <= 0:
        return None

    return round(num / den * multiplier, digits)


def _department_from_code(code_insee: str, fallback_department: str | None = None) -> str:
    """Déduit le département depuis le code INSEE, avec prise en charge Corse."""
    code = str(code_insee or "").strip()
    fallback = str(fallback_department or "").strip()

    if code.startswith(("2A", "2B")):
        return code[:2]

    if len(code) >= 2 and code[:2].isdigit():
        return code[:2]

    return fallback


def _filter_kept_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Ne conserve que les champs autorisés pour City."""
    return {
        key: value
        for key, value in data.items()
        if key in KEPT_CITY_FIELDS
    }


def _is_missing(value: Any) -> bool:
    """Détermine si une valeur doit être considérée comme manquante."""
    if value is None:
        return True

    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def apply_postprocess_fallbacks(results: list[IngestResult]) -> list[IngestResult]:
    """
    Fallbacks post-traitement limités aux champs fiables mais parfois incomplets.

    Principes :
    - prix_immo_m2 : fallback médiane département, puis région, puis nationale ;
    - score_securite : fallback médiane région, puis nationale ;
    - pas de fallback air : qualite_air_score reste NULL si aucune vraie source ATMO ;
    - pas de fallback fibre : fibre_pct reste une vraie donnée ARCEP ;
    - conversion numérique explicite avant les médianes pandas.
    """
    ok_items = [
        (code, nom, data, error)
        for code, nom, data, error in results
        if error is None and data
    ]

    if not ok_items:
        return results

    df = pd.DataFrame([data for _, _, data, _ in ok_items])

    def fill_by_group(field: str, groups: list[str]) -> None:
        if field not in df.columns:
            return

        df[field] = pd.to_numeric(df[field], errors="coerce")

        for group in groups:
            if group not in df.columns:
                continue

            medians = df.groupby(group, dropna=True)[field].median()

            for _, _, data, _ in ok_items:
                if not _is_missing(data.get(field)):
                    continue

                group_value = data.get(group)

                if group_value is None or group_value not in medians.index:
                    continue

                median_value = medians.get(group_value)

                if pd.notna(median_value):
                    data[field] = round(float(median_value), 1)

            df[field] = pd.to_numeric(
                [data.get(field) for _, _, data, _ in ok_items],
                errors="coerce",
            )

        national_values = df[field].dropna()

        if national_values.empty:
            return

        national_median = float(national_values.median())

        for _, _, data, _ in ok_items:
            if _is_missing(data.get(field)):
                data[field] = round(national_median, 1)

    fill_by_group("prix_immo_m2", ["departement", "region"])
    fill_by_group("score_securite", ["region"])

    cleaned_results: list[IngestResult] = []

    for code, nom, data, error in results:
        if error is None and data:
            cleaned_results.append(
                (
                    code,
                    nom,
                    _filter_kept_fields(data),
                    error,
                )
            )
        else:
            cleaned_results.append((code, nom, data, error))

    return cleaned_results


def _add_base_identity(
    city: dict[str, Any],
    code_insee: str,
    nom: str,
    departement: str,
    region: str,
    lat: Any,
    lon: Any,
) -> None:
    """Ajoute les champs d'identité et de géographie de base."""
    city.update(
        {
            "code_insee": str(code_insee),
            "nom": str(nom),
            "departement": str(departement),
            "region": str(region),
            "latitude": _round_or_none(lat, 6),
            "longitude": _round_or_none(lon, 6),
        }
    )


def _add_insee_indicators(
    city: dict[str, Any],
    communes_df: pd.DataFrame,
    code_insee: str,
) -> int:
    """Ajoute les indicateurs INSEE et retourne une population sûre."""
    rp = extract_rp_indicators(communes_df, code_insee)

    population = _to_int(rp.get("population"))

    if population is None or population <= 0:
        population = DEFAULT_POPULATION_FALLBACK

    city.update(
        {
            "population": population,
            "taux_chomage": _round_or_none(rp.get("taux_chomage"), 2),
            "age_median": _round_or_none(rp.get("age_median"), 1),
            "revenu_median": _round_or_none(rp.get("revenu_median"), 0),
            "taux_logements_vacants": _round_or_none(
                rp.get("taux_logements_vacants"),
                2,
            ),
            "pct_moins_15_ans": _round_or_none(rp.get("pct_moins_15_ans"), 2),
            "pct_plus_65_ans": _round_or_none(rp.get("pct_plus_65_ans"), 2),
            "nb_entreprises": _to_int(rp.get("nb_entreprises")),
            "entreprises_pour_1000": _round_or_none(
                rp.get("entreprises_pour_1000"),
                3,
            ),
        }
    )

    return population


def _add_bpe_indicators(
    city: dict[str, Any],
    bpe_df: pd.DataFrame,
    code_insee: str,
    population: int,
) -> None:
    """Ajoute les équipements BPE et leurs ratios."""
    bpe = extract_bpe_for_commune(bpe_df, code_insee)

    raw_fields = [
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
    ]

    for field in raw_fields:
        city[field] = _to_int(bpe.get(field)) or 0

    children_pct = _to_float(city.get("pct_moins_15_ans"))

    if children_pct is None or children_pct <= 0:
        children_pct = DEFAULT_CHILDREN_PCT
    elif children_pct > 1:
        children_pct = children_pct / 100

    children_count = max(int(population * children_pct), 1)
    teen_count = max(int(population * DEFAULT_TEEN_PCT), 1)

    city["creches_pour_1000"] = _safe_ratio(city["nb_creches"], population)
    city["medecins_pour_1000"] = _safe_ratio(
        city["nb_medecins_generalistes"],
        population,
    )
    city["supermarches_pour_1000"] = _safe_ratio(city["nb_supermarches"], population)
    city["nb_pharmacies_pour_1000"] = _safe_ratio(city["nb_pharmacies"], population)

    specialists = (
        city["nb_dentistes"]
        + city["nb_ophtalmologues"]
        + city["nb_pediatres"]
    )

    city["medecins_specialistes_pour_1000"] = _safe_ratio(
        specialists,
        population,
    )

    city["ecoles_pour_1000_enfants"] = _safe_ratio(
        city["nb_ecoles_primaires"],
        children_count,
    )

    city["nb_lycees_pour_1000_ados"] = _safe_ratio(
        city["nb_lycees"],
        teen_count,
    )

    restaurants_per_1000 = _safe_ratio(city["nb_restaurants"], population)

    if restaurants_per_1000 is not None:
        city["score_restauration"] = round(min(10.0, restaurants_per_1000 * 2), 1)
    else:
        city["score_restauration"] = None

    city["transport_score"] = round(
        min(10.0, city["nb_gares"] * 3 + min(7, population // 40_000)),
        1,
    )


def _add_security_indicators(
    city: dict[str, Any],
    crime_df: pd.DataFrame,
    code_insee: str,
    population: int,
) -> None:
    """Ajoute les indicateurs de criminalité et de sécurité."""
    crime = extract_criminalite(crime_df, code_insee, population)
    city.update(crime)

    criminality = _to_float(city.get("criminalite_pour_1000"))

    if criminality is not None:
        city["score_securite"] = round(
            max(0.0, min(10.0, 10 - criminality / 15)),
            1,
        )
    else:
        city["score_securite"] = None


def _add_real_estate_indicators(
    city: dict[str, Any],
    code_insee: str,
    departement: str,
) -> None:
    """Ajoute les indicateurs immobiliers DVF."""
    city["prix_immo_m2"] = _round_or_none(
        compute_prix_immo(code_insee, departement),
        1,
    )


def _add_connectivity_and_air(
    city: dict[str, Any],
    arcep_df: pd.DataFrame,
    atmo_df: pd.DataFrame | None,
    code_insee: str,
) -> None:
    """Ajoute la fibre ARCEP et la qualité de l'air ATMO."""
    city["fibre_pct"] = _round_or_none(
        extract_fibre_pct(arcep_df, code_insee),
        2,
    )

    atmo_source = atmo_df if atmo_df is not None else pd.DataFrame()

    city["qualite_air_score"] = _round_or_none(
        extract_qualite_air_score(atmo_source, code_insee),
        2,
    )


def _add_climate_indicators(city: dict[str, Any], region: str) -> None:
    """Ajoute les indicateurs climatiques régionaux ou orientatifs."""
    climate = get_climat(region)

    for key, value in climate.items():
        city[key] = _round_or_none(value, 2)


def _add_geographic_indicators(city: dict[str, Any], lat: Any, lon: Any) -> None:
    """Ajoute les distances mer/montagne."""
    distance_mer = distance_to_sea_km(lat, lon)
    distance_montagne = distance_to_mountain_km(lat, lon)

    city["distance_mer_km"] = (
        round(distance_mer, 1)
        if math.isfinite(distance_mer)
        else None
    )

    city["distance_montagne_km"] = (
        round(distance_montagne, 1)
        if math.isfinite(distance_montagne)
        else None
    )


def build_commune_data(
    code_insee: str,
    nom: str,
    dept: str,
    region: str,
    lat: Any,
    lon: Any,
    bpe_df: pd.DataFrame,
    crime_df: pd.DataFrame,
    arcep_df: pd.DataFrame,
    communes_df: pd.DataFrame,
    atmo_df: pd.DataFrame | None = None,
    no_api: bool = False,
) -> dict[str, Any]:
    """
    Construit uniquement les champs conservés dans le modèle simplifié.

    Les sources expérimentales ou trop estimées sont volontairement exclues :
    GBIF, pollution sols, risque sismique.

    ARCEP et ATMO ne sont utilisés que si une vraie donnée communale existe.
    """
    del no_api  # conservé pour compatibilité CLI ; aucune API externe ici.

    city: dict[str, Any] = {}
    department = _department_from_code(code_insee, fallback_department=dept)

    _add_base_identity(
        city=city,
        code_insee=code_insee,
        nom=nom,
        departement=department,
        region=region,
        lat=lat,
        lon=lon,
    )

    population = _add_insee_indicators(
        city=city,
        communes_df=communes_df,
        code_insee=code_insee,
    )

    _add_bpe_indicators(
        city=city,
        bpe_df=bpe_df,
        code_insee=code_insee,
        population=population,
    )

    _add_security_indicators(
        city=city,
        crime_df=crime_df,
        code_insee=code_insee,
        population=population,
    )

    _add_real_estate_indicators(
        city=city,
        code_insee=code_insee,
        departement=department,
    )

    city.update(
        extract_demo_indicators(
            communes_df,
            code_insee,
            population,
        )
    )

    _add_connectivity_and_air(
        city=city,
        arcep_df=arcep_df,
        atmo_df=atmo_df,
        code_insee=code_insee,
    )

    _add_climate_indicators(
        city=city,
        region=region,
    )

    _add_geographic_indicators(
        city=city,
        lat=lat,
        lon=lon,
    )

    return _filter_kept_fields(city)