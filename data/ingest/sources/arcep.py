"""
data/ingest/sources/arcep.py

Fibre communale réelle depuis ARCEP Ma Connexion Internet.
"""

from __future__ import annotations
import re

from typing import Optional

import pandas as pd

from data.ingest.config import ARCEP_LABEL
from data.ingest.utils import console, download_cached, read_csv_flexible, to_float


def load_arcep_fibre() -> pd.DataFrame:
    url = "https://data.arcep.fr/fixe/maconnexioninternet/statistiques/last/commune/commune_debit_filaire.csv"
    path = download_cached(url, "arcep_commune_fibre_last.csv")
    if path:
        df = read_csv_flexible(path)
        if not df.empty:
            console.print(f"[green]✅ ARCEP fibre {ARCEP_LABEL} : {len(df):,} communes[/green]")
            return df
    console.print("[yellow]⚠️  ARCEP fibre non chargée — fibre_pct restera NULL[/yellow]")
    return pd.DataFrame()


def extract_fibre_pct(arcep_df: pd.DataFrame, code_insee: str) -> Optional[float]:
    """
    Retourne une vraie valeur ARCEP si détectable, sinon None.

    Formats supportés :
    1. Format direct avec une colonne de taux fibre/FTTH.
    2. Format ratio avec colonnes locaux/prises FTTH.
    3. Format long ARCEP actuel :
       code_insee, nbr, type, inel_hd, elig_hd05, elig_hd3,
       elig_bhd8, elig_thd30, elig_thd100, elig_thd1g, date

       Dans ce format, une commune a plusieurs lignes par "type".
       On calcule :
           fibre_pct = nbr(type fibre/ftth) / somme(nbr tous types éligibles) * 100
       Si aucun type fibre/ftth n'est identifiable, on utilise elig_thd100 ou
       elig_thd1g comme proxy très haut débit robuste, car ces colonnes sont
       déjà communales et exprimées en part/compte selon le fichier.
    """
    if arcep_df.empty:
        return None

    df = arcep_df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    colmap = {c.lower(): c for c in df.columns}

    col_code = next(
        (
            colmap.get(c)
            for c in ["code_insee", "code_commune", "codgeo", "commune", "code_insee_commune"]
            if colmap.get(c)
        ),
        None,
    )
    if not col_code:
        return None

    code = str(code_insee).zfill(5)
    rows = df[df[col_code].astype(str).str.replace(r"\\.0$", "", regex=True).str.zfill(5) == code]
    if rows.empty:
        return None

    # ── 1) Taux direct fibre/FTTH ────────────────────────────────────────────
    pct_candidates = [
        c for c in df.columns
        if any(k in c.lower() for k in ["fibre", "ftth"])
        and any(k in c.lower() for k in ["taux", "pct", "pourcentage", "couverture", "eligibilite", "éligibilité"])
    ]
    for col in pct_candidates:
        vals = rows[col].map(to_float).dropna()
        if vals.empty:
            continue
        v = float(vals.median())
        if 0 <= v <= 1:
            return round(v * 100, 1)
        if 1 < v <= 100:
            return round(v, 1)

    # ── 2) Format long ARCEP : type + nbr ────────────────────────────────────
    lower_cols = {c.lower(): c for c in rows.columns}
    type_col = lower_cols.get("type")
    nbr_col = lower_cols.get("nbr")

    if type_col and nbr_col:
        work = rows[[type_col, nbr_col]].copy()
        work["_type"] = work[type_col].astype(str).str.lower()
        work["_nbr"] = work[nbr_col].map(to_float)

        total = work["_nbr"].dropna().sum()
        if total and total > 0:
            fibre_mask = work["_type"].str.contains(
                r"fibre|ftth|fth|fibre optique|fibre_optique",
                regex=True,
                na=False,
            )
            fibre_total = work.loc[fibre_mask, "_nbr"].dropna().sum()
            if fibre_total > 0:
                return round(min(100, max(0, fibre_total / total * 100)), 1)

        # Dans certains exports, il n'y a pas une ligne type=fibre,
        # mais des colonnes d'éligibilité par débit.
        # On tente alors elig_thd1g puis elig_thd100, en détectant si c'est un taux.
        for col_name in ["elig_thd1g", "elig_thd100", "elig_thd30"]:
            col = lower_cols.get(col_name)
            if not col:
                continue
            vals = rows[col].map(to_float).dropna()
            if vals.empty:
                continue

            v = float(vals.median())

            # Cas taux 0-1 ou 0-100
            if 0 <= v <= 1:
                return round(v * 100, 1)
            if 1 < v <= 100:
                return round(v, 1)

            # Cas compte : ratio sur nbr total si possible
            total_nbr = rows[nbr_col].map(to_float).dropna().sum()
            if total_nbr and total_nbr > 0 and 0 <= v <= total_nbr * 1.05:
                return round(min(100, max(0, v / total_nbr * 100)), 1)

    # ── 3) Ratio numérateur / dénominateur classique ─────────────────────────
    num_candidates = [
        c for c in df.columns
        if any(k in c.lower() for k in ["fibre", "ftth"])
        and any(k in c.lower() for k in ["locaux", "prises", "couverts", "eligible", "éligible", "raccordable"])
    ]
    den_candidates = [
        c for c in df.columns
        if any(k in c.lower() for k in ["locaux", "prises", "logements"])
        and not any(k in c.lower() for k in ["fibre", "ftth", "cuivre", "cable", "dsl"])
    ]

    r = rows.iloc[0]
    for num_col in num_candidates:
        num = to_float(r.get(num_col))
        if not num or num <= 0:
            continue
        for den_col in den_candidates:
            den = to_float(r.get(den_col))
            if den and den > 0 and num <= den * 1.05:
                return round(min(100, max(0, num / den * 100)), 1)

    return None
