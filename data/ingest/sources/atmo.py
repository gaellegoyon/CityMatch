"""
data/ingest/sources/atmo.py
───────────────────────────
Qualité de l'air réelle pour CityMatch.

Sources utilisées :
- cache national/local ATMO si disponible ;
- tentative de téléchargement national via data.gouv.fr ;
- fallback régional ATMO Occitanie ;
- fallback régional Air Breizh pour quelques pages publiques validées.

Aucune estimation statistique n'est réalisée.
Si aucune source officielle exploitable n'est trouvée, la valeur reste NULL.
"""

from __future__ import annotations

import json
import re
import unicodedata
import zipfile
from pathlib import Path
from typing import Any, Final

import pandas as pd
import requests

from data.ingest.config import CACHE_DIR, HTTP_TIMEOUT
from data.ingest.utils import console, read_csv_flexible, to_float


USER_AGENT: Final[str] = "CityMatch/1.0"
MIN_CACHE_SIZE_BYTES: Final[int] = 1_000
DOWNLOAD_CHUNK_SIZE: Final[int] = 8192

_atmo_cache: pd.DataFrame | None = None


ATMO_NATIONAL_CACHE_FILES: Final[tuple[str, ...]] = (
    "atmo_commune.csv",
    "atmo_air_quality_commune.csv",
    "indice_atmo_commune.csv",
    "atmo_indice_qualite_air_commune.csv",
)

ATMO_OCCITANIE_URLS: Final[tuple[str, ...]] = (
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
    (
        "https://data.82amenagement.fr/api/explore/v2.1/catalog/datasets/"
        "indice-quotidien-de-qualite-de-lair-pour-les-collectivites-territoriales/"
        "exports/csv?lang=fr&timezone=Europe%2FParis&use_labels=true&delimiter=%3B"
    ),
    (
        "https://dservices9.arcgis.com/7Sr9Ek9c1QTKmbwr/arcgis/rest/services/"
        "ind_occitanie/FeatureServer/0/query?where=1%3D1&outFields=*&f=geojson"
    ),
)

ATMO_OCCITANIE_CITY_ALIASES: Final[dict[str, str]] = {
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

ATMO_OCCITANIE_PAGE_SLUGS: Final[dict[str, str]] = {
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

AIR_BREIZH_PAGE_SLUGS: Final[dict[str, str]] = {
    "35238": "rennes",
    "29019": "brest",
    "29232": "quimper",
    "56121": "lorient",
    "56260": "vannes",
    "35288": "saint-malo",
    "22278": "saint-brieuc",
}


def _headers() -> dict[str, str]:
    """Retourne les headers HTTP utilisés par l'ingestion."""
    return {"User-Agent": USER_AGENT}


def _strip_accents(value: Any) -> str:
    """Normalise une chaîne pour comparaison robuste."""
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _normalize_insee_code(value: Any) -> str:
    """
    Normalise un code INSEE.

    Gère :
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
    """Construit un mapping nom_colonne_normalisé -> nom_colonne_original."""
    return {
        _strip_accents(column).replace(" ", "_"): str(column).strip()
        for column in dataframe.columns
    }


def _score_atmo_from_label(value: Any) -> float | None:
    """
    Convertit un libellé ATMO en score CityMatch 0-10.

    Échelle utilisée :
    - bon → 10 ;
    - moyen → 8 ;
    - dégradé → 6 ;
    - mauvais → 4 ;
    - très mauvais → 2 ;
    - extrêmement mauvais → 0.
    """
    text = _strip_accents(value)

    mapping = [
        ("extremement mauvais", 0.0),
        ("tres mauvais", 2.0),
        ("mauvais", 4.0),
        ("degrade", 6.0),
        ("moyen", 8.0),
        ("bon", 10.0),
    ]

    for label, score in mapping:
        if label in text:
            return score

    return None


def _score_atmo_from_index(value: Any) -> float | None:
    """
    Convertit un indice ATMO numérique 1-6 en score CityMatch 0-10.

    Indice ATMO :
    1 = bon, 6 = extrêmement mauvais.
    """
    number = to_float(value)

    if number is None:
        return None

    if 1 <= number <= 6:
        return round(max(0.0, min(10.0, 10.0 - (number - 1.0) * 2.0)), 1)

    return None


def _score_direct_0_10(value: Any) -> float | None:
    """Interprète une colonne déjà exprimée sur 0-10."""
    number = to_float(value)

    if number is None:
        return None

    if 0 <= number <= 10:
        return round(max(0.0, min(10.0, number)), 1)

    return None


def _guess_download_suffix(url: str, fmt: str = "") -> str:
    """Déduit l'extension locale d'une ressource téléchargée."""
    blob = f"{url} {fmt}".lower()

    if ".zip" in blob or "zip" == fmt.lower():
        return ".zip"

    if ".gz" in blob or "gzip" in blob:
        return ".csv.gz"

    if ".json" in blob or "geojson" in blob or "json" == fmt.lower():
        return ".json"

    return ".csv"


def _is_probably_html(first_chunk: bytes) -> bool:
    """Détecte une page HTML/API doc téléchargée par erreur."""
    head = first_chunk.decode("utf-8", errors="ignore").lower()
    return "<html" in head or "swagger" in head or "openapi" in head


def _write_stream_to_file(
    response: requests.Response,
    target_path: Path,
) -> bool:
    """Écrit une réponse streamée vers un fichier temporaire puis remplace la cible."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_suffix(target_path.suffix + ".part")

    first_chunk = b""

    with temp_path.open("wb") as file:
        for chunk in response.iter_content(DOWNLOAD_CHUNK_SIZE):
            if not chunk:
                continue

            if not first_chunk:
                first_chunk = chunk[:200]

            file.write(chunk)

    if _is_probably_html(first_chunk):
        temp_path.unlink(missing_ok=True)
        return False

    if not temp_path.exists() or temp_path.stat().st_size < MIN_CACHE_SIZE_BYTES:
        temp_path.unlink(missing_ok=True)
        return False

    temp_path.replace(target_path)
    return True


def _download_data_gouv_atmo_resource(
    query: str,
    cache_filename: str,
) -> Path | None:
    """
    Recherche et télécharge une ressource CSV/ZIP/JSON exploitable depuis data.gouv.fr.

    Cette fonction est volontairement spécifique à ATMO pour éviter de récupérer
    une documentation HTML ou un PDF non exploitable.
    """
    cache_path = CACHE_DIR / cache_filename

    if cache_path.exists() and cache_path.stat().st_size > MIN_CACHE_SIZE_BYTES:
        return cache_path

    api_url = "https://www.data.gouv.fr/api/1/datasets/"

    try:
        response = requests.get(
            api_url,
            params={"q": query, "page_size": 10},
            timeout=HTTP_TIMEOUT,
            headers=_headers(),
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        console.print(f"[yellow]⚠️  Recherche data.gouv ATMO impossible : {exc}[/yellow]")
        return None

    datasets = payload.get("data", [])

    if not isinstance(datasets, list):
        return None

    candidates: list[tuple[int, str, str]] = []

    for dataset in datasets:
        if not isinstance(dataset, dict):
            continue

        dataset_title = str(dataset.get("title") or "")

        for resource in dataset.get("resources", []) or []:
            if not isinstance(resource, dict):
                continue

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
            if "json" in blob or "geojson" in blob:
                score += 15
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

            if any(bad in blob for bad in ["api/doc", "documentation", ".pdf", "html", "swagger"]):
                score -= 200

            if score > 0:
                candidates.append((score, url, fmt))

    candidates.sort(reverse=True)

    for score, url, fmt in candidates:
        suffix = _guess_download_suffix(url, fmt)
        target_path = cache_path.with_suffix(suffix)

        try:
            console.print(f"[blue]⬇️  Téléchargement data.gouv ATMO : {url}[/blue]")

            with requests.get(
                url,
                timeout=HTTP_TIMEOUT,
                stream=True,
                headers=_headers(),
            ) as response:
                if response.status_code != 200:
                    console.print(f"[yellow]⚠️  HTTP {response.status_code} pour {url}[/yellow]")
                    continue

                if _write_stream_to_file(response, target_path):
                    console.print(
                        f"[green]✅ Ressource data.gouv ATMO : {target_path.name} "
                        f"({target_path.stat().st_size // 1024} KB, score={score})[/green]"
                    )
                    return target_path

                console.print("[yellow]⚠️  Ressource data.gouv ATMO ignorée : contenu inexploitable[/yellow]")

        except Exception as exc:
            console.print(f"[yellow]⚠️  Téléchargement data.gouv ATMO impossible : {exc}[/yellow]")

    return None


def _build_score_from_columns(
    dataframe: pd.DataFrame,
    index: pd.Index,
    score_col: str | None,
    idx_col: str | None,
    label_col: str | None,
) -> pd.Series:
    """Construit une série de scores 0-10 depuis les colonnes détectées."""
    if score_col:
        return dataframe.loc[index, score_col].map(_score_direct_0_10)

    if idx_col:
        scores = dataframe.loc[index, idx_col].map(_score_atmo_from_index)

        if scores.dropna().empty and label_col:
            scores = dataframe.loc[index, label_col].map(_score_atmo_from_label)

        return scores

    if label_col:
        return dataframe.loc[index, label_col].map(_score_atmo_from_label)

    return pd.Series([None] * len(index), index=index)


def _normalize_atmo_dataframe(raw_df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalise un export ATMO en table communale :
        code_insee, qualite_air_score
    """
    if raw_df.empty:
        return pd.DataFrame(columns=["code_insee", "qualite_air_score"])

    dataframe = raw_df.copy()
    dataframe.columns = [str(column).strip().replace("\ufeff", "") for column in dataframe.columns]
    column_map = _lower_column_map(dataframe)

    code_col = next(
        (
            column_map.get(name)
            for name in [
                "code_insee",
                "codgeo",
                "code_commune",
                "code_com",
                "insee",
                "commune_code",
                "code_zone",
                "code_zone_insee",
                "code_territoire",
                "code_collectivite",
            ]
            if column_map.get(name) is not None
        ),
        None,
    )

    score_col = next(
        (
            column
            for column in dataframe.columns
            if _strip_accents(column).replace(" ", "_")
            in {"qualite_air_score", "score_air", "air_score", "score"}
        ),
        None,
    )

    idx_col = next(
        (
            column
            for column in dataframe.columns
            if _strip_accents(column).replace(" ", "_")
            in {
                "indice_atmo",
                "indice",
                "code_qual",
                "code_qualite",
                "qualite",
                "code_qual_air",
                "valeur_indice",
                "code_indice",
                "indice_qualite_air",
            }
        ),
        None,
    )

    label_col = next(
        (
            column
            for column in dataframe.columns
            if any(
                keyword in _strip_accents(column)
                for keyword in [
                    "lib qual",
                    "qualificatif",
                    "libelle",
                    "qualite de l air",
                    "libelle indice",
                    "lib indice",
                ]
            )
        ),
        None,
    )

    if not code_col:
        return pd.DataFrame(columns=["code_insee", "qualite_air_score"])

    out = pd.DataFrame(index=dataframe.index)
    out["code_insee"] = dataframe[code_col].map(_normalize_insee_code)
    out = out[out["code_insee"].str.match(r"^(\d{5}|2[AB]\d{3})$", na=False)]

    if out.empty:
        return pd.DataFrame(columns=["code_insee", "qualite_air_score"])

    out["qualite_air_score"] = _build_score_from_columns(
        dataframe=dataframe,
        index=out.index,
        score_col=score_col,
        idx_col=idx_col,
        label_col=label_col,
    )

    out["qualite_air_score"] = pd.to_numeric(out["qualite_air_score"], errors="coerce")
    out = out.dropna(subset=["code_insee", "qualite_air_score"])

    if out.empty:
        return pd.DataFrame(columns=["code_insee", "qualite_air_score"])

    out["qualite_air_score"] = out["qualite_air_score"].clip(lower=0, upper=10)
    out = out.groupby("code_insee", as_index=False)["qualite_air_score"].median()
    out["qualite_air_score"] = out["qualite_air_score"].round(1)

    return out


def _score_from_row(
    row: pd.Series,
    score_col: str | None,
    idx_col: str | None,
    label_col: str | None,
) -> float | None:
    """Calcule un score depuis une ligne ATMO."""
    if score_col:
        score = _score_direct_0_10(row.get(score_col))

        if score is not None:
            return score

    if idx_col:
        score = _score_atmo_from_index(row.get(idx_col))

        if score is not None:
            return score

    if label_col:
        score = _score_atmo_from_label(row.get(label_col))

        if score is not None:
            return score

    return None


def _normalize_atmo_occitanie_dataframe(raw_df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalise le jeu ATMO Occitanie.

    Si un vrai code INSEE communal est présent, il est utilisé.
    Sinon, certains territoires publiés par ATMO Occitanie sont rattachés à une
    ville centrale connue, sans inventer de score.
    """
    if raw_df.empty:
        return pd.DataFrame(columns=["code_insee", "qualite_air_score"])

    dataframe = raw_df.copy()
    dataframe.columns = [str(column).strip().replace("\ufeff", "") for column in dataframe.columns]
    column_map = _lower_column_map(dataframe)

    score_col = next(
        (
            column_map.get(name)
            for name in ["qualite_air_score", "score_air", "air_score", "score"]
            if column_map.get(name) is not None
        ),
        None,
    )

    idx_col = next(
        (
            column_map.get(name)
            for name in [
                "code_qual",
                "code_qualite",
                "indice_atmo",
                "indice",
                "valeur_indice",
                "code_indice",
                "code_qual_air",
            ]
            if column_map.get(name) is not None
        ),
        None,
    )

    label_col = next(
        (
            column_map.get(name)
            for name in [
                "lib_qual",
                "libelle_qualite",
                "lib_qual_air",
                "qualificatif",
                "libelle_indice",
                "qualite",
            ]
            if column_map.get(name) is not None
        ),
        None,
    )

    if not any([score_col, idx_col, label_col]):
        console.print(
            f"[yellow]⚠️  ATMO Occitanie : colonne indice introuvable. "
            f"Colonnes={list(dataframe.columns)}[/yellow]"
        )
        return pd.DataFrame(columns=["code_insee", "qualite_air_score"])

    rows: list[dict[str, Any]] = []

    code_col = next(
        (
            column_map.get(name)
            for name in [
                "code_insee",
                "codgeo",
                "code_commune",
                "code_com",
                "insee",
                "code_insee_commune",
                "commune_code",
            ]
            if column_map.get(name) is not None
        ),
        None,
    )

    if code_col:
        for _, row in dataframe.iterrows():
            code = _normalize_insee_code(row.get(code_col))

            if not re.fullmatch(r"\d{5}", code):
                continue

            score = _score_from_row(
                row=row,
                score_col=score_col,
                idx_col=idx_col,
                label_col=label_col,
            )

            if score is not None:
                rows.append({"code_insee": code, "qualite_air_score": score})

    zone_col = next(
        (
            column_map.get(name)
            for name in [
                "lib_zone",
                "nom_zone",
                "zone",
                "nom_territoire",
                "territoire",
                "collectivite",
                "nom_collectivite",
                "lib_collectivite",
                "epci",
                "nom_epci",
                "lib_epci",
                "nom",
                "name",
            ]
            if column_map.get(name) is not None
        ),
        None,
    )

    if not zone_col:
        zone_col = next(
            (
                column
                for column in dataframe.columns
                if any(keyword in _strip_accents(column) for keyword in ["zone", "territoire", "collectiv", "epci"])
                and not any(keyword in _strip_accents(column) for keyword in ["qual", "indice", "polluant"])
            ),
            None,
        )

    if zone_col:
        aliases = {
            _strip_accents(name): code
            for name, code in ATMO_OCCITANIE_CITY_ALIASES.items()
        }

        for _, row in dataframe.iterrows():
            zone = _strip_accents(row.get(zone_col, ""))

            if not zone:
                continue

            matched_code = None

            for alias, code in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
                if alias and alias in zone:
                    matched_code = code
                    break

            if not matched_code:
                continue

            score = _score_from_row(
                row=row,
                score_col=score_col,
                idx_col=idx_col,
                label_col=label_col,
            )

            if score is not None:
                rows.append({"code_insee": matched_code, "qualite_air_score": score})

    if not rows:
        console.print(
            "[yellow]⚠️  ATMO Occitanie : aucune ville rattachée. "
            f"Colonnes={list(dataframe.columns)}[/yellow]"
        )
        return pd.DataFrame(columns=["code_insee", "qualite_air_score"])

    out = pd.DataFrame(rows)
    out["code_insee"] = out["code_insee"].map(_normalize_insee_code)
    out["qualite_air_score"] = pd.to_numeric(out["qualite_air_score"], errors="coerce")
    out = out.dropna(subset=["code_insee", "qualite_air_score"])
    out["qualite_air_score"] = out["qualite_air_score"].clip(lower=0, upper=10)
    out = out.groupby("code_insee", as_index=False)["qualite_air_score"].median()
    out["qualite_air_score"] = out["qualite_air_score"].round(1)

    console.print(
        "[dim]ATMO Occitanie codes rattachés : "
        f"{', '.join(sorted(out['code_insee'].astype(str).unique())[:20])}"
        f"{'...' if len(out) > 20 else ''}[/dim]"
    )

    return out


def _read_atmo_file(path: Path) -> pd.DataFrame:
    """Lit un fichier ATMO CSV, JSON ou GeoJSON."""
    if not path.exists():
        return pd.DataFrame()

    suffix = path.suffix.lower()

    if suffix in {".geojson", ".json"}:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return pd.DataFrame()

        features = data.get("features")

        if isinstance(features, list):
            records = [
                feature.get("properties") or {}
                for feature in features
                if isinstance(feature, dict)
            ]
            return pd.DataFrame(records)

        if isinstance(data, list):
            return pd.DataFrame(data)

        if isinstance(data, dict):
            return pd.DataFrame(data.get("data", []))

        return pd.DataFrame()

    return read_csv_flexible(path)


def _download_atmo_occitanie() -> Path | None:
    """Télécharge l'export ATMO Occitanie si disponible et exploitable."""
    cache_csv = CACHE_DIR / "atmo_occitanie_collectivites.csv"
    cache_geojson = CACHE_DIR / "atmo_occitanie_collectivites.geojson"

    for existing_path in (cache_csv, cache_geojson):
        if existing_path.exists() and existing_path.stat().st_size > MIN_CACHE_SIZE_BYTES:
            raw = _read_atmo_file(existing_path)
            normalized = _normalize_atmo_occitanie_dataframe(raw)

            if not normalized.empty:
                console.print(
                    f"[dim]Cache ATMO Occitanie valide : {existing_path.name} "
                    f"({existing_path.stat().st_size // 1024} KB)[/dim]"
                )
                return existing_path

            console.print(f"[yellow]⚠️  Cache ATMO Occitanie inexploitable ignoré : {existing_path.name}[/yellow]")
            existing_path.unlink(missing_ok=True)

    for url in ATMO_OCCITANIE_URLS:
        is_geojson = "f=geojson" in url.lower()
        cache_path = cache_geojson if is_geojson else cache_csv

        try:
            console.print("[blue]⬇️  Téléchargement ATMO Occitanie...[/blue]")

            with requests.get(
                url,
                timeout=HTTP_TIMEOUT,
                stream=True,
                headers=_headers(),
            ) as response:
                if response.status_code != 200:
                    console.print(f"[yellow]⚠️  ATMO Occitanie HTTP {response.status_code}[/yellow]")
                    continue

                if not _write_stream_to_file(response, cache_path):
                    console.print("[yellow]⚠️  ATMO Occitanie téléchargé mais inexploitable[/yellow]")
                    continue

            raw = _read_atmo_file(cache_path)
            normalized = _normalize_atmo_occitanie_dataframe(raw)

            if not normalized.empty:
                console.print(
                    f"[green]✅ ATMO Occitanie ({cache_path.stat().st_size / 1024 / 1024:.1f} MB)[/green]"
                )
                return cache_path

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


def _extract_atmo_score_from_html(page_html: str) -> tuple[float | None, str | None, str]:
    """
    Extrait un indice ATMO depuis une page publique régionale.

    Retour :
        (score, label, méthode)
    """
    raw = str(page_html or "")
    normalized = _strip_accents(raw)

    numeric_patterns = [
        r'"(?:indice|code_qual|codeQual|qualite|iqa)"\s*:\s*"?([1-6])"?',
        r"(?:indice|qualite|iqa)[^0-9]{0,40}([1-6])",
    ]

    for pattern in numeric_patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE)

        if not match:
            continue

        score = _score_atmo_from_index(match.group(1))

        if score is not None:
            return round(float(score), 1), _label_from_atmo_score(float(score)), "numeric_pattern"

    label_patterns = [
        r"indice\s+du\s+jour\s+(bon|moyen|degrade|dégradé|mauvais|tres mauvais|très mauvais|extremement mauvais|extrêmement mauvais)",
        r"qualite\s+de\s+l\s+air[^a-z0-9]{0,80}(bon|moyen|degrade|mauvais|tres mauvais|extremement mauvais)",
    ]

    for pattern in label_patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)

        if not match:
            continue

        label = match.group(1)
        score = _score_atmo_from_label(label)

        if score is not None:
            return round(float(score), 1), label, "label_pattern"

    label_candidates = [
        ("extrêmement mauvais", ["extremement mauvais", "extrêmement mauvais"]),
        ("très mauvais", ["tres mauvais", "très mauvais"]),
        ("dégradé", ["degrade", "dégradé"]),
        ("mauvais", ["mauvais"]),
        ("moyen", ["moyen"]),
        ("bon", ["bon"]),
    ]
    context_words = [
        "indice",
        "indice du jour",
        "qualite de l air",
        "qualite",
        "air",
        "iqa",
        "aujourd hui",
        "prevision",
    ]

    best: tuple[int, str] | None = None

    for label, variants in label_candidates:
        for variant in variants:
            normalized_variant = _strip_accents(variant)

            for match in re.finditer(re.escape(normalized_variant), normalized):
                start = max(0, match.start() - 120)
                end = min(len(normalized), match.end() + 120)
                context = normalized[start:end]
                context_score = sum(1 for word in context_words if word in context)

                candidate = (context_score, label)

                if best is None or candidate[0] > best[0]:
                    best = candidate

    if best and best[0] > 0:
        label = best[1]
        score = _score_atmo_from_label(label)

        if score is not None:
            return round(float(score), 1), label, f"label_context_{best[0]}"

    return None, None, "not_found"


def _download_public_atmo_page(url: str, cache_name: str) -> str | None:
    """Télécharge une page publique ATMO/Air Breizh avec cache local."""
    cache_path = CACHE_DIR / cache_name

    if cache_path.exists() and cache_path.stat().st_size > MIN_CACHE_SIZE_BYTES:
        try:
            return cache_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass

    try:
        with requests.get(
            url,
            timeout=45,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        ) as response:
            if response.status_code != 200:
                console.print(f"[yellow]⚠️  ATMO page HTTP {response.status_code} : {url}[/yellow]")
                return None

            page = response.text
            cache_path.write_text(page, encoding="utf-8", errors="replace")
            return page

    except Exception as exc:
        console.print(f"[yellow]⚠️  ATMO page inaccessible {url}: {exc}[/yellow]")
        return None


def _build_air_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Construit un DataFrame code_insee / qualite_air_score depuis des lignes."""
    if not rows:
        return pd.DataFrame(columns=["code_insee", "qualite_air_score"])

    dataframe = pd.DataFrame(rows)
    dataframe["code_insee"] = dataframe["code_insee"].map(_normalize_insee_code)
    dataframe["qualite_air_score"] = pd.to_numeric(dataframe["qualite_air_score"], errors="coerce")
    dataframe = dataframe.dropna(subset=["code_insee", "qualite_air_score"])
    dataframe = dataframe[dataframe["code_insee"] != ""]
    dataframe["qualite_air_score"] = dataframe["qualite_air_score"].clip(lower=0, upper=10)
    dataframe = dataframe.groupby("code_insee", as_index=False)["qualite_air_score"].median()
    dataframe["qualite_air_score"] = dataframe["qualite_air_score"].round(1)

    return dataframe


def _load_atmo_occitanie_pages() -> pd.DataFrame:
    """Fallback réel ATMO Occitanie par pages publiques ville."""
    rows: list[dict[str, Any]] = []

    for code, slug in ATMO_OCCITANIE_PAGE_SLUGS.items():
        url = f"https://www.atmo-occitanie.org/{slug}?indice=iqa"
        page = _download_public_atmo_page(url, f"atmo_occitanie_page_{slug}.html")

        if not page:
            continue

        score, label, method = _extract_atmo_score_from_html(page)

        if score is None:
            console.print(f"[yellow]⚠️  ATMO Occitanie page non parsée : {slug} ({method})[/yellow]")
            continue

        rows.append(
            {
                "code_insee": _normalize_insee_code(code),
                "qualite_air_score": score,
            }
        )
        console.print(f"[dim]ATMO Occitanie page : {slug} → {score}/10 ({label}, {method})[/dim]")

    return _build_air_dataframe(rows)


def _load_air_breizh_pages() -> pd.DataFrame:
    """Fallback réel Air Breizh par pages publiques ville."""
    rows: list[dict[str, Any]] = []

    for code, slug in AIR_BREIZH_PAGE_SLUGS.items():
        url = f"https://www.airbreizh.asso.fr/ville/{slug}/"
        page = _download_public_atmo_page(url, f"airbreizh_page_{slug}.html")

        if not page:
            continue

        score, label, method = _extract_atmo_score_from_html(page)

        if score is None:
            console.print(f"[yellow]⚠️  Air Breizh page non parsée : {slug} ({method})[/yellow]")
            continue

        rows.append(
            {
                "code_insee": _normalize_insee_code(code),
                "qualite_air_score": score,
            }
        )
        console.print(f"[dim]Air Breizh page : {slug} → {score}/10 ({label}, {method})[/dim]")

    return _build_air_dataframe(rows)


def _merge_atmo_sources(parts: list[pd.DataFrame]) -> pd.DataFrame:
    """
    Fusionne les sources ATMO en conservant la première source disponible.

    Ordre attendu :
    1. national/local ;
    2. ATMO Occitanie open data ;
    3. ATMO Occitanie pages publiques ville ;
    4. Air Breizh pages publiques ville.
    """
    clean_parts: list[pd.DataFrame] = []

    for part in parts:
        if part is None or part.empty:
            continue

        if not {"code_insee", "qualite_air_score"}.issubset(part.columns):
            continue

        dataframe = part[["code_insee", "qualite_air_score"]].copy()
        dataframe["code_insee"] = dataframe["code_insee"].map(_normalize_insee_code)
        dataframe["qualite_air_score"] = pd.to_numeric(dataframe["qualite_air_score"], errors="coerce")
        dataframe = dataframe.dropna(subset=["code_insee", "qualite_air_score"])
        dataframe = dataframe[dataframe["code_insee"] != ""]
        dataframe["qualite_air_score"] = dataframe["qualite_air_score"].clip(lower=0, upper=10).round(1)

        if not dataframe.empty:
            clean_parts.append(dataframe)

    if not clean_parts:
        return pd.DataFrame(columns=["code_insee", "qualite_air_score"])

    merged = pd.concat(clean_parts, ignore_index=True)
    merged = merged.drop_duplicates(subset=["code_insee"], keep="first")

    return merged[["code_insee", "qualite_air_score"]]


def _load_national_or_local_atmo_cache() -> pd.DataFrame:
    """Charge un cache national/local ATMO existant si exploitable."""
    for filename in ATMO_NATIONAL_CACHE_FILES:
        path = CACHE_DIR / filename

        if not path.exists():
            continue

        dataframe = read_csv_flexible(path)
        normalized = _normalize_atmo_dataframe(dataframe)

        if not normalized.empty:
            console.print(f"[green]✅ ATMO cache national/local : {len(normalized):,} communes[/green]")
            return normalized

    return pd.DataFrame(columns=["code_insee", "qualite_air_score"])


def _load_downloaded_national_atmo() -> pd.DataFrame:
    """Télécharge et normalise une source nationale ATMO via data.gouv si possible."""
    downloaded = _download_data_gouv_atmo_resource(
        query="indice qualité air quotidien commune indice atmo",
        cache_filename="atmo_indice_qualite_air_commune.csv",
    )

    if downloaded is None or not downloaded.exists():
        return pd.DataFrame(columns=["code_insee", "qualite_air_score"])

    try:
        if downloaded.suffix.lower() == ".zip":
            zip_parts: list[pd.DataFrame] = []

            with zipfile.ZipFile(downloaded) as zip_file:
                for name in zip_file.namelist():
                    if not name.lower().endswith(".csv"):
                        continue

                    raw_path = CACHE_DIR / f"_atmo_extract_{Path(name).name}"

                    with zip_file.open(name) as file:
                        raw_path.write_bytes(file.read())

                    raw = read_csv_flexible(raw_path)
                    normalized = _normalize_atmo_dataframe(raw)

                    if not normalized.empty:
                        zip_parts.append(normalized)

            if not zip_parts:
                return pd.DataFrame(columns=["code_insee", "qualite_air_score"])

            merged = pd.concat(zip_parts, ignore_index=True)
            merged = merged.groupby("code_insee", as_index=False)["qualite_air_score"].median()
            merged["qualite_air_score"] = merged["qualite_air_score"].round(1)

            console.print(f"[green]✅ ATMO téléchargé national : {len(merged):,} communes[/green]")
            return merged

        raw = _read_atmo_file(downloaded)
        normalized = _normalize_atmo_dataframe(raw)

        if not normalized.empty:
            console.print(f"[green]✅ ATMO téléchargé national : {len(normalized):,} communes[/green]")
            return normalized

    except Exception as exc:
        console.print(f"[yellow]⚠️  ATMO national téléchargé mais inexploitable : {exc}[/yellow]")

    return pd.DataFrame(columns=["code_insee", "qualite_air_score"])


def load_atmo_air_quality() -> pd.DataFrame:
    """
    Charge la qualité de l'air réelle disponible.

    Aucun score n'est estimé par médiane département/région/nationale.
    """
    global _atmo_cache

    if _atmo_cache is not None:
        return _atmo_cache

    parts: list[pd.DataFrame] = []

    national_cache = _load_national_or_local_atmo_cache()

    if not national_cache.empty:
        parts.append(national_cache)
    else:
        national_download = _load_downloaded_national_atmo()

        if not national_download.empty:
            parts.append(national_download)

    occ_path = _download_atmo_occitanie()

    if occ_path is not None and occ_path.exists():
        try:
            occ_raw = _read_atmo_file(occ_path)
            occ_norm = _normalize_atmo_occitanie_dataframe(occ_raw)

            if not occ_norm.empty:
                console.print(
                    f"[green]✅ ATMO Occitanie open data : "
                    f"{len(occ_norm):,} villes/territoires rattachés[/green]"
                )
                parts.append(occ_norm)

        except Exception as exc:
            console.print(f"[yellow]⚠️  ATMO Occitanie téléchargé mais inexploitable : {exc}[/yellow]")

    try:
        occ_pages = _load_atmo_occitanie_pages()

        if not occ_pages.empty:
            console.print(f"[green]✅ ATMO Occitanie pages ville : {len(occ_pages):,} villes[/green]")
            parts.append(occ_pages)

    except Exception as exc:
        console.print(f"[yellow]⚠️  ATMO Occitanie pages indisponibles : {exc}[/yellow]")

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


def extract_qualite_air_score(
    atmo_df: pd.DataFrame,
    code_insee: str,
) -> float | None:
    """Extrait le score de qualité de l'air d'une commune."""
    if atmo_df.empty:
        return None

    column_map = _lower_column_map(atmo_df)

    code_col = next(
        (
            column_map.get(name)
            for name in [
                "code_insee",
                "codgeo",
                "code_commune",
                "commune",
                "code_zone",
            ]
            if column_map.get(name) is not None
        ),
        None,
    )

    if not code_col:
        return None

    target_code = _normalize_insee_code(code_insee)

    rows = atmo_df[
        atmo_df[code_col].map(_normalize_insee_code) == target_code
    ]

    if rows.empty:
        return None

    score_col = next(
        (
            column
            for column in atmo_df.columns
            if _strip_accents(column).replace(" ", "_")
            in {"qualite_air_score", "score_air", "air_score", "score"}
        ),
        None,
    )

    if not score_col:
        return None

    values = rows[score_col].map(to_float).dropna()

    if values.empty:
        return None

    score = float(values.median())

    return round(max(0.0, min(10.0, score)), 1)
