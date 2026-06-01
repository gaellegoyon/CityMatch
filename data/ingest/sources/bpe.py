"""
data/ingest/sources/bpe.py

Chargement de la Base Permanente des Équipements (BPE) et extraction
des équipements utiles par commune.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Optional

import pandas as pd

from data.ingest.config import BPE_YEAR, CACHE_DIR
from data.ingest.utils import console, download_cached


def load_bpe_2024() -> pd.DataFrame:
    """
    Charge la BPE 2024 au format robuste.

    Cette version accepte les exports INSEE/Melodi récents qui peuvent avoir :
    - un format long : GEO_OBJECT + TYPE_EQUIPEMENT + OBS_VALUE ;
    - un format détail : DEPCOM + TYPEQU, une ligne = un équipement ;
    - un format large : une colonne par type d'équipement, ex. C101, D201...

    Elle détecte les colonnes aussi par leurs valeurs, pas seulement par leurs noms,
    car les exports BPE 2024 peuvent changer les libellés de colonnes.

    Retour : DataFrame agrégé avec colonnes DEPCOM, TYPEQU, NB.
    """
    urls = [
        ("https://www.insee.fr/fr/statistiques/fichier/8217527/DS_BPE_CSV_FR.zip", "BPE24_ENSEMBLE_CSV.zip"),
        ("https://www.insee.fr/fr/statistiques/fichier/8217527/DS_BPE_XLSX_FR.zip", "BPE24_ENSEMBLE.zip"),
        ("https://www.insee.fr/fr/statistiques/fichier/7766585/BPE23_ENSEMBLE.zip", "BPE23_ENSEMBLE.zip"),
    ]

    path = None
    for url, filename in urls:
        p = download_cached(url, filename)
        if p is not None and p.exists() and p.stat().st_size > 0:
            path = p
            break

    if path is None:
        console.print("[yellow]⚠️  BPE indisponible[/yellow]")
        return pd.DataFrame(columns=["DEPCOM", "TYPEQU", "NB"])

    def normalize_code_commune(v) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        if s == "" or s.lower() in {"nan", "none", "na", "_z"}:
            return None

        # Formats fréquents : COM-34172, GEO-COM-34172, FR-COM-34172, 34172, 34172.0
        s = re.sub(r"\.0$", "", s)
        s = re.sub(r"^(GEO-)?(COM|CODGEO|FR|FR-COM)-", "", s, flags=re.IGNORECASE)

        # Corse : 2A004 / 2B033
        m_corse = re.search(r"(2[AB]\d{3})$", s, flags=re.IGNORECASE)
        if m_corse:
            return m_corse.group(1).upper()

        # Commune numérique sur 5 caractères. On accepte 4 caractères pour les communes 01xxx
        # exportées sans le zéro initial, mais PAS 2 ou 3 caractères pour éviter de prendre DEP.
        m = re.search(r"(\d{5})$", s)
        if m:
            code = m.group(1)
        elif s.isdigit() and len(s) == 4:
            code = s.zfill(5)
        else:
            return None

        # Agrégation Paris/Lyon/Marseille : la BPE localise aux arrondissements.
        if re.match(r"^751\d{2}$", code):
            return "75056"
        if re.match(r"^6938\d$", code):
            return "69123"
        if re.match(r"^132\d{2}$", code):
            return "13055"

        return code if re.match(r"^\d{5}$", code) else None

    def extract_type_code(v) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip().upper()
        if s == "" or s.lower() in {"nan", "none", "na", "_z"}:
            return None
        # Accepte "C101", "C101 - ECOLE", "TYPE-C101", etc.
        m = re.search(r"\b([A-G]\d{3})\b", s)
        return m.group(1) if m else None

    def clean_columns(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df.columns = [str(c).strip().replace("\ufeff", "") for c in df.columns]
        df = df.dropna(axis=1, how="all")
        return df

    def score_code_column(s: pd.Series) -> int:
        sample = s.dropna().astype(str).head(1000)
        if sample.empty:
            return 0
        return sum(1 for v in sample if normalize_code_commune(v) is not None)

    def score_type_column(s: pd.Series) -> int:
        sample = s.dropna().astype(str).head(1000)
        if sample.empty:
            return 0
        return sum(1 for v in sample if extract_type_code(v) is not None)

    def detect_code_col(df: pd.DataFrame) -> Optional[str]:
        if df.empty:
            return None
        exact = [
            "DEPCOM", "CODGEO", "COM", "GEO", "GEO_OBJECT", "geo_object",
            "code_insee", "code_commune", "Code commune", "Code géographique",
            "Code geographique", "OBS_GEO", "REF_AREA",
        ]
        lower_map = {str(c).strip().lower(): c for c in df.columns}
        for name in exact:
            c = lower_map.get(name.lower())
            if c is not None:
                # Vérifier que ce n'est pas seulement DEP/REG déguisé.
                if score_code_column(df[c]) >= 5:
                    return c

        best_col, best_score = None, 0
        for c in df.columns:
            lc = str(c).lower()
            if lc in {"dep", "reg", "annee", "year"}:
                continue
            score = score_code_column(df[c])
            # Bonus si le nom ressemble à une géographie communale.
            if any(tok in lc for tok in ["commune", "com", "codgeo", "depcom", "geo", "insee"]):
                score += 20
            if score > best_score:
                best_col, best_score = c, score
        return best_col if best_score >= 10 else None

    def detect_type_col(df: pd.DataFrame) -> Optional[str]:
        if df.empty:
            return None
        exact = [
            "TYPEQU", "TYPE_EQUIPEMENT", "TYPE_EQUIP", "typequ", "type_equip",
            "type_equipement", "EQUIP", "EQUIPEMENT", "equipement",
            "Code équipement", "Code equipement", "code_equipement", "TYPEQU24",
        ]
        lower_map = {str(c).strip().lower(): c for c in df.columns}
        for name in exact:
            c = lower_map.get(name.lower())
            if c is not None and score_type_column(df[c]) >= 5:
                return c

        best_col, best_score = None, 0
        for c in df.columns:
            lc = str(c).lower()
            score = score_type_column(df[c])
            if any(tok in lc for tok in ["type", "equip", "équip", "bpe"]):
                score += 20
            if score > best_score:
                best_col, best_score = c, score
        return best_col if best_score >= 10 else None

    def is_year_like_series(vals: pd.Series) -> bool:
        """
        Évite de prendre une colonne année/période comme colonne de comptage.

        Les exports Melodi peuvent contenir des valeurs comme 2024, "2024",
        "TIME_PERIOD=2024" ou "2024-01". Dans les versions précédentes,
        cette colonne était parfois détectée comme NB, ce qui donnait des
        contrôles absurdes du type Montpellier = 39 700 équipements.
        """
        raw = vals.dropna().astype(str).str.strip()
        if raw.empty:
            return False

        # Extraction robuste d'une année même si la valeur contient du texte.
        year_txt = raw.str.extract(r"(19\d{2}|20\d{2}|21\d{2})", expand=False)
        years = pd.to_numeric(year_txt, errors="coerce").dropna()
        if years.empty:
            return False

        share_year = len(years) / max(len(raw), 1)
        top_freq = years.value_counts(normalize=True).iloc[0]
        return bool(share_year > 0.80 and top_freq > 0.50)

    def detect_value_col(df: pd.DataFrame, excluded: set[str]) -> Optional[str]:
        """
        Détecte une vraie colonne de comptage.

        Important : on NE prend plus "la meilleure colonne numérique" au hasard,
        car les exports INSEE/Melodi contiennent souvent TIME_PERIOD=2024.
        Si aucune colonne clairement nommée OBS_VALUE/NB/NOMBRE n'est trouvée,
        on considère que chaque ligne commune/type vaut 1.
        """
        exact = [
            "OBS_VALUE", "obs_value", "OBS_VALUE_NB",
            "NB", "nb", "NOMBRE", "nombre",
            "nb_equipements", "NB_EQUIP", "NB_EQU",
        ]
        lower_map = {str(c).strip().lower(): c for c in df.columns}

        for name in exact:
            c = lower_map.get(name.lower())
            if c is None or c in excluded:
                continue
            lc = str(c).lower()
            if any(tok in lc for tok in ["time", "annee", "année", "year", "periode", "period"]):
                continue
            if is_year_like_series(df[c]):
                continue

            vals = pd.to_numeric(
                df[c].astype(str).str.replace(",", ".", regex=False),
                errors="coerce",
            )
            if vals.notna().sum() >= 10:
                return c

        return None

    def type_columns_from_names(df: pd.DataFrame) -> dict[str, str]:
        mapping = {}
        for c in df.columns:
            code = extract_type_code(c)
            if code:
                mapping[c] = code
        return mapping

    def normalize_long_or_detail(df: pd.DataFrame, source_name: str) -> Optional[pd.DataFrame]:
        df = clean_columns(df)
        if df.empty or len(df.columns) < 2:
            return None

        # Cas prioritaire : export INSEE/Melodi BPE 2024.
        # Le fichier DS_BPE_2024_data.csv contient plusieurs géographies
        # dans la même colonne GEO : COM, DEP, EPCI, BV2022, UU2020...
        # Il faut impérativement garder GEO_OBJECT == "COM", sinon des codes
        # supra-communaux qui finissent par 5 chiffres sont pris pour des communes
        # et gonflent les équipements.
        melodi_cols = {"GEO", "GEO_OBJECT", "FACILITY_TYPE", "OBS_VALUE"}
        if melodi_cols.issubset(set(df.columns)):
            sub = df[df["GEO_OBJECT"].astype(str).str.upper().eq("COM")].copy()
            if sub.empty:
                return None

            out = pd.DataFrame()
            out["DEPCOM"] = sub["GEO"].map(normalize_code_commune)
            out["TYPEQU"] = sub["FACILITY_TYPE"].map(extract_type_code)
            out["NB"] = pd.to_numeric(
                sub["OBS_VALUE"].astype(str).str.replace(",", ".", regex=False),
                errors="coerce",
            ).fillna(0)

            out = out[out["DEPCOM"].notna() & out["TYPEQU"].notna()]
            out = out[out["NB"] > 0]
            if out.empty:
                return None

            out = out.groupby(["DEPCOM", "TYPEQU"], as_index=False)["NB"].sum()
            out["_source_bpe"] = source_name
            return out

        col_code = detect_code_col(df)
        col_type = detect_type_col(df)
        if not col_code or not col_type:
            return None

        out = pd.DataFrame()
        out["DEPCOM"] = df[col_code].map(normalize_code_commune)
        out["TYPEQU"] = df[col_type].map(extract_type_code)

        value_col = detect_value_col(df, {col_code, col_type})
        if value_col:
            nb = pd.to_numeric(
                df[value_col].astype(str).str.replace(",", ".", regex=False),
                errors="coerce",
            ).fillna(0)

            # Sécurité : si malgré tout la colonne détectée ressemble à une année
            # constante (ex. 2024), on repasse en comptage de lignes.
            if is_year_like_series(df[value_col]):
                out["NB"] = 1
            else:
                out["NB"] = nb
        else:
            # Base géolocalisée/détail : une ligne = un équipement.
            out["NB"] = 1

        out = out[out["DEPCOM"].notna() & out["TYPEQU"].notna()]
        out["NB"] = pd.to_numeric(out["NB"], errors="coerce").fillna(0)
        out = out[out["NB"] > 0]
        if out.empty:
            return None

        out = out.groupby(["DEPCOM", "TYPEQU"], as_index=False)["NB"].sum()
        out["_source_bpe"] = source_name
        return out

    def normalize_wide(df: pd.DataFrame, source_name: str) -> Optional[pd.DataFrame]:
        df = clean_columns(df)
        if df.empty:
            return None

        col_code = detect_code_col(df)
        if not col_code:
            return None

        type_col_map = type_columns_from_names(df)
        if not type_col_map:
            return None

        base = df[[col_code] + list(type_col_map.keys())].copy()
        base = base.rename(columns={col_code: "DEPCOM"})
        base["DEPCOM"] = base["DEPCOM"].map(normalize_code_commune)
        base = base[base["DEPCOM"].notna()]
        if base.empty:
            return None

        # Renommer les colonnes de types vers le code exact C101, D201...
        base = base.rename(columns=type_col_map)
        type_cols = sorted(set(type_col_map.values()))

        long = base.melt(
            id_vars=["DEPCOM"],
            value_vars=type_cols,
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

    def normalize_any_bpe(df: pd.DataFrame, source_name: str) -> Optional[pd.DataFrame]:
        # Le CSV Melodi est souvent long ; le XLSX commune est souvent large.
        for normalizer in (normalize_long_or_detail, normalize_wide):
            part = normalizer(df, source_name)
            if part is not None and not part.empty:
                return part
        return None

    def read_csv_attempts(raw_path: Path) -> list[pd.DataFrame]:
        """Essaie plusieurs lectures, y compris les exports Melodi avec lignes de métadonnées."""
        dfs = []
        for sep in [";", ",", "\t", "|"]:
            for enc in ["utf-8", "utf-8-sig", "latin-1", "cp1252"]:
                try:
                    df = pd.read_csv(raw_path, sep=sep, encoding=enc, dtype=str, low_memory=False, on_bad_lines="skip")
                    if len(df.columns) > 1:
                        dfs.append(df)
                except Exception:
                    pass
        # Certains fichiers ont quelques lignes de commentaire avant l'en-tête.
        for header in range(1, 8):
            for sep in [";", ",", "\t"]:
                try:
                    df = pd.read_csv(raw_path, sep=sep, dtype=str, low_memory=False, on_bad_lines="skip", header=header)
                    if len(df.columns) > 1:
                        dfs.append(df)
                except Exception:
                    pass
        return dfs

    loaded_parts: list[pd.DataFrame] = []
    inspected = 0

    try:
        with zipfile.ZipFile(path) as zf:
            files = [
                f for f in zf.namelist()
                if f.lower().endswith((".csv", ".txt", ".xlsx", ".xls"))
                and not Path(f).name.startswith("~$")
                and "__macosx" not in f.lower()
                and "metadata" not in Path(f).name.lower()
            ]
            console.print(f"[dim]BPE : {len(files)} fichier(s) à inspecter dans {path.name}[/dim]")

            for file_name in files:
                lower = file_name.lower()
                inspected += 1
                raw_path = CACHE_DIR / f"_bpe_extract_{inspected}_{Path(file_name).name}"

                try:
                    with zf.open(file_name) as f:
                        raw_path.write_bytes(f.read())

                    found = False

                    if lower.endswith((".csv", ".txt")):
                        for df_try in read_csv_attempts(raw_path):
                            part = normalize_any_bpe(df_try, Path(file_name).name)
                            if part is not None:
                                loaded_parts.append(part)
                                console.print(
                                    f"[green]  → BPE table retenue : {Path(file_name).name} "
                                    f"({len(part):,} couples commune-type)[/green]"
                                )
                                found = True
                                break

                        if not found:
                            # Log diagnostic court pour comprendre un nouveau format.
                            try:
                                preview = pd.read_csv(raw_path, sep=None, engine="python", dtype=str, nrows=3)
                                console.print(
                                    f"[yellow]  ⚠️ Non reconnu {Path(file_name).name} : "
                                    f"colonnes={list(preview.columns[:12])}[/yellow]"
                                )
                            except Exception:
                                console.print(f"[yellow]  ⚠️ Non reconnu {Path(file_name).name}[/yellow]")

                    elif lower.endswith((".xlsx", ".xls")):
                        try:
                            xls = pd.ExcelFile(raw_path)
                        except Exception as e:
                            console.print(f"[yellow]  ⚠️ Excel illisible {Path(file_name).name}: {e}[/yellow]")
                            continue

                        for sheet_name in xls.sheet_names:
                            for header in range(0, 15):
                                try:
                                    df_try = pd.read_excel(
                                        raw_path,
                                        sheet_name=sheet_name,
                                        dtype=str,
                                        header=header,
                                        engine="openpyxl",
                                    )
                                    part = normalize_any_bpe(df_try, f"{Path(file_name).name}::{sheet_name}")
                                    if part is not None:
                                        loaded_parts.append(part)
                                        console.print(
                                            f"[green]  → BPE table retenue : {Path(file_name).name} / "
                                            f"{sheet_name} header={header} ({len(part):,} couples commune-type)[/green]"
                                        )
                                        found = True
                                        break
                                except Exception:
                                    continue
                            if found:
                                break

                        if not found:
                            console.print(f"[yellow]  ⚠️ Excel non reconnu {Path(file_name).name}[/yellow]")

                except Exception as e:
                    console.print(f"[yellow]  ⚠️ BPE fichier ignoré {file_name}: {e}[/yellow]")

        if not loaded_parts:
            console.print("[yellow]⚠️  BPE chargée mais aucune table exploitable trouvée[/yellow]")
            return pd.DataFrame(columns=["DEPCOM", "TYPEQU", "NB"])

        df = pd.concat(loaded_parts, ignore_index=True)
        df = df.groupby(["DEPCOM", "TYPEQU"], as_index=False)["NB"].sum()
        df["NB"] = pd.to_numeric(df["NB"], errors="coerce").fillna(0)

        console.print(
            f"[green]✅ BPE {BPE_YEAR} : {len(df):,} couples commune-type, "
            f"{df['DEPCOM'].nunique():,} communes, {df['TYPEQU'].nunique():,} types[/green]"
        )
        console.print(f"[dim]BPE colonnes normalisées : {list(df.columns)}[/dim]")

        for code, nom in [("75056", "Paris"), ("13055", "Marseille"), ("69123", "Lyon"), ("34172", "Montpellier")]:
            nb = df.loc[df["DEPCOM"] == code, "NB"].sum()
            console.print(f"[dim]  BPE contrôle {nom} {code}: {int(nb)} unités BPE agrégées[/dim]")

        return df

    except Exception as e:
        console.print(f"[yellow]⚠️  Erreur BPE : {e}[/yellow]")
        return pd.DataFrame(columns=["DEPCOM", "TYPEQU", "NB"])


def extract_bpe_for_commune(bpe_df: pd.DataFrame, code_insee: str) -> dict:
    result = {
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

    if bpe_df.empty:
        return result

    required = {"DEPCOM", "TYPEQU"}
    if not required.issubset(set(bpe_df.columns)):
        return result

    sub = bpe_df[
        bpe_df["DEPCOM"].astype(str).str.zfill(5) == str(code_insee).zfill(5)
    ].copy()

    if sub.empty:
        return result

    sub["TYPEQU"] = sub["TYPEQU"].astype(str).str.strip().str.upper()

    if "NB" in sub.columns:
        sub["NB"] = pd.to_numeric(sub["NB"], errors="coerce").fillna(0)

        # Garde-fou contre une mauvaise détection de colonne année :
        # si NB est quasi toujours 2024/2023, on compte les lignes au lieu de sommer NB.
        nums = sub["NB"].dropna()
        if not nums.empty:
            share_year = nums.between(1900, 2100).mean()
            top_freq = nums.value_counts(normalize=True).iloc[0]
            if share_year > 0.80 and top_freq > 0.50:
                sub["NB"] = 1
    else:
        sub["NB"] = 1

    def sum_codes(codes: list[str]) -> int:
        codes = [c.upper() for c in codes]
        val = sub.loc[sub["TYPEQU"].isin(codes), "NB"].sum()
        return int(round(float(val))) if pd.notna(val) else 0

    def sum_prefix(prefix: str) -> int:
        val = sub.loc[sub["TYPEQU"].str.startswith(prefix, na=False), "NB"].sum()
        return int(round(float(val))) if pd.notna(val) else 0

    # Codes BPE 2024 officiels.
    # Attention : les anciens codes BPE ont changé.
    # Exemples importants :
    # - A504 = restaurant/restauration rapide, pas crèche
    # - D502 = établissement d'accueil du jeune enfant
    # - D265 = médecin généraliste
    # - D277 = chirurgien-dentiste
    # - F307 = bibliothèque
    mapping = {
        "nb_creches": ["D502"],
        "nb_ecoles_primaires": ["C107", "C108", "C109"],
        "nb_colleges": ["C201"],
        "nb_lycees": ["C301", "C302", "C303", "C304", "C305"],
        "nb_medecins_generalistes": ["D265"],
        "nb_pharmacies": ["D307"],
        "nb_hopitaux": ["D101", "D102", "D103", "D104", "D105"],
        "nb_gares": ["E107", "E108", "E109"],
        # La BPE 2024 ne contient pas d'arrêt de bus communal comparable.
        "nb_piscines": ["F101"],
        "nb_bibliotheques": ["F307"],
        "nb_supermarches": ["B104", "B105"],
        "nb_restaurants": ["A504"],
        "nb_cinemas": ["F303"],
        # La BPE 2024 n'a plus un code unique "musée" dans ce fichier ;
        # on garde les équipements culturels proches.
        "nb_musees": ["F312", "F313"],
        "nb_dentistes": ["D277"],
        "nb_ophtalmologues": ["D270"],
        "nb_pediatres": ["D272"],
        "nb_urgences": ["D106"],
    }

    for field, codes in mapping.items():
        result[field] = sum_codes(codes)

    result["nb_equipements_sportifs"] = sum_prefix("F1")

    return result
