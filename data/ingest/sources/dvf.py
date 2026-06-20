"""
data/ingest/sources/dvf.py
──────────────────────────
Prix immobilier médian au m² depuis DVF.

La logique importante est le regroupement par mutation pour éviter de
surévaluer les transactions multi-lignes.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any, Final

import pandas as pd

from data.ingest.config import CACHE_DIR, DEPARTEMENTS_SANS_DVF, DVF_YEARS
from data.ingest.utils import console, download_cached, read_csv_flexible, to_float


_dvf_lock = threading.Lock()
_dvf_memory: dict[str, pd.DataFrame] = {}

DVF_BASE_URL: Final[str] = "https://files.data.gouv.fr/geo-dvf/latest/csv"
EMPTY_DVF_COLUMNS: Final[list[str]] = [
    "id_mutation",
    "date_mutation",
    "nature_mutation",
    "valeur_fonciere",
    "code_commune",
    "type_local",
    "surface_reelle_bati",
]

PROPERTY_TYPES: Final[frozenset[str]] = frozenset({"appartement", "maison"})
VALID_NATURE_KEYWORDS: Final[tuple[str, ...]] = (
    "vente",
)


def _empty_dvf_dataframe() -> pd.DataFrame:
    """Retourne un DataFrame DVF vide mais stable."""
    return pd.DataFrame(columns=EMPTY_DVF_COLUMNS)


def _normalize_department(dep: str | None, code_insee: str | None = None) -> str:
    """
    Normalise un code département.

    Gère :
    - 01 ;
    - 1 ;
    - 2A / 2B ;
    - fallback depuis le code INSEE.
    """
    raw = str(dep or "").strip().upper()

    if raw in {"2A", "2B"}:
        return raw

    if raw.isdigit():
        return raw.zfill(2)

    code = str(code_insee or "").strip().upper()

    if code.startswith(("2A", "2B")):
        return code[:2]

    if len(code) >= 2 and code[:2].isdigit():
        return code[:2]

    return raw


def _normalize_insee_code(value: Any) -> str:
    """Normalise un code INSEE pour comparaison."""
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
    """Trouve la première colonne existante parmi plusieurs noms possibles."""
    column_map = _lower_column_map(dataframe)

    for candidate in candidates:
        column = column_map.get(candidate.lower())

        if column:
            return column

    return None


def _filter_property_types(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Garde uniquement les maisons et appartements si la colonne existe."""
    type_column = _find_column(dataframe, ("type_local", "type", "type local"))

    if not type_column:
        return dataframe

    filtered = dataframe[
        dataframe[type_column]
        .astype(str)
        .str.strip()
        .str.lower()
        .isin(PROPERTY_TYPES)
    ].copy()

    return filtered


def _filter_sale_nature(dataframe: pd.DataFrame) -> pd.DataFrame:
    """
    Garde les mutations de vente si la colonne nature existe.

    On conserve aussi les ventes en l'état futur d'achèvement, car elles
    restent des transactions immobilières exploitables.
    """
    nature_column = _find_column(dataframe, ("nature_mutation", "nature mutation", "nature"))

    if not nature_column:
        return dataframe

    normalized = dataframe[nature_column].astype(str).str.lower()
    mask = normalized.apply(
        lambda value: any(keyword in value for keyword in VALID_NATURE_KEYWORDS)
    )

    filtered = dataframe[mask].copy()

    return filtered if not filtered.empty else dataframe


def _read_cached_dvf_csv(cache_path: Path) -> pd.DataFrame:
    """Lit un cache CSV DVF déjà décompressé."""
    dataframe = read_csv_flexible(
        cache_path,
        seps=[",", ";"],
        min_columns=3,
    )

    if dataframe.empty:
        return dataframe

    dataframe.columns = [str(column).strip().replace("\ufeff", "") for column in dataframe.columns]

    return _filter_property_types(_filter_sale_nature(dataframe))


def _download_and_cache_dvf(dep: str, year: int, cache_path: Path) -> pd.DataFrame:
    """Télécharge un fichier DVF départemental puis crée un cache CSV filtré."""
    url = f"{DVF_BASE_URL}/{year}/departements/{dep}.csv.gz"
    compressed_path = download_cached(url, f"dvf_{dep}_{year}.csv.gz")

    if compressed_path is None:
        return _empty_dvf_dataframe()

    dataframe = pd.read_csv(
        compressed_path,
        compression="gzip",
        dtype=str,
        low_memory=False,
    )

    if dataframe.empty:
        return _empty_dvf_dataframe()

    dataframe.columns = [str(column).strip().replace("\ufeff", "") for column in dataframe.columns]
    dataframe = _filter_property_types(_filter_sale_nature(dataframe))

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = cache_path.with_suffix(cache_path.suffix + ".part")
    dataframe.to_csv(temp_path, index=False)
    temp_path.replace(cache_path)

    console.print(
        f"[green]✅ DVF {year} département {dep} : {len(dataframe):,} lignes[/green]"
    )

    return dataframe


def load_dvf_par_departement(dep: str, code_insee: str | None = None) -> pd.DataFrame:
    """
    Charge le DVF d'un département avec cache mémoire et cache disque.

    Les départements explicitement absents de geo-dvf retournent un DataFrame vide.
    """
    normalized_dep = _normalize_department(dep, code_insee=code_insee)

    if not normalized_dep:
        return _empty_dvf_dataframe()

    if normalized_dep in DEPARTEMENTS_SANS_DVF:
        _dvf_memory[normalized_dep] = _empty_dvf_dataframe()
        return _dvf_memory[normalized_dep]

    if normalized_dep in _dvf_memory:
        return _dvf_memory[normalized_dep]

    with _dvf_lock:
        if normalized_dep in _dvf_memory:
            return _dvf_memory[normalized_dep]

        for year in DVF_YEARS:
            cache_path = CACHE_DIR / f"dvf_{normalized_dep}_{year}.csv"

            if cache_path.exists() and cache_path.stat().st_size > 0:
                dataframe = _read_cached_dvf_csv(cache_path)

                if not dataframe.empty:
                    _dvf_memory[normalized_dep] = dataframe
                    return dataframe

            try:
                dataframe = _download_and_cache_dvf(
                    dep=normalized_dep,
                    year=int(year),
                    cache_path=cache_path,
                )

                if not dataframe.empty:
                    _dvf_memory[normalized_dep] = dataframe
                    return dataframe

            except Exception as exc:
                console.print(
                    f"[yellow]⚠️  DVF département {normalized_dep} {year}: {exc}[/yellow]"
                )

        _dvf_memory[normalized_dep] = _empty_dvf_dataframe()

    return _dvf_memory[normalized_dep]


def _prepare_city_dvf_rows(
    dvf: pd.DataFrame,
    code_insee: str,
) -> pd.DataFrame:
    """Filtre et nettoie les lignes DVF d'une commune."""
    commune_column = _find_column(
        dvf,
        (
            "code_commune",
            "codecommune",
            "com_insee",
            "code_insee",
            "codgeo",
        ),
    )
    surface_column = _find_column(
        dvf,
        (
            "surface_reelle_bati",
            "surface_reelle_bâtie",
            "surface_bati",
            "surface",
        ),
    )
    value_column = _find_column(
        dvf,
        (
            "valeur_fonciere",
            "valeur fonciere",
            "valeur_foncière",
            "valeur",
        ),
    )

    if not commune_column or not surface_column or not value_column:
        return pd.DataFrame(columns=["surface", "valeur"])

    target_code = _normalize_insee_code(code_insee)

    if not target_code:
        return pd.DataFrame(columns=["surface", "valeur"])

    rows = dvf[
        dvf[commune_column].map(_normalize_insee_code) == target_code
    ].copy()

    if rows.empty:
        return pd.DataFrame(columns=["surface", "valeur"])

    rows = _filter_property_types(_filter_sale_nature(rows))

    rows["surface"] = rows[surface_column].map(to_float)
    rows["valeur"] = rows[value_column].map(to_float)
    rows = rows.dropna(subset=["surface", "valeur"])

    rows = rows[(rows["surface"] >= 15) & (rows["surface"] <= 400)]
    rows = rows[(rows["valeur"] >= 20_000) & (rows["valeur"] <= 10_000_000)]

    return rows


def _group_by_mutation(rows: pd.DataFrame) -> pd.DataFrame:
    """
    Agrège les lignes DVF par mutation quand un identifiant fiable existe.

    Important :
    on n'utilise pas numero_disposition seul comme identifiant de mutation,
    car il vaut très souvent 1 et regrouperait des ventes différentes entre elles.
    """
    mutation_column = _find_column(rows, ("id_mutation", "idmutation"))

    if not mutation_column:
        return rows[["surface", "valeur"]].copy()

    grouped = (
        rows.groupby(mutation_column, as_index=False)
        .agg(
            surface=("surface", "sum"),
            valeur=("valeur", "first"),
        )
    )

    return grouped[["surface", "valeur"]]


def _clean_grouped_mutations(grouped: pd.DataFrame) -> pd.DataFrame:
    """Nettoie les mutations groupées et calcule le prix au m²."""
    if grouped.empty:
        return grouped

    grouped = grouped.copy()
    grouped = grouped[(grouped["surface"] >= 15) & (grouped["surface"] <= 800)]
    grouped = grouped[(grouped["valeur"] >= 20_000) & (grouped["valeur"] <= 10_000_000)]

    if grouped.empty:
        return grouped

    grouped["prix_m2"] = grouped["valeur"] / grouped["surface"]
    grouped = grouped[(grouped["prix_m2"] >= 300) & (grouped["prix_m2"] <= 20_000)]

    if grouped.empty:
        return grouped

    if len(grouped) >= 20:
        q05 = grouped["prix_m2"].quantile(0.05)
        q95 = grouped["prix_m2"].quantile(0.95)
        grouped = grouped[(grouped["prix_m2"] >= q05) & (grouped["prix_m2"] <= q95)]

    return grouped


def compute_prix_immo(code_insee: str, dep: str) -> float | None:
    """
    Calcule le prix immobilier médian €/m² depuis DVF.

    Méthode robuste :
    1. charger le DVF départemental ;
    2. garder uniquement la commune demandée ;
    3. garder Maison/Appartement si type_local existe ;
    4. convertir surface et valeur ;
    5. agréger par id_mutation quand disponible ;
    6. calculer prix_m2 par mutation ;
    7. retirer les extrêmes techniques ;
    8. retourner la médiane.
    """
    normalized_dep = _normalize_department(dep, code_insee=code_insee)
    dvf = load_dvf_par_departement(normalized_dep, code_insee=code_insee)

    if dvf.empty:
        return None

    rows = _prepare_city_dvf_rows(
        dvf=dvf,
        code_insee=code_insee,
    )

    if rows.empty:
        return None

    grouped = _group_by_mutation(rows)
    grouped = _clean_grouped_mutations(grouped)

    if grouped.empty:
        return None

    return round(float(grouped["prix_m2"].median()), 0)
