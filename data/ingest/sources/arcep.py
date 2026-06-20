"""
data/ingest/sources/arcep.py
────────────────────────────
Fibre communale réelle depuis ARCEP Ma Connexion Internet.

Ce module charge le fichier communal ARCEP et extrait un taux fibre utilisable
par CityMatch lorsque la donnée est réellement disponible.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from data.ingest.config import ARCEP_LABEL
from data.ingest.utils import console, download_cached, read_csv_flexible, to_float


ARCEP_FIBRE_URL = (
    "https://data.arcep.fr/fixe/maconnexioninternet/statistiques/last/"
    "commune/commune_debit_filaire.csv"
)

ARCEP_CACHE_FILENAME = "arcep_commune_fibre_last.csv"

CODE_COLUMN_CANDIDATES = (
    "code_insee",
    "code_commune",
    "codgeo",
    "commune",
    "code_insee_commune",
)

DIRECT_PERCENT_KEYWORDS = (
    "taux",
    "pct",
    "pourcentage",
    "couverture",
    "eligibilite",
    "éligibilité",
)

FIBRE_KEYWORDS = (
    "fibre",
    "ftth",
)

ELIGIBILITY_PROXY_COLUMNS = (
    "elig_thd1g",
    "elig_thd100",
    "elig_thd30",
)


def load_arcep_fibre() -> pd.DataFrame:
    """
    Charge le fichier communal ARCEP fibre.

    Retourne un DataFrame vide si la source n'est pas disponible.
    """
    path = download_cached(
        ARCEP_FIBRE_URL,
        ARCEP_CACHE_FILENAME,
    )

    if path is None:
        console.print("[yellow]⚠️  ARCEP fibre non chargée — fibre_pct restera NULL[/yellow]")
        return pd.DataFrame()

    dataframe = read_csv_flexible(path)

    if dataframe.empty:
        console.print("[yellow]⚠️  ARCEP fibre illisible — fibre_pct restera NULL[/yellow]")
        return pd.DataFrame()

    dataframe.columns = [str(column).strip() for column in dataframe.columns]

    console.print(
        f"[green]✅ ARCEP fibre {ARCEP_LABEL} : {len(dataframe):,} lignes[/green]"
    )

    return dataframe


def _normalize_insee_code(value: Any) -> str:
    """
    Normalise un code INSEE.

    Gère :
    - les codes numériques lus comme float : 75056.0 ;
    - les zéros initiaux : 01053 ;
    - la Corse : 2A004 / 2B033.
    """
    raw = str(value or "").strip().upper()

    if not raw:
        return ""

    raw = re.sub(r"\.0$", "", raw)
    raw = raw.replace(" ", "")

    if raw.startswith(("2A", "2B")):
        return raw

    if raw.isdigit():
        return raw.zfill(5)

    return raw


def _lower_column_map(dataframe: pd.DataFrame) -> dict[str, str]:
    """Construit un mapping nom_colonne_minuscule -> nom_colonne_original."""
    return {
        str(column).strip().lower(): str(column).strip()
        for column in dataframe.columns
    }


def _find_column(dataframe: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    """Trouve la première colonne existante parmi plusieurs noms possibles."""
    column_map = _lower_column_map(dataframe)

    for candidate in candidates:
        column = column_map.get(candidate.lower())

        if column:
            return column

    return None


def _filter_rows_by_code(
    dataframe: pd.DataFrame,
    code_column: str,
    code_insee: str,
) -> pd.DataFrame:
    """Filtre le DataFrame ARCEP sur une commune."""
    target_code = _normalize_insee_code(code_insee)

    if not target_code:
        return pd.DataFrame()

    normalized_codes = dataframe[code_column].map(_normalize_insee_code)

    return dataframe[normalized_codes == target_code]


def _to_percentage(value: Any) -> float | None:
    """
    Convertit une valeur en pourcentage.

    Accepte :
    - taux 0-1 ;
    - pourcentage 0-100.
    """
    number = to_float(value)

    if number is None:
        return None

    if 0 <= number <= 1:
        return round(number * 100, 1)

    if 1 < number <= 100:
        return round(number, 1)

    return None


def _clean_percentage(value: float | None) -> float | None:
    """Borne un pourcentage entre 0 et 100."""
    if value is None:
        return None

    return round(min(100.0, max(0.0, float(value))), 1)


def _candidate_columns(
    dataframe: pd.DataFrame,
    include_keywords: tuple[str, ...],
    also_include_keywords: tuple[str, ...] | None = None,
    exclude_keywords: tuple[str, ...] = (),
) -> list[str]:
    """Retourne les colonnes dont le nom correspond à certains mots-clés."""
    columns: list[str] = []

    for column in dataframe.columns:
        column_lower = str(column).lower()

        if exclude_keywords and any(keyword in column_lower for keyword in exclude_keywords):
            continue

        if not any(keyword in column_lower for keyword in include_keywords):
            continue

        if also_include_keywords and not any(
            keyword in column_lower for keyword in also_include_keywords
        ):
            continue

        columns.append(column)

    return columns


def _extract_direct_fibre_percentage(rows: pd.DataFrame) -> float | None:
    """
    Extrait un taux fibre direct si une colonne explicite existe.

    Exemple de colonne possible :
    - taux_fibre ;
    - pct_ftth ;
    - couverture_fibre ;
    - eligibilite_ftth.
    """
    pct_columns = _candidate_columns(
        dataframe=rows,
        include_keywords=FIBRE_KEYWORDS,
        also_include_keywords=DIRECT_PERCENT_KEYWORDS,
    )

    for column in pct_columns:
        values = rows[column].map(_to_percentage).dropna()

        if values.empty:
            continue

        return _clean_percentage(float(values.median()))

    return None


def _extract_long_format_fibre_percentage(rows: pd.DataFrame) -> float | None:
    """
    Extrait la fibre dans le format long ARCEP.

    Format attendu :
    - une colonne type ;
    - une colonne nbr ;
    - plusieurs lignes par commune.

    Calcul :
        fibre_pct = nbr lignes fibre / somme nbr tous types * 100
    """
    column_map = _lower_column_map(rows)

    type_column = column_map.get("type")
    count_column = column_map.get("nbr")

    if not type_column or not count_column:
        return None

    work = rows[[type_column, count_column]].copy()
    work["_type"] = work[type_column].astype(str).str.lower()
    work["_count"] = work[count_column].map(to_float)

    total = work["_count"].dropna().sum()

    if total <= 0:
        return None

    fibre_mask = work["_type"].str.contains(
        r"fibre|ftth|fibre optique",
        regex=True,
        na=False,
    )

    fibre_total = work.loc[fibre_mask, "_count"].dropna().sum()

    if fibre_total <= 0:
        return None

    return _clean_percentage(fibre_total / total * 100)


def _extract_eligibility_proxy(rows: pd.DataFrame) -> float | None:
    """
    Utilise les colonnes d'éligibilité très haut débit comme fallback.

    Ce fallback reste une donnée ARCEP réelle, mais ce n'est pas strictement
    identique à un taux FTTH si le fichier ne fournit pas explicitement la fibre.
    """
    column_map = _lower_column_map(rows)
    count_column = column_map.get("nbr")

    for column_name in ELIGIBILITY_PROXY_COLUMNS:
        column = column_map.get(column_name)

        if not column:
            continue

        values = rows[column].map(to_float).dropna()

        if values.empty:
            continue

        median_value = float(values.median())
        pct_value = _to_percentage(median_value)

        if pct_value is not None:
            return pct_value

        if count_column:
            total_count = rows[count_column].map(to_float).dropna().sum()

            if total_count > 0 and 0 <= median_value <= total_count * 1.05:
                return _clean_percentage(median_value / total_count * 100)

    return None


def _extract_ratio_fibre_percentage(rows: pd.DataFrame) -> float | None:
    """
    Extrait un taux fibre à partir de colonnes numérateur / dénominateur.

    Exemple :
        locaux_ftth / locaux_total * 100
    """
    numerator_columns = _candidate_columns(
        dataframe=rows,
        include_keywords=FIBRE_KEYWORDS,
        also_include_keywords=(
            "locaux",
            "prises",
            "couverts",
            "eligible",
            "éligible",
            "raccordable",
        ),
    )

    denominator_columns = _candidate_columns(
        dataframe=rows,
        include_keywords=(
            "locaux",
            "prises",
            "logements",
        ),
        exclude_keywords=(
            "fibre",
            "ftth",
            "cuivre",
            "cable",
            "dsl",
        ),
    )

    if not numerator_columns or not denominator_columns:
        return None

    for numerator_column in numerator_columns:
        numerator_values = rows[numerator_column].map(to_float).dropna()

        if numerator_values.empty:
            continue

        numerator = float(numerator_values.sum())

        if numerator <= 0:
            continue

        for denominator_column in denominator_columns:
            denominator_values = rows[denominator_column].map(to_float).dropna()

            if denominator_values.empty:
                continue

            denominator = float(denominator_values.sum())

            if denominator <= 0:
                continue

            if numerator <= denominator * 1.05:
                return _clean_percentage(numerator / denominator * 100)

    return None


def extract_fibre_pct(arcep_df: pd.DataFrame, code_insee: str) -> float | None:
    """
    Retourne une vraie valeur ARCEP si elle est détectable, sinon None.

    Formats supportés :
    1. format direct avec une colonne de taux fibre / FTTH ;
    2. format long avec colonnes type + nbr ;
    3. colonnes d'éligibilité très haut débit ARCEP ;
    4. ratio numérateur / dénominateur.
    """
    if arcep_df.empty:
        return None

    dataframe = arcep_df.copy()
    dataframe.columns = [str(column).strip() for column in dataframe.columns]

    code_column = _find_column(dataframe, CODE_COLUMN_CANDIDATES)

    if not code_column:
        return None

    rows = _filter_rows_by_code(
        dataframe=dataframe,
        code_column=code_column,
        code_insee=code_insee,
    )

    if rows.empty:
        return None

    direct_pct = _extract_direct_fibre_percentage(rows)

    if direct_pct is not None:
        return direct_pct

    long_format_pct = _extract_long_format_fibre_percentage(rows)

    if long_format_pct is not None:
        return long_format_pct

    eligibility_pct = _extract_eligibility_proxy(rows)

    if eligibility_pct is not None:
        return eligibility_pct

    ratio_pct = _extract_ratio_fibre_percentage(rows)

    if ratio_pct is not None:
        return ratio_pct

    return None