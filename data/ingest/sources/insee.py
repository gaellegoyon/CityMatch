"""
data/ingest/sources/insee.py
────────────────────────────
Données INSEE communales :
- comparateur de territoires ;
- dossier complet ;
- indicateurs démographiques et socio-économiques.

Le module charge plusieurs sources INSEE, les fusionne par CODGEO, puis extrait
les indicateurs utiles à CityMatch.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any, Final

import pandas as pd

from data.ingest.config import CACHE_DIR
from data.ingest.utils import console, download_cached, read_csv_flexible, to_float


INSEE_SOURCES: Final[tuple[tuple[str, str, str], ...]] = (
    (
        "https://www.insee.fr/fr/statistiques/fichier/2521169/base_cc_comparateur_csv.zip",
        "base_cc_comparateur_csv.zip",
        "comparateur",
    ),
    (
        "https://www.insee.fr/fr/statistiques/fichier/5359146/dossier_complet.zip",
        "dossier_complet.zip",
        "dossier_complet",
    ),
)

CODE_COLUMN_CANDIDATES: Final[tuple[str, ...]] = (
    "CODGEO",
    "COM",
    "code_insee",
    "code_commune",
    "CODGEO_2024",
    "CODGEO_2025",
)

PLM_PREFIXES: Final[dict[str, str]] = {
    "75056": r"^751\d{2}$",
    "69123": r"^6938\d$",
    "13055": r"^132\d{2}$",
}

AGE_BINS_BY_YEAR: Final[tuple[tuple[tuple[str, int, int], ...], ...]] = (
    (
        ("P23_POP0014", 0, 14),
        ("P23_POP1529", 15, 29),
        ("P23_POP3044", 30, 44),
        ("P23_POP4559", 45, 59),
        ("P23_POP6074", 60, 74),
        ("P23_POP75P", 75, 95),
    ),
    (
        ("P22_POP0014", 0, 14),
        ("P22_POP1529", 15, 29),
        ("P22_POP3044", 30, 44),
        ("P22_POP4559", 45, 59),
        ("P22_POP6074", 60, 74),
        ("P22_POP75P", 75, 95),
    ),
    (
        ("P21_POP0014", 0, 14),
        ("P21_POP1529", 15, 29),
        ("P21_POP3044", 30, 44),
        ("P21_POP4559", 45, 59),
        ("P21_POP6074", 60, 74),
        ("P21_POP75P", 75, 95),
    ),
    (
        ("P20_POP0014", 0, 14),
        ("P20_POP1529", 15, 29),
        ("P20_POP3044", 30, 44),
        ("P20_POP4559", 45, 59),
        ("P20_POP6074", 60, 74),
        ("P20_POP75P", 75, 95),
    ),
)

EMPTY_RP_RESULT: Final[dict[str, float | int | None]] = {
    "population": None,
    "taux_chomage": None,
    "age_median": None,
    "revenu_median": None,
    "taux_logements_vacants": None,
    "pct_moins_15_ans": None,
    "pct_plus_65_ans": None,
    "nb_entreprises": None,
    "entreprises_pour_1000": None,
}

EMPTY_DEMO_RESULT: Final[dict[str, float | None]] = {
    "taux_natalite": None,
    "evolution_population_pct": None,
}


def _empty_rp_result() -> dict[str, float | int | None]:
    """Retourne un résultat RP vide indépendant."""
    return dict(EMPTY_RP_RESULT)


def _empty_demo_result() -> dict[str, float | None]:
    """Retourne un résultat démographique vide indépendant."""
    return dict(EMPTY_DEMO_RESULT)


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


def _normalize_code_series(series: pd.Series) -> pd.Series:
    """Normalise une colonne de codes INSEE."""
    return series.map(_normalize_insee_code)


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


def _clean_columns(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Nettoie les noms de colonnes INSEE."""
    dataframe = dataframe.copy()
    dataframe.columns = [
        str(column).strip().replace("\ufeff", "")
        for column in dataframe.columns
    ]
    dataframe = dataframe.dropna(axis=1, how="all")
    return dataframe


def _is_relevant_csv_file(file_name: str) -> bool:
    """Filtre les fichiers CSV exploitables dans une archive INSEE."""
    lower = file_name.lower()
    name = Path(file_name).name.lower()

    return (
        lower.endswith(".csv")
        and "__macosx" not in lower
        and not name.startswith("~$")
    )


def _sort_preferred_csv_files(csv_files: list[str]) -> list[str]:
    """Met en priorité les fichiers communaux/base comparateur."""
    preferred = [
        file_name
        for file_name in csv_files
        if any(
            token in Path(file_name).name.lower()
            for token in ["base_cc", "comparateur", "commune", "com"]
        )
    ]

    return preferred or csv_files


def _standardize_code_column(dataframe: pd.DataFrame) -> pd.DataFrame | None:
    """Renomme la colonne code en CODGEO et normalise les codes."""
    dataframe = _clean_columns(dataframe)
    code_column = _find_column(dataframe, CODE_COLUMN_CANDIDATES)

    if not code_column:
        return None

    if code_column != "CODGEO":
        dataframe = dataframe.rename(columns={code_column: "CODGEO"})

    dataframe["CODGEO"] = _normalize_code_series(dataframe["CODGEO"])
    dataframe = dataframe[dataframe["CODGEO"] != ""]
    dataframe = dataframe.drop_duplicates(subset=["CODGEO"])

    return dataframe


def _read_csv_from_zip_member(
    archive: zipfile.ZipFile,
    csv_file: str,
    extract_prefix: str,
    index: int,
) -> pd.DataFrame | None:
    """Extrait et lit un CSV depuis une archive INSEE."""
    raw_path = CACHE_DIR / f"_{extract_prefix}_extract_{index}_{Path(csv_file).name}"

    with archive.open(csv_file) as source:
        raw_path.write_bytes(source.read())

    dataframe = read_csv_flexible(raw_path)

    if dataframe.empty:
        return None

    return _standardize_code_column(dataframe)


def _load_insee_source(url: str, filename: str, label: str) -> pd.DataFrame | None:
    """Télécharge et charge une source INSEE."""
    path = download_cached(url, filename)

    if path is None:
        return None

    try:
        loaded_parts: list[pd.DataFrame] = []

        if filename.endswith(".zip"):
            with zipfile.ZipFile(path) as archive:
                csv_files = [
                    file_name
                    for file_name in archive.namelist()
                    if _is_relevant_csv_file(file_name)
                ]
                csv_files = _sort_preferred_csv_files(csv_files)

                for index, csv_file in enumerate(csv_files):
                    part = _read_csv_from_zip_member(
                        archive=archive,
                        csv_file=csv_file,
                        extract_prefix=label,
                        index=index,
                    )

                    if part is not None and not part.empty:
                        loaded_parts.append(part)
        else:
            dataframe = read_csv_flexible(path)
            part = _standardize_code_column(dataframe)

            if part is not None and not part.empty:
                loaded_parts.append(part)

        if not loaded_parts:
            return None

        source = _merge_dataframes_on_codgeo(loaded_parts)
        _print_source_diagnostics(source, label)

        return source

    except Exception as exc:
        console.print(f"[yellow]⚠️  INSEE {filename}: {exc}[/yellow]")
        return None


def _merge_dataframes_on_codgeo(dataframes: list[pd.DataFrame]) -> pd.DataFrame:
    """Fusionne plusieurs DataFrames INSEE sur CODGEO sans dupliquer les colonnes."""
    if not dataframes:
        return pd.DataFrame()

    merged = dataframes[0]

    for part in dataframes[1:]:
        new_columns = [
            column
            for column in part.columns
            if column == "CODGEO" or column not in merged.columns
        ]

        if len(new_columns) > 1:
            merged = merged.merge(part[new_columns], on="CODGEO", how="left")

    return merged


def _print_source_diagnostics(dataframe: pd.DataFrame, label: str) -> None:
    """Affiche un résumé court d'une source INSEE chargée."""
    revenue_columns = [
        column
        for column in dataframe.columns
        if column.upper().startswith(("MED", "DISP_MED")) or "NIVEAU" in column.upper()
    ]
    business_columns = [
        column
        for column in dataframe.columns
        if column.upper().startswith(("ETTOT", "ETTEF", "ETAZ", "ETBE", "ETFZ", "ETGU", "ETOQ"))
    ]

    console.print(
        f"[green]✅ INSEE {label} : {len(dataframe):,} communes, "
        f"{len(dataframe.columns):,} colonnes[/green]"
    )

    if revenue_columns:
        console.print(f"[dim]  Colonnes revenu : {revenue_columns[:12]}[/dim]")

    if business_columns:
        console.print(f"[dim]  Colonnes entreprises : {business_columns[:12]}[/dim]")


def load_dossier_complet() -> pd.DataFrame:
    """
    Charge et fusionne les données INSEE utiles au niveau communal.

    Sources :
    - base du comparateur de territoires ;
    - dossier complet.

    Retour :
        DataFrame avec une ligne par commune ou arrondissement municipal.
    """
    dataframes: list[pd.DataFrame] = []

    for url, filename, label in INSEE_SOURCES:
        source = _load_insee_source(
            url=url,
            filename=filename,
            label=label,
        )

        if source is not None and not source.empty:
            dataframes.append(source)

    if not dataframes:
        return pd.DataFrame()

    merged = _merge_dataframes_on_codgeo(dataframes)

    console.print(
        f"[green]✅ INSEE fusionné : {len(merged):,} communes, "
        f"{len(merged.columns):,} colonnes[/green]"
    )

    return merged


def _rows_for_code(
    dataframe: pd.DataFrame,
    code_column: str,
    code_insee: str,
) -> pd.DataFrame:
    """
    Retourne les lignes INSEE correspondant à une commune.

    Pour Paris, Lyon et Marseille, si la commune principale n'existe pas, on
    agrège les arrondissements municipaux.
    """
    code = _normalize_insee_code(code_insee)

    if not code:
        return pd.DataFrame()

    codes = _normalize_code_series(dataframe[code_column])
    rows = dataframe[codes == code]

    if not rows.empty:
        return rows

    pattern = PLM_PREFIXES.get(code)

    if pattern:
        return dataframe[codes.str.match(pattern, na=False)]

    return rows


def _column_values(
    rows: pd.DataFrame,
    dataframe: pd.DataFrame,
    candidates: list[str],
) -> tuple[str | None, pd.Series]:
    """Retourne les valeurs numériques de la première colonne candidate existante."""
    for column in candidates:
        if column in dataframe.columns:
            values = rows[column].map(to_float).dropna()
            return column, values

    return None, pd.Series(dtype=float)


def _get_sum(rows: pd.DataFrame, dataframe: pd.DataFrame, candidates: list[str]) -> float | None:
    """Somme les valeurs positives d'une colonne candidate."""
    _, values = _column_values(rows, dataframe, candidates)
    values = values[values > 0]

    return float(values.sum()) if not values.empty else None


def _get_weighted(
    rows: pd.DataFrame,
    dataframe: pd.DataFrame,
    value_candidates: list[str],
    weight_candidates: list[str],
) -> float | None:
    """Retourne une moyenne pondérée par population/ménages si possible."""
    value_column, _ = _column_values(rows, dataframe, value_candidates)

    if value_column is None:
        return None

    weight_column, _ = _column_values(rows, dataframe, weight_candidates)
    tmp = pd.DataFrame({"value": rows[value_column].map(to_float)})

    if weight_column:
        tmp["weight"] = rows[weight_column].map(to_float)
    else:
        tmp["weight"] = 1.0

    tmp = tmp.dropna()
    tmp = tmp[(tmp["value"] > 0) & (tmp["weight"] > 0)]

    if tmp.empty:
        return None

    return float((tmp["value"] * tmp["weight"]).sum() / tmp["weight"].sum())


def approximate_median_age_from_bins(
    row: pd.Series,
    dataframe: pd.DataFrame | None = None,
) -> float | None:
    """
    Estime l'âge médian à partir des classes d'âge INSEE.

    L'INSEE ne fournit pas toujours une colonne d'âge médian directement.
    Cette fonction reconstruit une médiane approximative par interpolation
    linéaire dans les classes d'âge disponibles.
    """
    available_columns = set(dataframe.columns) if dataframe is not None else set(row.index)

    for bins in AGE_BINS_BY_YEAR:
        values: list[tuple[float, int, int]] = []

        for column, age_min, age_max in bins:
            if column not in available_columns and column not in row.index:
                continue

            value = to_float(row.get(column))

            if value is not None and value > 0:
                values.append((value, age_min, age_max))

        if len(values) < 3:
            continue

        total = sum(value for value, _, _ in values)

        if total <= 0:
            continue

        half = total / 2.0
        cumulative = 0.0

        for count, age_min, age_max in values:
            previous = cumulative
            cumulative += count

            if cumulative >= half:
                within_class = (half - previous) / count
                median_age = age_min + within_class * (age_max - age_min)
                return round(median_age, 1)

    return None


def _build_age_synthetic_row(rows: pd.DataFrame, dataframe: pd.DataFrame) -> pd.Series:
    """Agrège les colonnes d'âge pour une commune ou des arrondissements PLM."""
    synthetic: dict[str, float] = {}

    for column in dataframe.columns:
        if re.match(r"^P\d{2}_POP(0014|1529|3044|4559|6074|75P)$", str(column)):
            synthetic[column] = rows[column].map(to_float).dropna().sum()

    return pd.Series(synthetic)


def _extract_population(rows: pd.DataFrame, dataframe: pd.DataFrame) -> float | None:
    """Extrait la population communale."""
    return _get_sum(
        rows,
        dataframe,
        [
            "P23_POP",
            "P22_POP",
            "P21_POP",
            "P20_POP",
            "PMUN23",
            "PMUN22",
            "PMUN21",
            "PMUN20",
            "population",
            "pop",
        ],
    )


def _extract_unemployment_rate(
    rows: pd.DataFrame,
    dataframe: pd.DataFrame,
) -> float | None:
    """Calcule le taux de chômage à partir chômeurs / actifs."""
    chomeurs = _get_sum(
        rows,
        dataframe,
        ["P23_CHOM1564", "P22_CHOM1564", "P21_CHOM1564", "P20_CHOM1564"],
    )
    actifs = _get_sum(
        rows,
        dataframe,
        ["P23_ACT1564", "P22_ACT1564", "P21_ACT1564", "P20_ACT1564"],
    )

    if chomeurs is not None and actifs is not None and actifs > 0:
        return round(chomeurs / actifs * 100.0, 1)

    return None


def _extract_median_income(rows: pd.DataFrame, dataframe: pd.DataFrame) -> float | None:
    """Extrait le revenu / niveau de vie médian."""
    return _get_weighted(
        rows,
        dataframe,
        [
            "MED_SL23",
            "MED_SL22",
            "MED_SL21",
            "MED23",
            "MED22",
            "MED21",
            "MED20",
            "DISP_MED23",
            "DISP_MED22",
            "DISP_MED21",
            "DISP_MED20",
            "MED_NIVEAU_VIE23",
            "MED_NIVEAU_VIE22",
            "MED_NIVEAU_VIE21",
            "MED_NIVEAU_VIE20",
            "revenu_median",
            "niveau_vie_median",
        ],
        [
            "P23_POP",
            "P22_POP",
            "P21_POP",
            "P20_POP",
            "P23_MEN",
            "P22_MEN",
        ],
    )


def _extract_vacant_housing_rate(
    rows: pd.DataFrame,
    dataframe: pd.DataFrame,
) -> float | None:
    """Calcule le taux de logements vacants."""
    vacant_housing = _get_sum(
        rows,
        dataframe,
        [
            "P23_LOGVAC",
            "P23_LOG_VAC",
            "P22_LOGVAC",
            "P22_LOG_VAC",
            "P21_LOGVAC",
            "P21_LOG_VAC",
            "P20_LOGVAC",
            "P20_LOG_VAC",
        ],
    )
    total_housing = _get_sum(rows, dataframe, ["P23_LOG", "P22_LOG", "P21_LOG", "P20_LOG"])

    if vacant_housing is not None and total_housing is not None and total_housing > 0:
        return round(vacant_housing / total_housing * 100.0, 1)

    return None


def _extract_children_pct(
    rows: pd.DataFrame,
    dataframe: pd.DataFrame,
    population: float,
) -> float | None:
    """Calcule la part des moins de 15 ans."""
    children = _get_sum(rows, dataframe, ["P23_POP0014", "P22_POP0014", "P21_POP0014", "P20_POP0014"])

    if children is not None and population > 0:
        return round(children / population * 100.0, 1)

    return None


def _extract_seniors_pct(
    rows: pd.DataFrame,
    dataframe: pd.DataFrame,
    population: float,
) -> float | None:
    """Calcule ou approxime la part des 65 ans et plus."""
    seniors = _get_sum(rows, dataframe, ["P23_POP65P", "P22_POP65P", "P21_POP65P", "P20_POP65P"])

    if seniors is not None and population > 0:
        return round(seniors / population * 100.0, 1)

    seniors_65_79 = _get_sum(rows, dataframe, ["P23_POP6579", "P22_POP6579", "P21_POP6579", "P20_POP6579"])
    pop_75p = _get_sum(rows, dataframe, ["P23_POP75P", "P22_POP75P", "P21_POP75P", "P20_POP75P"])
    pop_80p = _get_sum(rows, dataframe, ["P23_POP80P", "P22_POP80P", "P21_POP80P", "P20_POP80P"])
    pop_60_74 = _get_sum(rows, dataframe, ["P23_POP6074", "P22_POP6074", "P21_POP6074", "P20_POP6074"])

    if seniors_65_79 is not None and pop_75p is not None and population > 0:
        return round((seniors_65_79 + pop_75p) / population * 100.0, 1)

    if seniors_65_79 is not None and pop_80p is not None and population > 0:
        return round((seniors_65_79 + pop_80p) / population * 100.0, 1)

    if pop_60_74 is not None and pop_75p is not None and population > 0:
        # Approximation : dans la classe 60-74, 10 années sur 15 sont >= 65 ans.
        estimated_seniors = pop_60_74 * (10.0 / 15.0) + pop_75p
        return round(estimated_seniors / population * 100.0, 1)

    return None


def _extract_business_indicators(
    rows: pd.DataFrame,
    dataframe: pd.DataFrame,
    population: float,
) -> tuple[int | None, float | None]:
    """Extrait le nombre d'entreprises et le ratio pour 1000 habitants."""
    companies = _get_sum(
        rows,
        dataframe,
        [
            "ETTOT24",
            "ETTOT23",
            "ETTOT22",
            "nb_entreprises",
        ],
    )

    if companies is None:
        return None, None

    companies_int = int(round(companies))
    companies_per_1000 = round(companies / max(population / 1000.0, 0.001), 2)

    return companies_int, companies_per_1000


def extract_rp_indicators(df: pd.DataFrame, code_insee: str) -> dict[str, float | int | None]:
    """Extrait les indicateurs RP / socio-économiques pour une commune."""
    result = _empty_rp_result()

    if df.empty:
        return result

    code_column = _find_column(df, CODE_COLUMN_CANDIDATES)

    if not code_column:
        return result

    rows = _rows_for_code(df, code_column, code_insee)

    if rows.empty:
        return result

    population = _extract_population(rows, df)

    if population is None or population <= 0:
        return result

    result["population"] = int(round(population))
    result["taux_chomage"] = _extract_unemployment_rate(rows, df)

    synthetic_age_row = _build_age_synthetic_row(rows, df)
    result["age_median"] = approximate_median_age_from_bins(synthetic_age_row, df)

    result["revenu_median"] = _extract_median_income(rows, df)
    result["taux_logements_vacants"] = _extract_vacant_housing_rate(rows, df)
    result["pct_moins_15_ans"] = _extract_children_pct(rows, df, population)
    result["pct_plus_65_ans"] = _extract_seniors_pct(rows, df, population)

    companies, companies_per_1000 = _extract_business_indicators(rows, df, population)
    result["nb_entreprises"] = companies
    result["entreprises_pour_1000"] = companies_per_1000

    return result


def extract_demo_indicators(
    df: pd.DataFrame,
    code_insee: str,
    pop: int,
) -> dict[str, float | None]:
    """Extrait les indicateurs démographiques complémentaires."""
    result = _empty_demo_result()

    if df.empty or not pop or pop <= 0:
        return result

    code_column = _find_column(df, CODE_COLUMN_CANDIDATES)

    if not code_column:
        return result

    rows = _rows_for_code(df, code_column, code_insee)

    if rows.empty:
        return result

    population = max(float(pop), 1.0)

    births = _get_sum(rows, df, ["NAISD24", "NAISD23", "NAISD22", "NAISD21", "NAISD20"])

    if births is not None and births > 0:
        result["taux_natalite"] = round(births / (population / 1000.0), 1)

    old_population = _get_sum(rows, df, ["P16_POP", "P17_POP", "P15_POP"])

    if old_population is not None and old_population > 0:
        result["evolution_population_pct"] = round((population - old_population) / old_population * 100.0, 1)

    return result
