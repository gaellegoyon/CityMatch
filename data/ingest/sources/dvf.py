"""
data/ingest/sources/dvf.py

Prix immobilier médian au m² depuis DVF.

La logique importante est le regroupement par mutation pour éviter de
surévaluer les transactions multi-lignes.
"""

from __future__ import annotations

import re
import threading
from typing import Optional

import pandas as pd

from data.ingest.config import CACHE_DIR, DEPARTEMENTS_SANS_DVF, DVF_YEARS
from data.ingest.utils import console, download_cached, read_csv_flexible, to_float

_dvf_lock = threading.Lock()
_dvf_memory: dict[str, pd.DataFrame] = {}


def load_dvf_par_departement(dep: str) -> pd.DataFrame:
    # Départements sans DVF → retourner directement vide
    if dep in DEPARTEMENTS_SANS_DVF:
        return pd.DataFrame()
    # Cache mémoire — évite de relire le CSV pour chaque commune du même département
    if dep in _dvf_memory:
        return _dvf_memory[dep]

    with _dvf_lock:
        if dep in _dvf_memory:
            return _dvf_memory[dep]

        for year in DVF_YEARS:
            cache_name = f"dvf_{dep}_{year}.csv"
            cache_path = CACHE_DIR / cache_name
            if cache_path.exists():
                df = read_csv_flexible(cache_path, seps=[",", ";"])
                if not df.empty:
                    _dvf_memory[dep] = df
                    return df

            url = f"https://files.data.gouv.fr/geo-dvf/latest/csv/{year}/departements/{dep}.csv.gz"
            path = download_cached(url, f"dvf_{dep}_{year}.csv.gz")
            if not path:
                continue
            try:
                df = pd.read_csv(path, compression="gzip", dtype=str, low_memory=False)
                if "type_local" in df.columns:
                    df = df[df["type_local"].isin(["Appartement", "Maison"])]
                df.to_csv(cache_path, index=False)
                console.print(f"[green]✅ DVF {year} département {dep} : {len(df):,} lignes[/green]")
                _dvf_memory[dep] = df
                return df
            except Exception as e:
                console.print(f"[yellow]⚠️  DVF dépt {dep} {year}: {e}[/yellow]")

        _dvf_memory[dep] = pd.DataFrame()
    return _dvf_memory.get(dep, pd.DataFrame())


def compute_prix_immo(code_insee: str, dep: str) -> Optional[float]:
    """
    Calcule le prix immobilier médian €/m² depuis DVF.

    Correction importante :
    DVF contient souvent plusieurs lignes pour une même mutation.
    La valeur_fonciere peut être répétée sur chaque ligne de lot/local.
    Si on calcule valeur_fonciere / surface ligne par ligne, on peut créer
    des prix artificiellement énormes, par exemple Auxerre à ~9 680 €/m².

    Méthode robuste :
    1. garder uniquement Maison/Appartement si type_local existe ;
    2. convertir surface et valeur ;
    3. agréger par id_mutation quand la colonne existe :
       - valeur = première valeur foncière non nulle de la mutation ;
       - surface = somme des surfaces bâties de la mutation ;
    4. calculer prix_m2 par mutation ;
    5. retirer les extrêmes techniques ;
    6. retourner la médiane, si volume suffisant.
    """
    dvf = load_dvf_par_departement(dep)
    if dvf.empty:
        return None

    col_commune = next((c for c in ["code_commune", "codecommune", "com_insee"] if c in dvf.columns), None)
    col_surface = next((c for c in ["surface_reelle_bati", "surface_bati"] if c in dvf.columns), None)
    col_valeur = next((c for c in ["valeur_fonciere", "valeur"] if c in dvf.columns), None)
    col_type = next((c for c in ["type_local", "type"] if c in dvf.columns), None)
    col_mutation = next((c for c in ["id_mutation", "idmutation", "numero_disposition"] if c in dvf.columns), None)

    if not all([col_commune, col_surface, col_valeur]):
        return None

    code = str(code_insee).zfill(5)
    sub = dvf[dvf[col_commune].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(5) == code].copy()
    if sub.empty:
        return None

    if col_type:
        sub = sub[sub[col_type].astype(str).isin(["Appartement", "Maison"])].copy()

    sub["surface"] = sub[col_surface].map(to_float)
    sub["valeur"] = sub[col_valeur].map(to_float)
    sub = sub.dropna(subset=["surface", "valeur"])
    sub = sub[(sub["surface"] >= 15) & (sub["surface"] <= 400)]
    sub = sub[(sub["valeur"] >= 20000) & (sub["valeur"] <= 10000000)]

    if sub.empty:
        return None

    if col_mutation and col_mutation in sub.columns:
        # Une mutation peut contenir plusieurs lignes/localisations.
        # On somme la surface et on prend une seule fois la valeur foncière.
        grouped = (
            sub.groupby(col_mutation, as_index=False)
            .agg(
                surface=("surface", "sum"),
                valeur=("valeur", "first"),
            )
        )
    else:
        grouped = sub[["surface", "valeur"]].copy()

    grouped = grouped[(grouped["surface"] >= 15) & (grouped["surface"] <= 800)]
    grouped = grouped[(grouped["valeur"] >= 20000) & (grouped["valeur"] <= 10000000)]
    if grouped.empty:
        return None

    grouped["prix_m2"] = grouped["valeur"] / grouped["surface"]

    # Bornes techniques larges : on ne veut pas capper Paris/Neuilly,
    # seulement enlever les erreurs DVF/lotissement.
    grouped = grouped[(grouped["prix_m2"] >= 300) & (grouped["prix_m2"] <= 20000)]

    if grouped.empty:
        return None

    # Si volume suffisant, trim léger 5%-95%.
    if len(grouped) >= 20:
        q05 = grouped["prix_m2"].quantile(0.05)
        q95 = grouped["prix_m2"].quantile(0.95)
        grouped = grouped[(grouped["prix_m2"] >= q05) & (grouped["prix_m2"] <= q95)]

    if grouped.empty:
        return None

    return round(float(grouped["prix_m2"].median()), 0)
