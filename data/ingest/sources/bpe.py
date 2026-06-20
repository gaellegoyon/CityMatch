"""
data/ingest/sources/bpe.py
──────────────────────────
Chargement de la Base Permanente des Équipements (BPE) et extraction
des équipements utiles par commune.

Le chargeur est volontairement robuste, car les exports INSEE/Melodi peuvent
changer de structure :
- format long : GEO_OBJECT + GEO + FACILITY_TYPE + OBS_VALUE ;
- format détail : une ligne = un équipement ;
- format large : une colonne par type d'équipement.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any, Final

import pandas as pd

from data.ingest.config import BPE_YEAR, CACHE_DIR
from data.ingest.utils import console, download_cached


BPE_COLUMNS: Final[tuple[str, str, str]] = ("DEPCOM", "TYPEQU", "NB")

BPE_URLS: Final[tuple[tuple[str, str], ...]] = (
    (
        "https://www.insee.fr/fr/statistiques/fichier/8217527/DS_BPE_CSV_FR.zip",
        "BPE24_ENSEMBLE_CSV.zip",
    ),
    (
        "https://www.insee.fr/fr/statistiques/fichier/8217527/DS_BPE_XLSX_FR.zip",
        "BPE24_ENSEMBLE_XLSX.zip",
    ),
    (
        "https://www.insee.fr/fr/statistiques/fichier/7766585/BPE23_ENSEMBLE.zip",
        "BPE23_ENSEMBLE.zip",
    ),
)

CSV_SEPARATORS: Final[tuple[str, ...]] = (";", ",", "\t", "|")
CSV_ENCODINGS: Final[tuple[str, ...]] = ("utf-8", "utf-8-sig", "latin-1", "cp1252")

RAW_BPE_FIELDS: Final[dict[str, list[str]]] = {
    "nb_creches": ["D502"],
    "nb_ecoles_primaires": ["C107", "C108", "C109"],
    "nb_colleges": ["C201"],
    "nb_lycees": ["C301", "C302", "C303", "C304", "C305"],
    "nb_medecins_generalistes": ["D265"],
    "nb_pharmacies": ["D307"],
    "nb_hopitaux": ["D101", "D102", "D103", "D104", "D105"],
    "nb_gares": ["E107", "E108", "E109"],
    "nb_piscines": ["F101"],
    "nb_bibliotheques": ["F307"],
    "nb_supermarches": ["B104", "B105"],
    "nb_restaurants": ["A504"],
    "nb_cinemas": ["F303"],
    "nb_musees": ["F312", "F313"],
    "nb_dentistes": ["D277"],
    "nb_ophtalmologues": ["D270"],
    "nb_pediatres": ["D272"],
    "nb_urgences": ["D106"],
}

EMPTY_BPE_RESULT: Final[dict[str, int]] = {
    "nb_creches": 0,
    "nb_ecoles_primaires": 0,
    "nb_colleges": 0,
    "nb_lycees": 0,
    "nb_medecins_generalistes": 0,
    "nb_pharmacies": 0,
    "nb_hopitaux": 0,
    "nb_gares": 0,
    "nb_piscines": 0,
    "nb_bibliotheques": 0,
    "nb_supermarches": 0,
    "nb_restaurants": 0,
    "nb_equipements_sportifs": 0,
    "nb_cinemas": 0,
    "nb_musees": 0,
    "nb_dentistes": 0,
    "nb_ophtalmologues": 0,
    "nb_pediatres": 0,
    "nb_urgences": 0,
}


def _empty_bpe_dataframe() -> pd.DataFrame:
    """Retourne un DataFrame BPE vide au format normalisé."""
    return pd.DataFrame(columns=list(BPE_COLUMNS))


def normalize_code_commune(value: Any) -> str | None:
    """
    Normalise un code commune BPE vers un code INSEE CityMatch.

    Gère :
    - COM-34172, GEO-COM-34172, FR-COM-34172 ;
    - 34172, 34172.0 ;
    - 2A004 / 2B033 ;
    - arrondissements Paris/Lyon/Marseille vers commune principale.
    """
    if value is None:
        return None

    text = str(value).strip().upper()

    if not text or text.lower() in {"nan", "none", "na", "_z"}:
        return None

    text = re.sub(r"\.0$", "", text)
    text = re.sub(
        r"^(GEO-)?(COM|CODGEO|FR|FR-COM)-",
        "",
        text,
        flags=re.IGNORECASE,
    )

    corse_match = re.search(r"(2[AB]\d{3})$", text, flags=re.IGNORECASE)
    if corse_match:
        return corse_match.group(1).upper()

    numeric_match = re.search(r"(\d{5})$", text)

    if numeric_match:
        code = numeric_match.group(1)
    elif text.isdigit() and len(text) == 4:
        code = text.zfill(5)
    else:
        return None

    if re.match(r"^751\d{2}$", code):
        return "75056"

    if re.match(r"^6938\d$", code):
        return "69123"

    if re.match(r"^132\d{2}$", code):
        return "13055"

    return code if re.match(r"^\d{5}$", code) else None


def extract_type_code(value: Any) -> str | None:
    """Extrait un code équipement BPE du type C101, D265, F307."""
    if value is None:
        return None

    text = str(value).strip().upper()

    if not text or text.lower() in {"nan", "none", "na", "_z"}:
        return None

    match = re.search(r"\b([A-G]\d{3})\b", text)

    return match.group(1) if match else None


def clean_columns(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Nettoie les noms de colonnes et supprime les colonnes vides."""
    dataframe = dataframe.copy()
    dataframe.columns = [
        str(column).strip().replace("\ufeff", "")
        for column in dataframe.columns
    ]
    dataframe = dataframe.dropna(axis=1, how="all")
    return dataframe


def _score_code_column(series: pd.Series) -> int:
    """Score une colonne selon sa vraisemblance comme code commune."""
    sample = series.dropna().astype(str).head(1000)

    if sample.empty:
        return 0

    return sum(1 for value in sample if normalize_code_commune(value) is not None)


def _score_type_column(series: pd.Series) -> int:
    """Score une colonne selon sa vraisemblance comme type d'équipement BPE."""
    sample = series.dropna().astype(str).head(1000)

    if sample.empty:
        return 0

    return sum(1 for value in sample if extract_type_code(value) is not None)


def detect_code_col(dataframe: pd.DataFrame) -> str | None:
    """Détecte la colonne commune."""
    if dataframe.empty:
        return None

    exact_names = [
        "DEPCOM",
        "CODGEO",
        "COM",
        "GEO",
        "GEO_OBJECT",
        "code_insee",
        "code_commune",
        "Code commune",
        "Code géographique",
        "Code geographique",
        "OBS_GEO",
        "REF_AREA",
    ]

    lower_map = {str(column).strip().lower(): column for column in dataframe.columns}

    for name in exact_names:
        column = lower_map.get(name.lower())

        if column is not None and _score_code_column(dataframe[column]) >= 5:
            return column

    best_column: str | None = None
    best_score = 0

    for column in dataframe.columns:
        column_lower = str(column).lower()

        if column_lower in {"dep", "reg", "annee", "année", "year"}:
            continue

        score = _score_code_column(dataframe[column])

        if any(token in column_lower for token in ["commune", "com", "codgeo", "depcom", "geo", "insee"]):
            score += 20

        if score > best_score:
            best_column = column
            best_score = score

    return best_column if best_score >= 10 else None


def detect_type_col(dataframe: pd.DataFrame) -> str | None:
    """Détecte la colonne type d'équipement."""
    if dataframe.empty:
        return None

    exact_names = [
        "TYPEQU",
        "TYPE_EQUIPEMENT",
        "TYPE_EQUIP",
        "typequ",
        "type_equip",
        "type_equipement",
        "EQUIP",
        "EQUIPEMENT",
        "equipement",
        "Code équipement",
        "Code equipement",
        "code_equipement",
        "TYPEQU24",
        "FACILITY_TYPE",
    ]

    lower_map = {str(column).strip().lower(): column for column in dataframe.columns}

    for name in exact_names:
        column = lower_map.get(name.lower())

        if column is not None and _score_type_column(dataframe[column]) >= 5:
            return column

    best_column: str | None = None
    best_score = 0

    for column in dataframe.columns:
        column_lower = str(column).lower()
        score = _score_type_column(dataframe[column])

        if any(token in column_lower for token in ["type", "equip", "équip", "bpe", "facility"]):
            score += 20

        if score > best_score:
            best_column = column
            best_score = score

    return best_column if best_score >= 10 else None


def is_year_like_series(series: pd.Series) -> bool:
    """
    Évite de prendre une colonne année/période comme colonne de comptage.

    Les exports Melodi contiennent parfois TIME_PERIOD=2024. Cette colonne ne
    doit jamais être interprétée comme un nombre d'équipements.
    """
    raw = series.dropna().astype(str).str.strip()

    if raw.empty:
        return False

    year_text = raw.str.extract(r"(19\d{2}|20\d{2}|21\d{2})", expand=False)
    years = pd.to_numeric(year_text, errors="coerce").dropna()

    if years.empty:
        return False

    share_year = len(years) / max(len(raw), 1)
    top_freq = years.value_counts(normalize=True).iloc[0]

    return bool(share_year > 0.80 and top_freq > 0.50)


def detect_value_col(dataframe: pd.DataFrame, excluded: set[str]) -> str | None:
    """
    Détecte une vraie colonne de comptage.

    Si aucune colonne clairement nommée OBS_VALUE/NB/NOMBRE n'est trouvée, le
    chargeur considère que chaque ligne commune/type vaut 1.
    """
    exact_names = [
        "OBS_VALUE",
        "OBS_VALUE_NB",
        "NB",
        "nb",
        "NOMBRE",
        "nombre",
        "nb_equipements",
        "NB_EQUIP",
        "NB_EQU",
    ]

    lower_map = {str(column).strip().lower(): column for column in dataframe.columns}

    for name in exact_names:
        column = lower_map.get(name.lower())

        if column is None or column in excluded:
            continue

        column_lower = str(column).lower()

        if any(token in column_lower for token in ["time", "annee", "année", "year", "periode", "period"]):
            continue

        if is_year_like_series(dataframe[column]):
            continue

        values = pd.to_numeric(
            dataframe[column].astype(str).str.replace(",", ".", regex=False),
            errors="coerce",
        )

        if values.notna().sum() >= 10:
            return column

    return None


def type_columns_from_names(dataframe: pd.DataFrame) -> dict[str, str]:
    """Détecte les colonnes larges dont le nom est un code équipement."""
    mapping: dict[str, str] = {}

    for column in dataframe.columns:
        code = extract_type_code(column)

        if code:
            mapping[column] = code

    return mapping


def _normalize_melodi_bpe(dataframe: pd.DataFrame, source_name: str) -> pd.DataFrame | None:
    """
    Normalise le format INSEE/Melodi BPE récent.

    Colonnes attendues :
    - GEO_OBJECT ;
    - GEO ;
    - FACILITY_TYPE ;
    - OBS_VALUE.
    """
    required = {"GEO", "GEO_OBJECT", "FACILITY_TYPE", "OBS_VALUE"}

    if not required.issubset(set(dataframe.columns)):
        return None

    subset = dataframe[
        dataframe["GEO_OBJECT"].astype(str).str.upper().eq("COM")
    ].copy()

    if subset.empty:
        return None

    out = pd.DataFrame()
    out["DEPCOM"] = subset["GEO"].map(normalize_code_commune)
    out["TYPEQU"] = subset["FACILITY_TYPE"].map(extract_type_code)
    out["NB"] = pd.to_numeric(
        subset["OBS_VALUE"].astype(str).str.replace(",", ".", regex=False),
        errors="coerce",
    ).fillna(0)

    out = out[out["DEPCOM"].notna() & out["TYPEQU"].notna()]
    out = out[out["NB"] > 0]

    if out.empty:
        return None

    out = out.groupby(["DEPCOM", "TYPEQU"], as_index=False)["NB"].sum()
    out["_source_bpe"] = source_name

    return out


def normalize_long_or_detail(dataframe: pd.DataFrame, source_name: str) -> pd.DataFrame | None:
    """Normalise un format long ou détail."""
    dataframe = clean_columns(dataframe)

    if dataframe.empty or len(dataframe.columns) < 2:
        return None

    melodi = _normalize_melodi_bpe(dataframe, source_name)

    if melodi is not None and not melodi.empty:
        return melodi

    code_column = detect_code_col(dataframe)
    type_column = detect_type_col(dataframe)

    if not code_column or not type_column:
        return None

    out = pd.DataFrame()
    out["DEPCOM"] = dataframe[code_column].map(normalize_code_commune)
    out["TYPEQU"] = dataframe[type_column].map(extract_type_code)

    value_column = detect_value_col(dataframe, {code_column, type_column})

    if value_column:
        values = pd.to_numeric(
            dataframe[value_column].astype(str).str.replace(",", ".", regex=False),
            errors="coerce",
        ).fillna(0)

        out["NB"] = 1 if is_year_like_series(dataframe[value_column]) else values
    else:
        out["NB"] = 1

    out = out[out["DEPCOM"].notna() & out["TYPEQU"].notna()]
    out["NB"] = pd.to_numeric(out["NB"], errors="coerce").fillna(0)
    out = out[out["NB"] > 0]

    if out.empty:
        return None

    out = out.groupby(["DEPCOM", "TYPEQU"], as_index=False)["NB"].sum()
    out["_source_bpe"] = source_name

    return out


def normalize_wide(dataframe: pd.DataFrame, source_name: str) -> pd.DataFrame | None:
    """Normalise un format large : une colonne par type d'équipement."""
    dataframe = clean_columns(dataframe)

    if dataframe.empty:
        return None

    code_column = detect_code_col(dataframe)

    if not code_column:
        return None

    type_column_map = type_columns_from_names(dataframe)

    if not type_column_map:
        return None

    base = dataframe[[code_column] + list(type_column_map)].copy()
    base = base.rename(columns={code_column: "DEPCOM"})
    base["DEPCOM"] = base["DEPCOM"].map(normalize_code_commune)
    base = base[base["DEPCOM"].notna()]

    if base.empty:
        return None

    base = base.rename(columns=type_column_map)
    type_columns = sorted(set(type_column_map.values()))

    long = base.melt(
        id_vars=["DEPCOM"],
        value_vars=type_columns,
        var_name="TYPEQU",
        value_name="NB",
    )
    long["NB"] = pd.to_numeric(
        long["NB"].astype(str).str.replace(",", ".", regex=False),
        errors="coerce",
    ).fillna(0)

    long = long[long["NB"] > 0]

    if long.empty:
        return None

    long = long.groupby(["DEPCOM", "TYPEQU"], as_index=False)["NB"].sum()
    long["_source_bpe"] = source_name

    return long


def normalize_any_bpe(dataframe: pd.DataFrame, source_name: str) -> pd.DataFrame | None:
    """Normalise n'importe quel format BPE reconnu."""
    for normalizer in (normalize_long_or_detail, normalize_wide):
        part = normalizer(dataframe, source_name)

        if part is not None and not part.empty:
            return part

    return None


def read_csv_attempts(raw_path: Path) -> list[pd.DataFrame]:
    """Essaie plusieurs lectures CSV/TXT, y compris avec lignes de métadonnées."""
    dataframes: list[pd.DataFrame] = []

    for separator in CSV_SEPARATORS:
        for encoding in CSV_ENCODINGS:
            try:
                dataframe = pd.read_csv(
                    raw_path,
                    sep=separator,
                    encoding=encoding,
                    dtype=str,
                    low_memory=False,
                    on_bad_lines="skip",
                )

                if len(dataframe.columns) > 1:
                    dataframes.append(dataframe)

            except Exception:
                continue

    for header in range(1, 8):
        for separator in (";", ",", "\t"):
            try:
                dataframe = pd.read_csv(
                    raw_path,
                    sep=separator,
                    dtype=str,
                    low_memory=False,
                    on_bad_lines="skip",
                    header=header,
                )

                if len(dataframe.columns) > 1:
                    dataframes.append(dataframe)

            except Exception:
                continue

    return dataframes


def _download_first_available_bpe_archive() -> Path | None:
    """Télécharge le premier fichier BPE disponible."""
    for url, filename in BPE_URLS:
        path = download_cached(url, filename)

        if path is not None and path.exists() and path.stat().st_size > 0:
            return path

    return None


def _is_relevant_bpe_file(file_name: str) -> bool:
    """Filtre les fichiers exploitables dans l'archive BPE."""
    lower = file_name.lower()
    filename = Path(file_name).name.lower()

    return (
        lower.endswith((".csv", ".txt", ".xlsx", ".xls"))
        and not filename.startswith("~$")
        and "__macosx" not in lower
        and "metadata" not in filename
    )


def _normalize_csv_file(raw_path: Path, source_name: str) -> pd.DataFrame | None:
    """Normalise un fichier CSV/TXT extrait."""
    for dataframe in read_csv_attempts(raw_path):
        part = normalize_any_bpe(dataframe, source_name)

        if part is not None:
            return part

    try:
        preview = pd.read_csv(raw_path, sep=None, engine="python", dtype=str, nrows=3)
        console.print(
            f"[yellow]  ⚠️ Non reconnu {source_name} : "
            f"colonnes={list(preview.columns[:12])}[/yellow]"
        )
    except Exception:
        console.print(f"[yellow]  ⚠️ Non reconnu {source_name}[/yellow]")

    return None


def _normalize_excel_file(raw_path: Path, source_name: str) -> pd.DataFrame | None:
    """Normalise un fichier Excel extrait."""
    try:
        workbook = pd.ExcelFile(raw_path)
    except Exception as exc:
        console.print(f"[yellow]  ⚠️ Excel illisible {source_name}: {exc}[/yellow]")
        return None

    for sheet_name in workbook.sheet_names:
        for header in range(0, 15):
            try:
                dataframe = pd.read_excel(
                    raw_path,
                    sheet_name=sheet_name,
                    dtype=str,
                    header=header,
                    engine="openpyxl",
                )
                part = normalize_any_bpe(dataframe, f"{source_name}::{sheet_name}")

                if part is not None:
                    console.print(
                        f"[green]  → BPE table retenue : {source_name} / "
                        f"{sheet_name} header={header} ({len(part):,} couples commune-type)[/green]"
                    )
                    return part

            except Exception:
                continue

    console.print(f"[yellow]  ⚠️ Excel non reconnu {source_name}[/yellow]")

    return None


def _load_bpe_parts_from_archive(path: Path) -> list[pd.DataFrame]:
    """Inspecte l'archive BPE et retourne toutes les tables normalisées."""
    loaded_parts: list[pd.DataFrame] = []

    with zipfile.ZipFile(path) as archive:
        files = [
            file_name
            for file_name in archive.namelist()
            if _is_relevant_bpe_file(file_name)
        ]

        console.print(f"[dim]BPE : {len(files)} fichier(s) à inspecter dans {path.name}[/dim]")

        for index, file_name in enumerate(files, start=1):
            source_name = Path(file_name).name
            lower = file_name.lower()
            raw_path = CACHE_DIR / f"_bpe_extract_{index}_{source_name}"

            try:
                with archive.open(file_name) as source_file:
                    raw_path.write_bytes(source_file.read())

                part: pd.DataFrame | None = None

                if lower.endswith((".csv", ".txt")):
                    part = _normalize_csv_file(raw_path, source_name)
                elif lower.endswith((".xlsx", ".xls")):
                    part = _normalize_excel_file(raw_path, source_name)

                if part is not None and not part.empty:
                    loaded_parts.append(part)
                    console.print(
                        f"[green]  → BPE table retenue : {source_name} "
                        f"({len(part):,} couples commune-type)[/green]"
                    )

            except Exception as exc:
                console.print(f"[yellow]  ⚠️ BPE fichier ignoré {file_name}: {exc}[/yellow]")

    return loaded_parts


def load_bpe_2024() -> pd.DataFrame:
    """
    Charge la BPE et retourne un DataFrame agrégé :
        DEPCOM, TYPEQU, NB.
    """
    path = _download_first_available_bpe_archive()

    if path is None:
        console.print("[yellow]⚠️  BPE indisponible[/yellow]")
        return _empty_bpe_dataframe()

    try:
        loaded_parts = _load_bpe_parts_from_archive(path)

        if not loaded_parts:
            console.print("[yellow]⚠️  BPE chargée mais aucune table exploitable trouvée[/yellow]")
            return _empty_bpe_dataframe()

        dataframe = pd.concat(loaded_parts, ignore_index=True)
        dataframe = dataframe.groupby(["DEPCOM", "TYPEQU"], as_index=False)["NB"].sum()
        dataframe["NB"] = pd.to_numeric(dataframe["NB"], errors="coerce").fillna(0)

        console.print(
            f"[green]✅ BPE {BPE_YEAR} : {len(dataframe):,} couples commune-type, "
            f"{dataframe['DEPCOM'].nunique():,} communes, "
            f"{dataframe['TYPEQU'].nunique():,} types[/green]"
        )
        console.print(f"[dim]BPE colonnes normalisées : {list(dataframe.columns)}[/dim]")

        for code, name in [
            ("75056", "Paris"),
            ("13055", "Marseille"),
            ("69123", "Lyon"),
            ("34172", "Montpellier"),
        ]:
            count = dataframe.loc[dataframe["DEPCOM"] == code, "NB"].sum()
            console.print(f"[dim]  BPE contrôle {name} {code}: {int(count)} unités BPE agrégées[/dim]")

        return dataframe[list(BPE_COLUMNS)]

    except Exception as exc:
        console.print(f"[yellow]⚠️  Erreur BPE : {exc}[/yellow]")
        return _empty_bpe_dataframe()


def _normalize_bpe_code_for_lookup(code_insee: str) -> str:
    """Normalise un code INSEE pour recherche dans le DataFrame BPE."""
    normalized = normalize_code_commune(code_insee)

    if normalized is not None:
        return normalized

    return str(code_insee or "").strip().upper()


def extract_bpe_for_commune(bpe_df: pd.DataFrame, code_insee: str) -> dict[str, int]:
    """Extrait les équipements BPE utiles pour une commune."""
    result = dict(EMPTY_BPE_RESULT)

    if bpe_df.empty:
        return result

    required_columns = {"DEPCOM", "TYPEQU"}

    if not required_columns.issubset(set(bpe_df.columns)):
        return result

    target_code = _normalize_bpe_code_for_lookup(code_insee)

    dataframe = bpe_df.copy()
    dataframe["DEPCOM"] = dataframe["DEPCOM"].map(normalize_code_commune)
    dataframe["TYPEQU"] = dataframe["TYPEQU"].astype(str).str.strip().str.upper()

    sub = dataframe[dataframe["DEPCOM"] == target_code].copy()

    if sub.empty:
        return result

    if "NB" in sub.columns:
        sub["NB"] = pd.to_numeric(sub["NB"], errors="coerce").fillna(0)

        numbers = sub["NB"].dropna()

        if not numbers.empty:
            share_year = numbers.between(1900, 2100).mean()
            top_frequency = numbers.value_counts(normalize=True).iloc[0]

            if share_year > 0.80 and top_frequency > 0.50:
                sub["NB"] = 1
    else:
        sub["NB"] = 1

    def sum_codes(codes: list[str]) -> int:
        normalized_codes = [code.upper() for code in codes]
        value = sub.loc[sub["TYPEQU"].isin(normalized_codes), "NB"].sum()
        return int(round(float(value))) if pd.notna(value) else 0

    def sum_prefix(prefix: str) -> int:
        value = sub.loc[sub["TYPEQU"].str.startswith(prefix, na=False), "NB"].sum()
        return int(round(float(value))) if pd.notna(value) else 0

    for field, codes in RAW_BPE_FIELDS.items():
        result[field] = sum_codes(codes)

    result["nb_equipements_sportifs"] = sum_prefix("F1")

    return result
