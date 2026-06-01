"""
data/ingest/sources/insee.py

Données INSEE communales :
- comparateur de territoires ;
- dossier complet ;
- indicateurs démographiques et socio-économiques.
"""

from __future__ import annotations

import zipfile
import re
from pathlib import Path
from typing import Optional

import pandas as pd

from data.ingest.config import CACHE_DIR
from data.ingest.utils import console, download_cached, read_csv_flexible, to_float


def load_dossier_complet() -> pd.DataFrame:
    """
    Charge et fusionne les données INSEE utiles au niveau communal.

    Sources :
    - base du comparateur de territoires : revenus MED_SL23, établissements ETTOT24,
      population, logements, chômage, naissances ;
    - dossier complet : variables RP complémentaires si disponibles.

    Le retour est un DataFrame unique indexé par CODGEO, avec une ligne par commune
    ou arrondissement municipal. Les communes Paris/Lyon/Marseille sont agrégées
    plus tard dans les fonctions d'extraction.
    """
    sources = [
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
    ]

    dfs: list[pd.DataFrame] = []

    def normalize_code_series(s: pd.Series) -> pd.Series:
        return s.astype(str).str.strip().str.replace(r"\.0$", "", regex=True).str.zfill(5)

    for url, filename, label in sources:
        path = download_cached(url, filename)
        if not path:
            continue

        try:
            loaded_parts: list[pd.DataFrame] = []

            if filename.endswith(".zip"):
                with zipfile.ZipFile(path) as zf:
                    csv_files = [
                        f for f in zf.namelist()
                        if f.lower().endswith(".csv")
                        and "__macosx" not in f.lower()
                        and not Path(f).name.startswith("~$")
                    ]

                    # Priorité aux fichiers communaux/base_cc quand il y en a plusieurs.
                    preferred = [
                        f for f in csv_files
                        if any(token in Path(f).name.lower() for token in ["base_cc", "comparateur", "commune"])
                    ]
                    csv_files = preferred or csv_files

                    for i, csv_file in enumerate(csv_files):
                        raw_path = CACHE_DIR / f"_{label}_extract_{i}_{Path(csv_file).name}"
                        with zf.open(csv_file) as f:
                            raw_path.write_bytes(f.read())

                        df_part = read_csv_flexible(raw_path)
                        if df_part.empty:
                            continue

                        col_code = next(
                            (
                                c for c in [
                                    "CODGEO", "COM", "code_insee", "code_commune",
                                    "CODGEO_2024", "CODGEO_2025"
                                ]
                                if c in df_part.columns
                            ),
                            None,
                        )
                        if not col_code:
                            continue

                        if col_code != "CODGEO":
                            df_part = df_part.rename(columns={col_code: "CODGEO"})

                        df_part["CODGEO"] = normalize_code_series(df_part["CODGEO"])
                        df_part = df_part.drop_duplicates(subset=["CODGEO"])

                        loaded_parts.append(df_part)
            else:
                df_part = read_csv_flexible(path)
                if not df_part.empty:
                    col_code = next(
                        (c for c in ["CODGEO", "COM", "code_insee", "code_commune"] if c in df_part.columns),
                        None,
                    )
                    if col_code:
                        if col_code != "CODGEO":
                            df_part = df_part.rename(columns={col_code: "CODGEO"})
                        df_part["CODGEO"] = normalize_code_series(df_part["CODGEO"])
                        loaded_parts.append(df_part.drop_duplicates(subset=["CODGEO"]))

            if loaded_parts:
                df_source = loaded_parts[0]
                for part in loaded_parts[1:]:
                    new_cols = [c for c in part.columns if c == "CODGEO" or c not in df_source.columns]
                    if len(new_cols) > 1:
                        df_source = df_source.merge(part[new_cols], on="CODGEO", how="left")

                dfs.append(df_source)
                revenu_cols = [c for c in df_source.columns if c.upper().startswith(("MED", "DISP_MED")) or "NIVEAU" in c.upper()]
                entreprise_cols = [c for c in df_source.columns if c.upper().startswith(("ETTOT", "ETTEF", "ETAZ", "ETBE", "ETFZ", "ETGU", "ETOQ"))]
                console.print(
                    f"[green]✅ INSEE {label} : {len(df_source):,} communes, "
                    f"{len(df_source.columns):,} colonnes[/green]"
                )
                if revenu_cols:
                    console.print(f"[dim]  Colonnes revenu : {revenu_cols[:12]}[/dim]")
                if entreprise_cols:
                    console.print(f"[dim]  Colonnes entreprises : {entreprise_cols[:12]}[/dim]")

        except Exception as e:
            console.print(f"[yellow]⚠️  INSEE {filename}: {e}[/yellow]")

    if not dfs:
        return pd.DataFrame()

    merged = dfs[0]
    for part in dfs[1:]:
        new_cols = [c for c in part.columns if c == "CODGEO" or c not in merged.columns]
        if len(new_cols) > 1:
            merged = merged.merge(part[new_cols], on="CODGEO", how="left")

    console.print(
        f"[green]✅ INSEE fusionné : {len(merged):,} communes, "
        f"{len(merged.columns):,} colonnes[/green]"
    )
    return merged


def _rows_for_code(df: pd.DataFrame, col_code: str, code_insee: str) -> pd.DataFrame:
    """Retourne les lignes INSEE correspondant à une commune, avec agrégation PLM possible."""
    code = str(code_insee).zfill(5)
    codes = df[col_code].astype(str).str.strip().str.replace(r"\.0$", "", regex=True).str.zfill(5)

    row = df[codes == code]
    if not row.empty:
        return row

    # Paris, Lyon, Marseille : certains fichiers INSEE diffusent les arrondissements.
    prefixes = {
        "75056": r"^751\d{2}$",
        "69123": r"^6938\d$",
        "13055": r"^132\d{2}$",
    }
    pattern = prefixes.get(code)
    if pattern:
        return df[codes.str.match(pattern, na=False)]

    return row


def approximate_median_age_from_bins(r: pd.Series, df: pd.DataFrame) -> Optional[float]:
    """
    Estime l'âge médian à partir des classes d'âge INSEE.

    L'INSEE ne fournit pas toujours une colonne d'âge médian directement dans
    le dossier complet communal. Cette fonction reconstruit donc une médiane
    approximative par interpolation linéaire dans les classes d'âge disponibles.
    """
    candidates_by_year = [
        [
            ("P23_POP0014", 0, 14), ("P23_POP1529", 15, 29),
            ("P23_POP3044", 30, 44), ("P23_POP4559", 45, 59),
            ("P23_POP6074", 60, 74), ("P23_POP75P", 75, 95),
        ],
        [
            ("P22_POP0014", 0, 14), ("P22_POP1529", 15, 29),
            ("P22_POP3044", 30, 44), ("P22_POP4559", 45, 59),
            ("P22_POP6074", 60, 74), ("P22_POP75P", 75, 95),
        ],
        [
            ("P21_POP0014", 0, 14), ("P21_POP1529", 15, 29),
            ("P21_POP3044", 30, 44), ("P21_POP4559", 45, 59),
            ("P21_POP6074", 60, 74), ("P21_POP75P", 75, 95),
        ],
        [
            ("P20_POP0014", 0, 14), ("P20_POP1529", 15, 29),
            ("P20_POP3044", 30, 44), ("P20_POP4559", 45, 59),
            ("P20_POP6074", 60, 74), ("P20_POP75P", 75, 95),
        ],
    ]

    for bins in candidates_by_year:
        values = []
        for col, age_min, age_max in bins:
            if col not in df.columns:
                continue
            v = to_float(r.get(col))
            if v is not None and v > 0:
                values.append((v, age_min, age_max))

        if len(values) < 3:
            continue

        total = sum(v for v, _, _ in values)
        if total <= 0:
            continue

        half = total / 2
        cumulative = 0.0

        for count, age_min, age_max in values:
            previous = cumulative
            cumulative += count
            if cumulative >= half:
                within_class = (half - previous) / count
                median_age = age_min + within_class * (age_max - age_min)
                return round(median_age, 1)

    return None


def extract_rp_indicators(df: pd.DataFrame, code_insee: str) -> dict:
    result = {
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
    if df.empty:
        return result

    col_code = next(
        (c for c in ["CODGEO", "COM", "code_insee", "code_commune", "CODGEO_2024", "CODGEO_2025"] if c in df.columns),
        None,
    )
    if not col_code:
        return result

    rows = _rows_for_code(df, col_code, code_insee)
    if rows.empty:
        return result

    def col_values(candidates):
        for col in candidates:
            if col in df.columns:
                vals = rows[col].map(to_float).dropna()
                if not vals.empty:
                    return col, vals
        return None, pd.Series(dtype=float)

    def get_sum(candidates):
        _, vals = col_values(candidates)
        vals = vals[vals > 0]
        return float(vals.sum()) if not vals.empty else None

    def get_first(candidates):
        _, vals = col_values(candidates)
        vals = vals[vals > 0]
        return float(vals.iloc[0]) if not vals.empty else None

    def get_weighted(candidates, weight_candidates):
        col, vals = col_values(candidates)
        if col is None:
            return None

        weights_col, weights = col_values(weight_candidates)
        tmp = pd.DataFrame({"v": rows[col].map(to_float)})
        if weights_col:
            tmp["w"] = rows[weights_col].map(to_float)
        else:
            tmp["w"] = 1.0

        tmp = tmp.dropna()
        tmp = tmp[(tmp["v"] > 0) & (tmp["w"] > 0)]
        if tmp.empty:
            return None
        return float((tmp["v"] * tmp["w"]).sum() / tmp["w"].sum())

    pop = get_sum([
        "P23_POP", "P22_POP", "P21_POP", "P20_POP",
        "PMUN23", "PMUN22", "PMUN21", "PMUN20",
        "population", "pop",
    ])

    if pop:
        result["population"] = int(round(pop))
        pop_safe = max(pop, 1)

        chomeurs = get_sum(["P23_CHOM1564", "P22_CHOM1564", "P21_CHOM1564", "P20_CHOM1564"])
        actifs = get_sum(["P23_ACT1564", "P22_ACT1564", "P21_ACT1564", "P20_ACT1564"])
        if chomeurs and actifs and actifs > 0:
            result["taux_chomage"] = round(chomeurs / actifs * 100, 1)

        # Âge médian : on agrège les classes d'âge si plusieurs lignes PLM.
        synthetic = {}
        for col in df.columns:
            if re.match(r"^P\d{2}_POP(0014|1529|3044|4559|6074|75P)$", str(col)):
                synthetic[col] = rows[col].map(to_float).dropna().sum()
        result["age_median"] = approximate_median_age_from_bins(pd.Series(synthetic), df)

        # Revenu / niveau de vie médian.
        # MED_SL23 est la colonne officielle de la base du comparateur de territoires.
        result["revenu_median"] = get_weighted([
            "MED_SL23", "MED_SL22", "MED_SL21",
            "MED23", "MED22", "MED21", "MED20",
            "DISP_MED23", "DISP_MED22", "DISP_MED21", "DISP_MED20",
            "MED_NIVEAU_VIE23", "MED_NIVEAU_VIE22", "MED_NIVEAU_VIE21", "MED_NIVEAU_VIE20",
            "revenu_median", "niveau_vie_median",
        ], [
            "P22_POP", "P23_POP", "P21_POP", "P20_POP", "P22_MEN"
        ])

        log_vac = get_sum([
            "P23_LOGVAC", "P23_LOG_VAC",
            "P22_LOGVAC", "P22_LOG_VAC",
            "P21_LOGVAC", "P21_LOG_VAC",
            "P20_LOGVAC", "P20_LOG_VAC",
        ])
        log_tot = get_sum(["P23_LOG", "P22_LOG", "P21_LOG", "P20_LOG"])
        if log_vac is not None and log_tot and log_tot > 0:
            result["taux_logements_vacants"] = round(log_vac / log_tot * 100, 1)

        pop_0014 = get_sum(["P23_POP0014", "P22_POP0014", "P21_POP0014", "P20_POP0014"])
        if pop_0014 is not None:
            result["pct_moins_15_ans"] = round(pop_0014 / pop_safe * 100, 1)

        # Part des 65 ans et plus.
        # Les fichiers INSEE changent souvent les noms : on essaie d'abord une colonne 65P,
        # puis on reconstruit depuis les classes 60-74 / 75P avec approximation.
        pop_65p = get_sum(["P23_POP65P", "P22_POP65P", "P21_POP65P", "P20_POP65P"])
        if pop_65p is not None:
            result["pct_plus_65_ans"] = round(pop_65p / pop_safe * 100, 1)
        else:
            pop_6579 = get_sum(["P23_POP6579", "P22_POP6579", "P21_POP6579", "P20_POP6579"])
            pop_65_79 = get_sum(["P23_POP6579", "P22_POP6579", "P21_POP6579", "P20_POP6579"])
            pop_60_74 = get_sum(["P23_POP6074", "P22_POP6074", "P21_POP6074", "P20_POP6074"])
            pop_75p = get_sum(["P23_POP75P", "P22_POP75P", "P21_POP75P", "P20_POP75P"])
            pop_80p = get_sum(["P23_POP80P", "P22_POP80P", "P21_POP80P", "P20_POP80P"])

            if pop_6579 is not None and pop_75p is not None:
                result["pct_plus_65_ans"] = round((pop_6579 + pop_75p) / pop_safe * 100, 1)
            elif pop_65_79 is not None and pop_80p is not None:
                result["pct_plus_65_ans"] = round((pop_65_79 + pop_80p) / pop_safe * 100, 1)
            elif pop_60_74 is not None and pop_75p is not None:
                # Approximation : dans la classe 60-74, 10 années sur 15 sont >=65 ans.
                result["pct_plus_65_ans"] = round(((pop_60_74 * (10 / 15)) + pop_75p) / pop_safe * 100, 1)

        nb_entreprises = get_sum(["ETTOT24", "ETTOT23", "ETTOT22", "nb_entreprises"])
        if nb_entreprises is not None:
            result["nb_entreprises"] = int(round(nb_entreprises))
            result["entreprises_pour_1000"] = round(nb_entreprises / (pop_safe / 1000), 2)

    return result


def extract_demo_indicators(df: pd.DataFrame, code_insee: str, pop: int) -> dict:
    result = {
        "taux_natalite": None,
        "evolution_population_pct": None,
    }
    if df.empty or not pop:
        return result

    col_code = next((c for c in ["CODGEO", "COM"] if c in df.columns), None)
    if not col_code:
        return result

    rows = _rows_for_code(df, col_code, code_insee)
    if rows.empty:
        return result

    def get_sum(candidates):
        for col in candidates:
            if col in df.columns:
                vals = rows[col].map(to_float).dropna()
                vals = vals[vals > 0]
                if not vals.empty:
                    return float(vals.sum())
        return None

    pop_safe = max(pop, 1)

    # Préférence au dernier état civil disponible.
    naissances = get_sum(["NAISD24", "NAISD23", "NAISD22", "NAISD21", "NAISD20"])
    if naissances:
        result["taux_natalite"] = round(naissances / (pop_safe / 1000), 1)

    pop_ancien = get_sum(["P16_POP", "P17_POP", "P15_POP"])
    if pop_ancien and pop_ancien > 0:
        result["evolution_population_pct"] = round((pop - pop_ancien) / pop_ancien * 100, 1)

    return result
