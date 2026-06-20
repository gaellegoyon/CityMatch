"""
data/ingest/sources/crime.py
────────────────────────────
Chargement et extraction des indicateurs SSMSI communaux.

Les indicateurs sont utilisés par CityMatch pour alimenter :
- cambriolages_pour_1000 ;
- violences_physiques_pour_1000 ;
- criminalite_pour_1000 ;
- score_securite.

Aucune estimation géographique n'est faite ici : si la commune n'est pas trouvée
ou si la source est indisponible, les valeurs restent NULL.
"""

from __future__ import annotations

import gzip
import re
from pathlib import Path
from typing import Any, Final

import pandas as pd

from data.ingest.config import CACHE_DIR, CRIME_YEAR
from data.ingest.utils import console, download_cached, read_csv_flexible, to_float


CrimeResult = dict[str, float | None]

CRIME_RESOURCES: Final[tuple[tuple[str, str], ...]] = (
    (
        "https://www.data.gouv.fr/api/1/datasets/r/44ef4323-1097-48d5-8719-3c544b55d294",
        "criminalite_commune_2016_2025.csv.gz",
    ),
    (
        "https://www.data.gouv.fr/api/1/datasets/r/604d71b8-337d-4869-9226-49e01bae87df",
        "criminalite_commune_2016_2025.parquet",
    ),
)

EMPTY_CRIME_RESULT: Final[CrimeResult] = {
    "cambriolages_pour_1000": None,
    "violences_physiques_pour_1000": None,
    "criminalite_pour_1000": None,
    "score_securite": None,
}

CODE_COLUMN_CANDIDATES: Final[tuple[str, ...]] = (
    "CODGEO_2025",
    "CODGEO",
    "code_insee",
    "Code commune",
    "code_commune",
    "commune",
)

INDICATOR_COLUMN_CANDIDATES: Final[tuple[str, ...]] = (
    "indicateur",
    "Indicateur",
    "classe",
    "Classe",
    "libelle_indicateur",
    "libellé_indicateur",
    "libelle",
    "libellé",
)

YEAR_COLUMN_CANDIDATES: Final[tuple[str, ...]] = (
    "annee",
    "Année",
    "ANNEE",
    "year",
    "Year",
    "annee_ref",
    "millésime",
    "millesime",
)

RATE_COLUMN_CANDIDATES: Final[tuple[str, ...]] = (
    "taux_pour_mille",
    "taux pour mille",
    "taux_pour_1000",
    "taux",
)

COUNT_COLUMN_CANDIDATES: Final[tuple[str, ...]] = (
    "nombre",
    "Nombre",
    "faits",
    "nb_faits",
    "nb",
    "effectif",
)

DIFFUSED_COLUMN_CANDIDATES: Final[tuple[str, ...]] = (
    "est_diffuse",
    "est_diffusé",
    "diffuse",
    "diffusé",
    "diffusion",
)


def _empty_result() -> CrimeResult:
    """Retourne un résultat vide indépendant."""
    return dict(EMPTY_CRIME_RESULT)


def _normalize_text(value: Any) -> str:
    """Normalise légèrement une chaîne pour comparaison."""
    text = str(value or "").strip().lower()
    text = text.replace("é", "e").replace("è", "e").replace("ê", "e")
    text = text.replace("à", "a").replace("ù", "u").replace("ç", "c")
    return " ".join(text.split())


def _normalize_insee_code(value: Any) -> str:
    """
    Normalise un code INSEE.

    Gère :
    - 75056 ;
    - 75056.0 ;
    - 01053 ;
    - 2A004 / 2B033.
    """
    raw = str(value or "").strip().upper()
    raw = re.sub(r"\.0$", "", raw)
    raw = raw.replace(" ", "")

    if not raw:
        return ""

    if re.fullmatch(r"2[AB]\d{3}", raw):
        return raw

    if re.fullmatch(r"\d{1,5}", raw):
        return raw.zfill(5)

    match = re.search(r"\b(\d{5})\b", raw)
    if match:
        return match.group(1)

    return ""


def _lower_column_map(dataframe: pd.DataFrame) -> dict[str, str]:
    """Construit un mapping nom_colonne_minuscule -> nom_colonne_original."""
    return {
        str(column).strip().lower(): str(column).strip()
        for column in dataframe.columns
    }


def _find_column(dataframe: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    """Trouve une colonne parmi plusieurs noms possibles."""
    lower_map = _lower_column_map(dataframe)

    for candidate in candidates:
        column = lower_map.get(candidate.lower())

        if column:
            return column

    return None


def _read_gzip_csv(path: Path) -> pd.DataFrame:
    """Décompresse un CSV gzip SSMSI dans le cache puis le lit."""
    csv_path = CACHE_DIR / "criminalite_commune_2016_2025.csv"

    if not csv_path.exists() or csv_path.stat().st_size == 0:
        temp_path = csv_path.with_suffix(csv_path.suffix + ".part")

        try:
            with gzip.open(path, "rb") as source:
                temp_path.write_bytes(source.read())

            temp_path.replace(csv_path)

        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    return read_csv_flexible(csv_path)


def _read_crime_resource(path: Path, filename: str) -> pd.DataFrame:
    """Lit une ressource criminalité selon son format."""
    suffixes = "".join(path.suffixes).lower()
    filename_lower = filename.lower()

    if filename_lower.endswith(".parquet") or suffixes.endswith(".parquet"):
        return pd.read_parquet(path)

    if filename_lower.endswith(".gz") or suffixes.endswith(".gz"):
        return _read_gzip_csv(path)

    return read_csv_flexible(path)


def load_criminalite() -> pd.DataFrame:
    """
    Charge les données communales SSMSI.

    Retourne un DataFrame vide si aucune ressource n'est exploitable.
    """
    for url, filename in CRIME_RESOURCES:
        path = download_cached(url, filename)

        if path is None:
            continue

        try:
            dataframe = _read_crime_resource(path, filename)

            if not dataframe.empty:
                dataframe.columns = [str(column).strip().replace("\ufeff", "") for column in dataframe.columns]
                console.print(f"[green]✅ Criminalité SSMSI : {len(dataframe):,} lignes[/green]")
                return dataframe

        except Exception as exc:
            console.print(f"[yellow]⚠️  Criminalité ressource {filename}: {exc}[/yellow]")

    console.print("[yellow]⚠️  Données criminalité non disponibles[/yellow]")
    return pd.DataFrame()


def _filter_by_code(
    dataframe: pd.DataFrame,
    code_column: str,
    code_insee: str,
) -> pd.DataFrame:
    """Filtre le DataFrame sur une commune."""
    target_code = _normalize_insee_code(code_insee)

    if not target_code:
        return pd.DataFrame()

    normalized_codes = dataframe[code_column].map(_normalize_insee_code)

    return dataframe[normalized_codes == target_code].copy()


def _extract_year(value: Any) -> int | None:
    """Extrait une année depuis une valeur."""
    text = str(value or "")
    match = re.search(r"(19\d{2}|20\d{2}|21\d{2})", text)

    if not match:
        return None

    try:
        return int(match.group(1))
    except ValueError:
        return None


def _filter_latest_year(dataframe: pd.DataFrame) -> pd.DataFrame:
    """
    Filtre sur CRIME_YEAR si disponible, sinon sur l'année disponible la plus proche.

    Le choix reste transparent : on n'invente pas de données, on sélectionne juste
    le millésime présent dans la source.
    """
    year_column = _find_column(dataframe, YEAR_COLUMN_CANDIDATES)

    if not year_column:
        return dataframe

    work = dataframe.copy()
    work["_crime_year"] = work[year_column].map(_extract_year)
    work = work.dropna(subset=["_crime_year"])

    if work.empty:
        return dataframe

    target_rows = work[work["_crime_year"] == CRIME_YEAR]

    if not target_rows.empty:
        return target_rows.drop(columns=["_crime_year"])

    previous_rows = work[work["_crime_year"] == CRIME_YEAR - 1]

    if not previous_rows.empty:
        return previous_rows.drop(columns=["_crime_year"])

    available_years = sorted(int(year) for year in work["_crime_year"].dropna().unique())

    if not available_years:
        return dataframe

    closest_year = max((year for year in available_years if year <= CRIME_YEAR), default=max(available_years))

    return work[work["_crime_year"] == closest_year].drop(columns=["_crime_year"])


def _filter_diffused_rows(dataframe: pd.DataFrame) -> pd.DataFrame:
    """
    Garde les lignes diffusées si la colonne existe.

    Si le filtrage supprimerait tout, on conserve le DataFrame initial.
    """
    diffused_column = _find_column(dataframe, DIFFUSED_COLUMN_CANDIDATES)

    if not diffused_column:
        return dataframe

    truthy_values = {"true", "1", "oui", "vrai", "yes", "y"}
    filtered = dataframe[
        dataframe[diffused_column]
        .astype(str)
        .str.strip()
        .str.lower()
        .isin(truthy_values)
    ]

    return filtered if not filtered.empty else dataframe


def _indicator_text(row: pd.Series, indicator_column: str) -> str:
    """Retourne le libellé d'indicateur normalisé."""
    return _normalize_text(row.get(indicator_column, ""))


def _matches_any_pattern(text: str, patterns: tuple[str, ...]) -> bool:
    """Indique si un texte contient au moins un motif."""
    return any(pattern in text for pattern in patterns)


def _values_to_rate_per_1000(
    rows: pd.DataFrame,
    rate_column: str | None,
    count_column: str | None,
    population: int,
) -> float | None:
    """
    Convertit des lignes SSMSI en taux pour 1000 habitants.

    Priorité :
    1. colonne taux déjà fournie ;
    2. colonne nombre / faits divisée par la population.
    """
    if rows.empty:
        return None

    if rate_column:
        values = rows[rate_column].map(to_float).dropna()

        if not values.empty:
            return round(float(values.sum()), 2)

    if count_column and population > 0:
        values = rows[count_column].map(to_float).dropna()

        if not values.empty:
            return round(float(values.sum()) / max(population / 1000.0, 0.001), 2)

    return None


def _get_rate_for_patterns(
    dataframe: pd.DataFrame,
    indicator_column: str,
    patterns: tuple[str, ...],
    rate_column: str | None,
    count_column: str | None,
    population: int,
) -> float | None:
    """Calcule un taux pour les indicateurs dont le libellé matche les motifs."""
    mask = dataframe.apply(
        lambda row: _matches_any_pattern(_indicator_text(row, indicator_column), patterns),
        axis=1,
    )

    rows = dataframe[mask]

    return _values_to_rate_per_1000(
        rows=rows,
        rate_column=rate_column,
        count_column=count_column,
        population=population,
    )


def _compute_total_criminality_rate(
    dataframe: pd.DataFrame,
    indicator_column: str,
    rate_column: str | None,
    count_column: str | None,
    population: int,
) -> float | None:
    """
    Calcule un total de criminalité pour 1000 habitants.

    Si une ligne globale existe, elle est utilisée.
    Sinon, on somme les indicateurs disponibles par libellé distinct pour éviter
    de compter plusieurs fois une même classe en cas de doublons.
    """
    global_patterns = (
        "ensemble",
        "total",
        "crimes et delits",
        "delinquance",
    )

    global_rate = _get_rate_for_patterns(
        dataframe=dataframe,
        indicator_column=indicator_column,
        patterns=global_patterns,
        rate_column=rate_column,
        count_column=count_column,
        population=population,
    )

    if global_rate is not None and global_rate > 0:
        return global_rate

    if dataframe.empty:
        return None

    work = dataframe.copy()
    work["_indicator_norm"] = work[indicator_column].map(_normalize_text)

    if rate_column:
        work["_rate"] = work[rate_column].map(to_float)
    elif count_column and population > 0:
        work["_rate"] = work[count_column].map(to_float) / max(population / 1000.0, 0.001)
    else:
        return None

    work = work.dropna(subset=["_indicator_norm", "_rate"])
    work = work[work["_indicator_norm"] != ""]
    work = work[work["_rate"] > 0]

    if work.empty:
        return None

    by_indicator = work.groupby("_indicator_norm")["_rate"].median()
    total = float(by_indicator.sum())

    return round(total, 1) if total > 0 else None


def _security_score_from_criminality(criminality_rate: float | None) -> float | None:
    """Transforme un taux de criminalité pour 1000 en score sécurité 0-10."""
    if criminality_rate is None:
        return None

    return round(max(0.0, min(10.0, 10.0 - criminality_rate / 15.0)), 1)


def extract_criminalite(
    crime_df: pd.DataFrame,
    code_insee: str,
    population: int,
) -> CrimeResult:
    """
    Extrait les indicateurs SSMSI pour une commune.

    Retourne des valeurs None quand la donnée réelle n'est pas disponible.
    """
    result = _empty_result()

    if crime_df.empty or not population or population <= 0:
        return result

    dataframe = crime_df.copy()
    dataframe.columns = [str(column).strip().replace("\ufeff", "") for column in dataframe.columns]

    code_column = _find_column(dataframe, CODE_COLUMN_CANDIDATES)
    indicator_column = _find_column(dataframe, INDICATOR_COLUMN_CANDIDATES)

    if not code_column or not indicator_column:
        return result

    commune_rows = _filter_by_code(
        dataframe=dataframe,
        code_column=code_column,
        code_insee=code_insee,
    )

    if commune_rows.empty:
        return result

    commune_rows = _filter_latest_year(commune_rows)
    commune_rows = _filter_diffused_rows(commune_rows)

    if commune_rows.empty:
        return result

    rate_column = _find_column(commune_rows, RATE_COLUMN_CANDIDATES)
    count_column = _find_column(commune_rows, COUNT_COLUMN_CANDIDATES)

    result["cambriolages_pour_1000"] = _get_rate_for_patterns(
        dataframe=commune_rows,
        indicator_column=indicator_column,
        patterns=("cambriol",),
        rate_column=rate_column,
        count_column=count_column,
        population=population,
    )

    result["violences_physiques_pour_1000"] = _get_rate_for_patterns(
        dataframe=commune_rows,
        indicator_column=indicator_column,
        patterns=("violences physiques", "coups et blessures"),
        rate_column=rate_column,
        count_column=count_column,
        population=population,
    )

    total_criminality = _compute_total_criminality_rate(
        dataframe=commune_rows,
        indicator_column=indicator_column,
        rate_column=rate_column,
        count_column=count_column,
        population=population,
    )

    result["criminalite_pour_1000"] = total_criminality
    result["score_securite"] = _security_score_from_criminality(total_criminality)

    return result
