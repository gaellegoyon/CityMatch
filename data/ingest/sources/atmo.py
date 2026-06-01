"""
data/ingest/sources/atmo.py

Qualité de l'air réelle :
- flux/cache national ATMO si disponible ;
- fallback régional ATMO Occitanie ;
- fallback régional Air Breizh pour les pages validées.

Aucune estimation statistique n'est réalisée. Si aucune source officielle
exploitable n'est trouvée, la valeur reste NULL.
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
import zipfile
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from data.ingest.config import CACHE_DIR, HTTP_TIMEOUT
from data.ingest.utils import console, download_cached, read_csv_flexible, to_float

# ── Source ATMO : qualité de l'air réelle si disponible ──────────────────────
_atmo_cache: Optional[pd.DataFrame] = None


def download_data_gouv_csv_resource(query: str, cache_filename: str) -> Optional[Path]:
    """
    Recherche et télécharge une ressource CSV/ZIP/GZ exploitable depuis data.gouv.fr.

    Cette fonction est volontairement défensive :
    - elle ignore les pages HTML, PDF et endpoints de documentation ;
    - elle garde le fichier en cache ;
    - elle retourne None si aucune ressource fiable n'est trouvée.

    Elle sert uniquement de tentative complémentaire pour le flux national ATMO.
    Les fallbacks régionaux officiels restent utilisés ensuite.
    """
    cache_path = CACHE_DIR / cache_filename

    if cache_path.exists() and cache_path.stat().st_size > 1000:
        return cache_path

    api_url = "https://www.data.gouv.fr/api/1/datasets/"
    try:
        response = requests.get(
            api_url,
            params={"q": query, "page_size": 10},
            timeout=HTTP_TIMEOUT,
            headers={"User-Agent": "CityMatch/1.0"},
        )
        if response.status_code != 200:
            console.print(f"[yellow]⚠️  data.gouv HTTP {response.status_code} pour {query}[/yellow]")
            return None

        payload = response.json()
        datasets = payload.get("data", []) or []
    except Exception as exc:
        console.print(f"[yellow]⚠️  Recherche data.gouv impossible : {exc}[/yellow]")
        return None

    candidates: list[tuple[int, str, str]] = []

    for dataset in datasets:
        dataset_title = str(dataset.get("title") or "")
        for resource in dataset.get("resources", []) or []:
            url = str(resource.get("url") or "")
            title = str(resource.get("title") or resource.get("description") or "")
            fmt = str(resource.get("format") or "")
            blob = f"{dataset_title} {title} {fmt} {url}".lower()

            if not url.startswith("http"):
                continue

            score = 0

            if "csv" in blob:
                score += 50
            if "zip" in blob:
                score += 25
            if "indice" in blob:
                score += 15
            if "atmo" in blob:
                score += 15
            if "commune" in blob or "communal" in blob:
                score += 15
            if "qualite" in blob or "qualité" in blob:
                score += 10
            if "air" in blob:
                score += 10

            # Ressources non exploitables pour pandas/read_csv_flexible.
            if any(bad in blob for bad in ["api/doc", "documentation", ".pdf", "html", "swagger"]):
                score -= 200

            if score > 0:
                candidates.append((score, url, fmt))

    candidates.sort(reverse=True)

    for score, url, fmt in candidates:
        try:
            suffix = _guess_download_suffix(url, fmt)
            target = cache_path.with_suffix(suffix) if suffix != cache_path.suffix else cache_path
            tmp = target.with_suffix(target.suffix + ".part")

            console.print(f"[blue]⬇️  Téléchargement data.gouv ATMO : {url}[/blue]")
            with requests.get(
                url,
                timeout=HTTP_TIMEOUT,
                stream=True,
                headers={"User-Agent": "CityMatch/1.0"},
            ) as resp:
                if resp.status_code != 200:
                    console.print(f"[yellow]⚠️  HTTP {resp.status_code} pour {url}[/yellow]")
                    continue

                first_chunk = b""
                with open(tmp, "wb") as fh:
                    for chunk in resp.iter_content(8192):
                        if not chunk:
                            continue
                        if not first_chunk:
                            first_chunk = chunk[:200]
                        fh.write(chunk)

            # Évite de garder une page HTML/API doc enregistrée en .csv.
            head = first_chunk.decode("utf-8", errors="ignore").lower()
            if "<html" in head or "swagger" in head or "openapi" in head:
                tmp.unlink(missing_ok=True)
                console.print("[yellow]⚠️  Ressource data.gouv ignorée : contenu non CSV/ZIP[/yellow]")
                continue

            if tmp.exists() and tmp.stat().st_size > 1000:
                tmp.replace(target)
                console.print(
                    f"[green]✅ Ressource data.gouv téléchargée : {target.name} "
                    f"({target.stat().st_size // 1024} KB, score={score})[/green]"
                )
                return target

            tmp.unlink(missing_ok=True)

        except Exception as exc:
            console.print(f"[yellow]⚠️  Téléchargement data.gouv impossible : {exc}[/yellow]")

    return None


def _guess_download_suffix(url: str, fmt: str = "") -> str:
    """Déduit l'extension locale à utiliser pour une ressource téléchargée."""
    blob = f"{url} {fmt}".lower()

    if ".zip" in blob or "zip" == str(fmt).lower():
        return ".zip"
    if ".gz" in blob or "gzip" in blob:
        return ".csv.gz"
    if ".json" in blob or "json" == str(fmt).lower():
        return ".json"

    return ".csv"


# Sources régionales utilisées en fallback réel pour les communes absentes
# du flux national ATMO. Ce n'est pas une estimation : ce sont des indices publiés
# par les AASQA régionales (ATMO Occitanie, Air Breizh).
ATMO_OCCITANIE_URLS = [
    # Opendatasoft officiel ATMO Occitanie — export CSV complet.
    (
        "https://82-opendata-occitanie.opendatasoft.com/api/explore/v2.1/catalog/datasets/"
        "indice-quotidien-de-qualite-de-lair-pour-les-collectivites-territoriales/"
        "exports/csv?lang=fr&timezone=Europe%2FParis&use_labels=true&delimiter=%3B"
    ),
    (
        "https://82-opendata-occitanie.opendatasoft.com/explore/dataset/"
        "indice-quotidien-de-qualite-de-lair-pour-les-collectivites-territoriales/"
        "download/?format=csv&timezone=Europe/Paris&use_labels_for_header=true"
    ),
    # Miroir parfois utilisé par la plateforme.
    (
        "https://data.82amenagement.fr/api/explore/v2.1/catalog/datasets/"
        "indice-quotidien-de-qualite-de-lair-pour-les-collectivites-territoriales/"
        "exports/csv?lang=fr&timezone=Europe%2FParis&use_labels=true&delimiter=%3B"
    ),
    # ArcGIS REST officiel : utilisé si les exports Opendatasoft changent.
    (
        "https://dservices9.arcgis.com/7Sr9Ek9c1QTKmbwr/arcgis/rest/services/"
        "ind_occitanie/FeatureServer/0/query?where=1%3D1&outFields=*&f=geojson"
    ),
]

# Codes INSEE des principales villes CityMatch d'Occitanie.
# Utilisé uniquement si le fichier régional fournit un nom de zone mais pas un
# code INSEE communal. On rattache alors l'indice réel publié pour le territoire
# ATMO correspondant à la ville centrale.
ATMO_OCCITANIE_CITY_ALIASES = {
    "toulouse": "31555",
    "toulouse metropole": "31555",
    "montpellier": "34172",
    "montpellier mediterranee": "34172",
    "nimes": "30189",
    "nîmes": "30189",
    "perpignan": "66136",
    "beziers": "34032",
    "béziers": "34032",
    "ales": "30007",
    "alès": "30007",
    "albi": "81004",
    "castres": "81065",
    "montauban": "82121",
    "tarbes": "65440",
    "lourdes": "65286",
    "rodez": "12202",
    "millau": "12145",
    "cahors": "46042",
    "auch": "32013",
    "foix": "09122",
    "carcassonne": "11069",
    "mende": "48095",
    "sete": "34301",
    "sète": "34301",
    "narbonne": "11262",
    "colomiers": "31149",
    "tournefeuille": "31557",
    "blagnac": "31069",
    "muret": "31395",
    "lunel": "34145",
    "agde": "34003",
    "frontignan": "34108",
    "mauguio": "34154",
    "plaisance du touch": "31424",
    "plaisance-du-touch": "31424",
    "castelnau le lez": "34057",
    "castelnau-le-lez": "34057",
    "balma": "31044",
    "cugnaux": "31157",
    "sete agglopole": "34301",
    "grand narbonne": "11262",
    "grand montauban": "82121",
}


# Slugs des pages publiques ATMO Occitanie testées/validées pour compléter
# les communes d'Occitanie absentes du flux national. On ne déduit rien :
# si la page ne répond pas ou ne contient pas d'indice exploitable, la ville reste NULL.
ATMO_OCCITANIE_PAGE_SLUGS = {
    "31555": "toulouse",
    "34172": "montpellier",
    "30189": "nimes",
    "66136": "perpignan",
    "34032": "beziers",
    "11262": "narbonne",
    "81004": "albi",
    "11069": "carcassonne",
    "34301": "sete",
    "30007": "ales",
    "65440": "tarbes",
    "81065": "castres",
    "31149": "colomiers",
    "31557": "tournefeuille",
    "34003": "agde",
    "31069": "blagnac",
    "34145": "lunel",
    "31395": "muret",
    "34057": "castelnau-le-lez",
    "12202": "rodez",
    "34108": "frontignan",
    "32013": "auch",
    "12145": "millau",
    "31424": "plaisance-du-touch",
    "31157": "cugnaux",
    "46042": "cahors",
}

# Pages publiques Air Breizh testées/validées.
# Certaines villes bretonnes (Lanester, Concarneau, Fougères, Lannion) n'ont pas
# de page ville directe exploitable chez Air Breizh : elles restent NULL.
AIR_BREIZH_PAGE_SLUGS = {
    "35238": "rennes",
    "29019": "brest",
    "29232": "quimper",
    "56121": "lorient",
    "56260": "vannes",
    "35288": "saint-malo",
    "22278": "saint-brieuc",
}


def _strip_accents(s: str) -> str:
    s = str(s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def _score_atmo_from_label(v) -> Optional[float]:
    if v is None:
        return None

    s = _strip_accents(v)
    mapping = [
        ("extremement mauvais", 0.0),
        ("tres mauvais", 2.0),
        ("mauvais", 4.0),
        ("degrade", 6.0),
        ("moyen", 8.0),
        ("bon", 10.0),
    ]
    for key, score in mapping:
        if key in s:
            return score
    return None


def _score_atmo_from_numeric(v) -> Optional[float]:
    num = to_float(v)
    if num is None:
        return None

    # Indice ATMO usuel : 1=bon, 2=moyen, 3=dégradé, 4=mauvais,
    # 5=très mauvais, 6=extrêmement mauvais.
    if 1 <= num <= 6:
        return max(0.0, min(10.0, 10.0 - (num - 1.0) * 2.0))

    # Certains exports peuvent déjà fournir un score 0-10.
    if 0 <= num <= 10:
        return max(0.0, min(10.0, num))

    return None


def _normalize_atmo_dataframe(raw_df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalise un export ATMO en table communale :
        code_insee, qualite_air_score

    Formats acceptés :
    - score déjà calculé : qualite_air_score / score_air / air_score ;
    - indice ATMO numérique : indice_atmo / code_qual / code_qualite ;
    - libellé qualité : Bon, Moyen, Dégradé, Mauvais, Très mauvais, Extrêmement mauvais.

    Aucun score n'est inventé : si aucune source ne donne la commune, le score reste NULL.
    """
    if raw_df.empty:
        return pd.DataFrame(columns=["code_insee", "qualite_air_score"])

    df = raw_df.copy()
    df.columns = [str(c).strip().replace("\ufeff", "") for c in df.columns]
    lower = {c.lower(): c for c in df.columns}

    code_col = next(
        (
            lower.get(name)
            for name in [
                "code_insee", "codgeo", "code_commune", "code_com", "insee",
                "code_zone", "code_zone_insee", "commune_code", "code territoire",
                "code_territoire", "code collectivite", "code_collectivite",
            ]
            if lower.get(name) is not None
        ),
        None,
    )

    score_col = next(
        (
            c for c in df.columns
            if c.lower() in {"qualite_air_score", "score_air", "air_score", "score"}
        ),
        None,
    )
    idx_col = next(
        (
            c for c in df.columns
            if c.lower() in {
                "indice_atmo", "indice", "code_qual", "code_qualite",
                "qualite", "code_qual_air", "valeur_indice", "code indice",
                "code_indice", "indice qualité air", "indice_qualite_air",
            }
        ),
        None,
    )
    label_col = next(
        (
            c for c in df.columns
            if any(
                k in c.lower()
                for k in [
                    "lib_qual", "qualificatif", "libelle", "libellé",
                    "qualite de l air", "qualité de l'air", "libelle_indice",
                    "lib_indice",
                ]
            )
        ),
        None,
    )

    def build_score(index) -> pd.Series:
        if score_col:
            vals = pd.to_numeric(
                df.loc[index, score_col].astype(str).str.replace(",", ".", regex=False),
                errors="coerce",
            )
            return vals.clip(lower=0, upper=10)

        if idx_col:
            scores = df.loc[index, idx_col].map(_score_atmo_from_numeric)
            # Si la colonne numérique ne marche pas, essayer le libellé.
            if scores.dropna().empty and label_col:
                scores = df.loc[index, label_col].map(_score_atmo_from_label)
            return scores

        if label_col:
            return df.loc[index, label_col].map(_score_atmo_from_label)

        return pd.Series([None] * len(index), index=index)

    frames: list[pd.DataFrame] = []

    # Cas normal : code INSEE communal directement disponible.
    if code_col:
        out = pd.DataFrame(index=df.index)
        out["code_insee"] = (
            df[code_col]
            .astype(str)
            .str.strip()
            .str.replace(r"\.0$", "", regex=True)
        )

        # Extraire un code commune à 5 chiffres même si le champ contient
        # un préfixe ou une chaîne plus longue.
        extracted = out["code_insee"].str.extract(r"(\d{5})", expand=False)
        out["code_insee"] = extracted.fillna(out["code_insee"]).str.zfill(5)
        out = out[out["code_insee"].str.match(r"^\d{5}$", na=False)]

        if not out.empty:
            out["qualite_air_score"] = build_score(out.index)
            frames.append(out[["code_insee", "qualite_air_score"]])

    if not frames:
        return pd.DataFrame(columns=["code_insee", "qualite_air_score"])

    out_all = pd.concat(frames, ignore_index=True)
    out_all["qualite_air_score"] = pd.to_numeric(out_all["qualite_air_score"], errors="coerce")
    out_all = out_all.dropna(subset=["code_insee", "qualite_air_score"])
    if out_all.empty:
        return pd.DataFrame(columns=["code_insee", "qualite_air_score"])

    out_all["qualite_air_score"] = out_all["qualite_air_score"].clip(lower=0, upper=10)
    out_all = out_all.groupby("code_insee", as_index=False)["qualite_air_score"].median()
    out_all["qualite_air_score"] = out_all["qualite_air_score"].round(1)
    return out_all


def _normalize_atmo_occitanie_dataframe(raw_df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalise le jeu ATMO Occitanie en vraie donnée CityMatch.

    Important :
    - Le jeu régional est souvent à l'échelle des EPCI/collectivités, pas toujours
      à l'échelle du code INSEE communal.
    - Quand un vrai code INSEE communal est présent, on l'utilise.
    - Sinon on rattache uniquement les villes connues via le nom de zone
      publié par ATMO Occitanie, par exemple :
          "Toulouse Métropole - Toulouse" → 31555
          "Montpellier Méditerranée Métropole - Montpellier" → 34172
    - Aucun score n'est estimé : on utilise seulement l'indice/libellé présent
      dans la source ATMO Occitanie.
    """
    if raw_df.empty:
        return pd.DataFrame(columns=["code_insee", "qualite_air_score"])

    df = raw_df.copy()
    df.columns = [str(c).strip().replace("\ufeff", "") for c in df.columns]

    def norm_col(c: str) -> str:
        return _strip_accents(c).replace(" ", "_")

    norm_to_col = {norm_col(c): c for c in df.columns}

    # ── 1) Colonnes de score / indice ───────────────────────────────────────
    score_col = next(
        (
            norm_to_col.get(name)
            for name in [
                "qualite_air_score", "score_air", "air_score", "score",
            ]
            if norm_to_col.get(name) is not None
        ),
        None,
    )

    idx_col = next(
        (
            norm_to_col.get(name)
            for name in [
                "code_qual", "code_qualite", "indice_atmo", "indice",
                "valeur_indice", "code_indice", "code_qual_air",
            ]
            if norm_to_col.get(name) is not None
        ),
        None,
    )

    label_col = next(
        (
            norm_to_col.get(name)
            for name in [
                "lib_qual", "libelle_qualite", "lib_qual_air",
                "qualificatif", "libelle_indice", "qualite",
            ]
            if norm_to_col.get(name) is not None
        ),
        None,
    )

    # Fallback colonnes score : éviter de confondre lib_zone avec lib_qual.
    if not label_col:
        label_col = next(
            (
                c for c in df.columns
                if any(k in norm_col(c) for k in ["qual", "indice"])
                and not any(k in norm_col(c) for k in ["zone", "territoire", "collectiv", "epci"])
            ),
            None,
        )

    if not any([score_col, idx_col, label_col]):
        console.print(f"[yellow]⚠️  ATMO Occitanie : colonne indice introuvable. Colonnes={list(df.columns)}[/yellow]")
        return pd.DataFrame(columns=["code_insee", "qualite_air_score"])

    def row_score(row) -> Optional[float]:
        score = None
        if score_col:
            score = to_float(row.get(score_col))
            if score is not None:
                return round(max(0.0, min(10.0, float(score))), 1)

        if idx_col:
            score = _score_atmo_from_numeric(row.get(idx_col))
            if score is not None:
                return round(float(score), 1)

        if label_col:
            score = _score_atmo_from_label(row.get(label_col))
            if score is not None:
                return round(float(score), 1)

        return None

    rows = []

    # ── 2) Cas code communal direct ──────────────────────────────────────────
    code_col = next(
        (
            norm_to_col.get(name)
            for name in [
                "code_insee", "codgeo", "code_commune", "code_com",
                "insee", "code_insee_commune", "commune_code",
            ]
            if norm_to_col.get(name) is not None
        ),
        None,
    )

    if code_col:
        for _, row in df.iterrows():
            raw_code = str(row.get(code_col, "")).strip()
            raw_code = re.sub(r"\.0$", "", raw_code)

            # On accepte uniquement un vrai code INSEE communal à 5 chiffres.
            # Les codes EPCI/SIREN à 9 chiffres ne sont PAS utilisés ici.
            m = re.fullmatch(r"\d{5}", raw_code)
            if not m:
                continue

            score = row_score(row)
            if score is not None:
                rows.append({"code_insee": raw_code, "qualite_air_score": score})

    # ── 3) Cas zone/territoire sans code INSEE communal ─────────────────────
    # Choix volontaire : on évite les colonnes lib_qual/libellé indice.
    zone_col = next(
        (
            norm_to_col.get(name)
            for name in [
                "lib_zone", "nom_zone", "zone", "nom_territoire",
                "territoire", "collectivite", "nom_collectivite",
                "lib_collectivite", "epci", "nom_epci", "lib_epci",
                "nom", "name",
            ]
            if norm_to_col.get(name) is not None
        ),
        None,
    )

    if not zone_col:
        zone_col = next(
            (
                c for c in df.columns
                if any(k in norm_col(c) for k in ["zone", "territoire", "collectiv", "epci"])
                and not any(k in norm_col(c) for k in ["qual", "indice", "polluant"])
            ),
            None,
        )

    if zone_col:
        aliases = {
            _strip_accents(name): code
            for name, code in ATMO_OCCITANIE_CITY_ALIASES.items()
        }

        for _, row in df.iterrows():
            zone = _strip_accents(row.get(zone_col, ""))
            if not zone:
                continue

            matched_code = None
            for alias, code in sorted(aliases.items(), key=lambda kv: len(kv[0]), reverse=True):
                if alias and alias in zone:
                    matched_code = code
                    break

            if not matched_code:
                continue

            score = row_score(row)
            if score is not None:
                rows.append({"code_insee": matched_code, "qualite_air_score": score})

    if not rows:
        console.print(
            "[yellow]⚠️  ATMO Occitanie : aucune ville rattachée. "
            f"Colonnes={list(df.columns)}[/yellow]"
        )
        return pd.DataFrame(columns=["code_insee", "qualite_air_score"])

    out = pd.DataFrame(rows)
    out["code_insee"] = out["code_insee"].astype(str).str.zfill(5)
    out["qualite_air_score"] = pd.to_numeric(out["qualite_air_score"], errors="coerce")
    out = out.dropna(subset=["qualite_air_score"])

    # La source contient plusieurs jours : on garde la médiane annuelle/disponible,
    # comme pour le national, pour ne pas dépendre d'une seule journée.
    out = out.groupby("code_insee", as_index=False)["qualite_air_score"].median()
    out["qualite_air_score"] = out["qualite_air_score"].clip(lower=0, upper=10).round(1)

    console.print(
        f"[dim]ATMO Occitanie codes rattachés : "
        f"{', '.join(sorted(out['code_insee'].astype(str).unique())[:20])}"
        f"{'...' if len(out) > 20 else ''}[/dim]"
    )
    return out


def _read_atmo_occitanie_file(path: Path) -> pd.DataFrame:
    """Lit CSV ou GeoJSON ATMO Occitanie."""
    if not path.exists():
        return pd.DataFrame()

    if path.suffix.lower() in {".geojson", ".json"}:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            features = data.get("features", [])
            records = []
            for feat in features:
                props = feat.get("properties") or {}
                if props:
                    records.append(props)
            return pd.DataFrame(records)
        except Exception:
            return pd.DataFrame()

    return read_csv_flexible(path)


def _download_atmo_occitanie() -> Optional[Path]:
    """
    Télécharge l'export ATMO Occitanie.

    On valide le contenu avant de garder le cache :
    l'ancien problème venait souvent d'un cache existant mais inexploitable
    ou d'un export où la colonne zone était mal détectée.
    """
    cache_csv = CACHE_DIR / "atmo_occitanie_collectivites.csv"
    cache_geojson = CACHE_DIR / "atmo_occitanie_collectivites.geojson"

    # Cache CSV/GeoJSON seulement s'il se normalise vraiment.
    for existing in [cache_csv, cache_geojson]:
        if existing.exists() and existing.stat().st_size > 1000:
            raw = _read_atmo_occitanie_file(existing)
            norm = _normalize_atmo_occitanie_dataframe(raw)
            if not norm.empty:
                console.print(f"[dim]Cache ATMO Occitanie valide : {existing.name} ({existing.stat().st_size // 1024} KB)[/dim]")
                return existing
            console.print(f"[yellow]⚠️  Cache ATMO Occitanie inexploitable ignoré : {existing.name}[/yellow]")
            existing.unlink(missing_ok=True)

    for url in ATMO_OCCITANIE_URLS:
        is_geojson = "f=geojson" in url.lower()
        cache_path = cache_geojson if is_geojson else cache_csv

        try:
            console.print("[blue]⬇️  Téléchargement ATMO Occitanie...[/blue]")
            with requests.get(url, timeout=HTTP_TIMEOUT, stream=True, headers={"User-Agent": "CityMatch/1.0"}) as resp:
                if resp.status_code != 200:
                    console.print(f"[yellow]⚠️  ATMO Occitanie HTTP {resp.status_code}[/yellow]")
                    continue

                tmp = cache_path.with_suffix(cache_path.suffix + ".part")
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_content(8192):
                        if chunk:
                            f.write(chunk)
                tmp.replace(cache_path)

            if cache_path.exists() and cache_path.stat().st_size > 1000:
                raw = _read_atmo_occitanie_file(cache_path)
                norm = _normalize_atmo_occitanie_dataframe(raw)
                if not norm.empty:
                    console.print(
                        f"[green]✅ ATMO Occitanie ({cache_path.stat().size if False else cache_path.stat().st_size / 1024 / 1024:.1f} MB)[/green]"
                    )
                    return cache_path

                console.print(f"[yellow]⚠️  ATMO Occitanie téléchargé mais non reconnu : {url}[/yellow]")
                cache_path.unlink(missing_ok=True)

        except Exception as exc:
            console.print(f"[yellow]⚠️  ATMO Occitanie indisponible : {exc}[/yellow]")

    return None


def _label_from_atmo_score(score: float) -> str:
    """Retourne un libellé indicatif à partir d'un score CityMatch 0-10."""
    if score >= 9:
        return "bon"
    if score >= 7:
        return "moyen"
    if score >= 5:
        return "dégradé"
    if score >= 3:
        return "mauvais"
    if score >= 1:
        return "très mauvais"
    return "extrêmement mauvais"


def _extract_atmo_score_from_html(page_html: str) -> tuple[Optional[float], Optional[str], str]:
    """
    Extrait un indice ATMO depuis une page publique régionale.

    Stratégies :
    - valeur numérique 1..6 proche de champs indice/iqa/code_qual ;
    - libellé ATMO avec contexte "indice", "qualité de l'air", etc.

    Retour : (score CityMatch 0-10, libellé, méthode). Si rien de fiable :
    (None, None, "not_found").
    """
    raw = str(page_html or "")
    normalized = _strip_accents(raw)

    # 1) Patterns numériques. Sur les pages ATMO Occitanie, c'est ce qui a été
    # validé en test : Toulouse → score 4.0 / label mauvais.
    numeric_patterns = [
        r'"(?:indice|code_qual|codeQual|qualite|iqa)"\s*:\s*"?([1-6])"?',
        r"(?:indice|qualite|iqa)[^0-9]{0,40}([1-6])",
    ]
    for pat in numeric_patterns:
        m = re.search(pat, raw, flags=re.IGNORECASE)
        if m:
            score = _score_atmo_from_numeric(m.group(1))
            if score is not None:
                return round(float(score), 1), _label_from_atmo_score(float(score)), "numeric_pattern"

    # 2) Patterns textuels Air Breizh / pages publiques.
    label_patterns = [
        r"indice\s+du\s+jour\s+(bon|moyen|degrade|dégradé|mauvais|tres mauvais|très mauvais|extremement mauvais|extrêmement mauvais)",
        r"qualite\s+de\s+l\s+air[^a-z0-9]{0,80}(bon|moyen|degrade|mauvais|tres mauvais|extremement mauvais)",
    ]
    for pat in label_patterns:
        m = re.search(pat, normalized, flags=re.IGNORECASE)
        if m:
            label = m.group(1)
            score = _score_atmo_from_label(label)
            if score is not None:
                return round(float(score), 1), label, "label_pattern"

    # 3) Fallback contextuel prudent : on exige que le libellé soit proche
    # d'un vocabulaire d'indice/air pour éviter de prendre une légende générique.
    label_candidates = [
        ("extrêmement mauvais", ["extremement mauvais", "extrêmement mauvais"]),
        ("très mauvais", ["tres mauvais", "très mauvais"]),
        ("dégradé", ["degrade", "dégradé"]),
        ("mauvais", ["mauvais"]),
        ("moyen", ["moyen"]),
        ("bon", ["bon"]),
    ]
    context_words = ["indice", "indice du jour", "qualite de l air", "qualite", "air", "iqa", "aujourd hui", "prevision"]
    best = None

    for label, variants in label_candidates:
        for variant in variants:
            v = _strip_accents(variant)
            for m in re.finditer(re.escape(v), normalized):
                start = max(0, m.start() - 120)
                end = min(len(normalized), m.end() + 120)
                ctx = normalized[start:end]
                context_score = sum(1 for w in context_words if w in ctx)
                candidate = (context_score, label)
                if best is None or candidate[0] > best[0]:
                    best = candidate

    if best and best[0] > 0:
        label = best[1]
        score = _score_atmo_from_label(label)
        if score is not None:
            return round(float(score), 1), label, f"label_context_{best[0]}"

    return None, None, "not_found"


def _download_public_atmo_page(url: str, cache_name: str) -> Optional[str]:
    """
    Télécharge une page publique ATMO/Air Breizh avec cache local.
    En cas d'erreur HTTP/timeout, on retourne None et la ville restera NULL.
    """
    cache_path = CACHE_DIR / cache_name

    if cache_path.exists() and cache_path.stat().st_size > 1000:
        try:
            return cache_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass

    try:
        with requests.get(
            url,
            timeout=45,
            headers={
                "User-Agent": "CityMatch/1.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        ) as resp:
            if resp.status_code != 200:
                console.print(f"[yellow]⚠️  ATMO page HTTP {resp.status_code} : {url}[/yellow]")
                return None
            page = resp.text
            cache_path.write_text(page, encoding="utf-8", errors="replace")
            return page
    except Exception as exc:
        console.print(f"[yellow]⚠️  ATMO page inaccessible {url}: {exc}[/yellow]")
        return None


def _load_atmo_occitanie_pages() -> pd.DataFrame:
    """
    Fallback réel ATMO Occitanie par pages publiques ville.

    Utilisé quand le flux national et l'open data tabulaire ne donnent pas la
    commune. Les pages ont été testées séparément avant intégration.
    """
    rows = []
    for code, slug in ATMO_OCCITANIE_PAGE_SLUGS.items():
        url = f"https://www.atmo-occitanie.org/{slug}?indice=iqa"
        page = _download_public_atmo_page(url, f"atmo_occitanie_page_{slug}.html")
        if not page:
            continue

        score, label, method = _extract_atmo_score_from_html(page)
        if score is None:
            console.print(f"[yellow]⚠️  ATMO Occitanie page non parsée : {slug} ({method})[/yellow]")
            continue

        rows.append({"code_insee": str(code).zfill(5), "qualite_air_score": score})
        console.print(f"[dim]ATMO Occitanie page : {slug} → {score}/10 ({label}, {method})[/dim]")

    if not rows:
        return pd.DataFrame(columns=["code_insee", "qualite_air_score"])

    out = pd.DataFrame(rows)
    out = out.groupby("code_insee", as_index=False)["qualite_air_score"].median()
    out["qualite_air_score"] = out["qualite_air_score"].round(1)
    return out


def _load_air_breizh_pages() -> pd.DataFrame:
    """
    Fallback réel Air Breizh par pages publiques ville.

    Couverture validée :
    Rennes, Brest, Quimper, Lorient, Vannes, Saint-Malo, Saint-Brieuc.
    Les autres villes bretonnes sans page officielle exploitable restent NULL.
    """
    rows = []
    for code, slug in AIR_BREIZH_PAGE_SLUGS.items():
        url = f"https://www.airbreizh.asso.fr/ville/{slug}/"
        page = _download_public_atmo_page(url, f"airbreizh_page_{slug}.html")
        if not page:
            continue

        score, label, method = _extract_atmo_score_from_html(page)
        if score is None:
            console.print(f"[yellow]⚠️  Air Breizh page non parsée : {slug} ({method})[/yellow]")
            continue

        rows.append({"code_insee": str(code).zfill(5), "qualite_air_score": score})
        console.print(f"[dim]Air Breizh page : {slug} → {score}/10 ({label}, {method})[/dim]")

    if not rows:
        return pd.DataFrame(columns=["code_insee", "qualite_air_score"])

    out = pd.DataFrame(rows)
    out = out.groupby("code_insee", as_index=False)["qualite_air_score"].median()
    out["qualite_air_score"] = out["qualite_air_score"].round(1)
    return out


def _merge_atmo_sources(parts: list[pd.DataFrame]) -> pd.DataFrame:
    """
    Fusionne les sources ATMO en conservant la première source disponible.
    Ordre attendu :
    1. national/cache ;
    2. ATMO Occitanie open data ;
    3. ATMO Occitanie pages publiques ville ;
    4. Air Breizh pages publiques ville.
    """
    clean_parts = []
    for part in parts:
        if part is None or part.empty:
            continue
        p = part[["code_insee", "qualite_air_score"]].copy()
        p["code_insee"] = p["code_insee"].astype(str).str.zfill(5)
        p["qualite_air_score"] = pd.to_numeric(p["qualite_air_score"], errors="coerce")
        p = p.dropna(subset=["code_insee", "qualite_air_score"])
        p["qualite_air_score"] = p["qualite_air_score"].clip(lower=0, upper=10).round(1)
        if not p.empty:
            clean_parts.append(p)

    if not clean_parts:
        return pd.DataFrame(columns=["code_insee", "qualite_air_score"])

    merged = pd.concat(clean_parts, ignore_index=True)
    merged = merged.drop_duplicates(subset=["code_insee"], keep="first")
    return merged[["code_insee", "qualite_air_score"]]


def load_atmo_air_quality() -> pd.DataFrame:
    """
    Charge la qualité de l'air réelle disponible.

    Ordre :
    1. cache local/national ATMO déjà préparé ;
    2. téléchargement automatique data.gouv si possible ;
    3. fallback réel ATMO Occitanie open data ;
    4. fallback réel ATMO Occitanie par pages publiques ville ;
    5. fallback réel Air Breizh par pages publiques ville ;
    6. sinon la commune reste NULL.

    Aucun score n'est estimé par médiane département/région/nationale.
    """
    global _atmo_cache
    if _atmo_cache is not None:
        return _atmo_cache

    parts: list[pd.DataFrame] = []

    # 1. Caches nationaux/locaux existants.
    candidates = [
        CACHE_DIR / "atmo_commune.csv",
        CACHE_DIR / "atmo_air_quality_commune.csv",
        CACHE_DIR / "indice_atmo_commune.csv",
        CACHE_DIR / "atmo_indice_qualite_air_commune.csv",
    ]

    for p in candidates:
        if p.exists():
            df = read_csv_flexible(p)
            normalized = _normalize_atmo_dataframe(df)
            if not normalized.empty:
                console.print(f"[green]✅ ATMO cache national/local : {len(normalized):,} communes[/green]")
                parts.append(normalized)
                break

    # 2. Téléchargement national via data.gouv si aucun cache national n'a marché.
    if not parts:
        downloaded = download_data_gouv_csv_resource(
            "indice qualité air quotidien commune indice atmo",
            "atmo_indice_qualite_air_commune.csv",
        )

        if downloaded and downloaded.exists():
            try:
                if downloaded.suffix.lower() == ".zip":
                    zip_parts = []
                    with zipfile.ZipFile(downloaded) as zf:
                        for name in zf.namelist():
                            if name.lower().endswith(".csv"):
                                raw_path = CACHE_DIR / f"_atmo_extract_{Path(name).name}"
                                with zf.open(name) as f:
                                    raw_path.write_bytes(f.read())
                                part = read_csv_flexible(raw_path)
                                norm = _normalize_atmo_dataframe(part)
                                if not norm.empty:
                                    zip_parts.append(norm)
                    if zip_parts:
                        normalized = pd.concat(zip_parts, ignore_index=True)
                        normalized = normalized.groupby("code_insee", as_index=False)["qualite_air_score"].median()
                        normalized["qualite_air_score"] = normalized["qualite_air_score"].round(1)
                        console.print(f"[green]✅ ATMO téléchargé national : {len(normalized):,} communes[/green]")
                        parts.append(normalized)
                else:
                    df = read_csv_flexible(downloaded)
                    normalized = _normalize_atmo_dataframe(df)
                    if not normalized.empty:
                        console.print(f"[green]✅ ATMO téléchargé national : {len(normalized):,} communes[/green]")
                        parts.append(normalized)
            except Exception as exc:
                console.print(f"[yellow]⚠️  ATMO national téléchargé mais inexploitable : {exc}[/yellow]")

    # 3. Fallback réel ATMO Occitanie open data.
    occ_path = _download_atmo_occitanie()
    if occ_path and occ_path.exists():
        try:
            occ_raw = _read_atmo_occitanie_file(occ_path)
            occ_norm = _normalize_atmo_occitanie_dataframe(occ_raw)
            if not occ_norm.empty:
                console.print(f"[green]✅ ATMO Occitanie open data : {len(occ_norm):,} villes/territoires rattachés[/green]")
                parts.append(occ_norm)

                if "31555" in set(occ_norm["code_insee"].astype(str).str.zfill(5)):
                    console.print("[green]✅ ATMO Occitanie open data : Toulouse disponible[/green]")
        except Exception as exc:
            console.print(f"[yellow]⚠️  ATMO Occitanie téléchargé mais inexploitable : {exc}[/yellow]")

    # 4. Fallback réel ATMO Occitanie par pages publiques ville.
    try:
        occ_pages = _load_atmo_occitanie_pages()
        if not occ_pages.empty:
            console.print(f"[green]✅ ATMO Occitanie pages ville : {len(occ_pages):,} villes[/green]")
            parts.append(occ_pages)
            if "31555" in set(occ_pages["code_insee"].astype(str).str.zfill(5)):
                console.print("[green]✅ ATMO Occitanie page : Toulouse disponible[/green]")
    except Exception as exc:
        console.print(f"[yellow]⚠️  ATMO Occitanie pages indisponibles : {exc}[/yellow]")

    # 5. Fallback réel Air Breizh par pages publiques ville.
    try:
        breizh_pages = _load_air_breizh_pages()
        if not breizh_pages.empty:
            console.print(f"[green]✅ Air Breizh pages ville : {len(breizh_pages):,} villes[/green]")
            parts.append(breizh_pages)
    except Exception as exc:
        console.print(f"[yellow]⚠️  Air Breizh pages indisponibles : {exc}[/yellow]")

    merged = _merge_atmo_sources(parts)
    if merged.empty:
        console.print("[yellow]⚠️  ATMO communal absent — qualite_air_score restera NULL[/yellow]")
    else:
        console.print(f"[green]✅ ATMO fusionné : {len(merged):,} communes avec qualité de l'air[/green]")

    _atmo_cache = merged
    return _atmo_cache


def extract_qualite_air_score(atmo_df: pd.DataFrame, code_insee: str) -> Optional[float]:
    if atmo_df.empty:
        return None

    col_code = next((c for c in ["code_insee", "CODGEO", "code_commune", "commune", "code_zone"] if c in atmo_df.columns), None)
    if not col_code:
        return None

    rows = atmo_df[atmo_df[col_code].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(5) == str(code_insee).zfill(5)]
    if rows.empty:
        return None

    score_col = next((c for c in atmo_df.columns if c.lower() in {"qualite_air_score", "score_air", "air_score", "score"}), None)
    if not score_col:
        return None

    vals = rows[score_col].map(to_float).dropna()
    if vals.empty:
        return None

    return round(max(0, min(10, float(vals.median()))), 1)
