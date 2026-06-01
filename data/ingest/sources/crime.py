"""
data/ingest/sources/crime.py

Chargement et extraction des indicateurs SSMSI communaux.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import pandas as pd

from data.ingest.config import CACHE_DIR, CRIME_YEAR
from data.ingest.utils import console, download_cached, read_csv_flexible, to_float


def load_criminalite() -> pd.DataFrame:
    resources = [
        ("https://www.data.gouv.fr/api/1/datasets/r/44ef4323-1097-48d5-8719-3c544b55d294", "criminalite_commune_2016_2025.csv.gz"),
        ("https://www.data.gouv.fr/api/1/datasets/r/604d71b8-337d-4869-9226-49e01bae87df", "criminalite_commune_2016_2025.parquet"),
    ]

    for url, filename in resources:
        path = download_cached(url, filename)
        if not path:
            continue
        try:
            if filename.endswith(".parquet"):
                df = pd.read_parquet(path)
            elif filename.endswith(".gz"):
                try:
                    csv_path = CACHE_DIR / "criminalite_commune_2016_2025.csv"
                    # Éviter de re-décompresser si le CSV existe déjà
                    if not csv_path.exists():
                        with gzip.open(path, "rb") as f_in:
                            csv_path.write_bytes(f_in.read())
                    df = read_csv_flexible(csv_path)
                except Exception:
                    df = read_csv_flexible(path)
            else:
                df = read_csv_flexible(path)

            if not df.empty:
                console.print(f"[green]✅ Criminalité SSMSI : {len(df):,} lignes[/green]")
                return df
        except Exception as e:
            console.print(f"[yellow]⚠️  Criminalité ressource {filename}: {e}[/yellow]")

    console.print("[yellow]⚠️  Données criminalité non disponibles[/yellow]")
    return pd.DataFrame()


def extract_criminalite(crime_df: pd.DataFrame, code_insee: str, population: int) -> dict:
    result = {
        "cambriolages_pour_1000": None,
        "violences_physiques_pour_1000": None,
        "criminalite_pour_1000": None,
        "score_securite": None,
    }
    if crime_df.empty or not population:
        return result

    col_code = next((c for c in ["CODGEO_2025", "CODGEO", "code_insee", "Code commune"] if c in crime_df.columns), None)
    col_ind = next((c for c in ["indicateur", "Indicateur", "classe", "libelle_indicateur"] if c in crime_df.columns), None)
    if not col_code or not col_ind:
        return result

    sub = crime_df[crime_df[col_code].astype(str).str.zfill(5) == str(code_insee).zfill(5)].copy()
    if sub.empty:
        return result

    if "annee" in sub.columns:
        sub_year = sub[sub["annee"].astype(str) == str(CRIME_YEAR)]
        sub = sub_year if not sub_year.empty else sub[sub["annee"].astype(str) == str(CRIME_YEAR - 1)]
    elif "Année" in sub.columns:
        sub_year = sub[sub["Année"].astype(str) == str(CRIME_YEAR)]
        sub = sub_year if not sub_year.empty else sub

    if "est_diffuse" in sub.columns:
        sub_diff = sub[sub["est_diffuse"].astype(str).str.lower().isin(["true", "1", "oui", "vrai"])]
        if not sub_diff.empty:
            sub = sub_diff

    if sub.empty:
        return result

    taux_col = next((c for c in ["taux_pour_mille", "taux pour mille", "taux"] if c in sub.columns), None)
    nb_col = next((c for c in ["nombre", "Nombre", "faits"] if c in sub.columns), None)

    def get_taux(patterns):
        mask = sub[col_ind].astype(str).str.lower().apply(lambda x: any(p in x for p in patterns))
        rows = sub[mask]
        if rows.empty:
            return None
        if taux_col:
            vals = rows[taux_col].map(to_float).dropna()
            if not vals.empty:
                return round(float(vals.sum()), 2)
        if nb_col:
            vals = rows[nb_col].map(to_float).dropna()
            if not vals.empty:
                return round(float(vals.sum()) / max(population / 1000, 0.001), 2)
        return None

    result["cambriolages_pour_1000"] = get_taux(["cambriol"])
    result["violences_physiques_pour_1000"] = get_taux(["violences physiques", "coups et blessures"])

    if taux_col:
        vals = sub[taux_col].map(to_float).dropna()
        if not vals.empty:
            total = float(vals.sum())
            result["criminalite_pour_1000"] = round(total, 1)
            result["score_securite"] = round(max(0.0, 10.0 - total / 15.0), 1)

    return result
