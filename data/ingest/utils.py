"""
data/ingest/utils.py
────────────────────
Fonctions utilitaires partagées pour l'ingestion CityMatch :
- téléchargement avec cache ;
- fallback data.gouv.fr ;
- lecture CSV robuste ;
- conversion numérique.
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from rich.console import Console

from data.ingest.config import CACHE_DIR, HTTP_TIMEOUT


console = Console()

USER_AGENT = "CityMatch/1.0"
DOWNLOAD_CHUNK_SIZE = 1024 * 128
MIN_VALID_DOWNLOAD_SIZE_BYTES = 1


def _safe_cache_path(filename: str) -> Path:
    """
    Construit un chemin sûr dans le dossier cache.

    Le nom ne doit pas permettre de sortir de CACHE_DIR.
    """
    clean_name = Path(filename).name

    if not clean_name:
        raise ValueError("Nom de fichier cache invalide.")

    return CACHE_DIR / clean_name


def _request_headers() -> dict[str, str]:
    """Retourne les headers HTTP utilisés par l'ingestion."""
    return {
        "User-Agent": USER_AGENT,
    }


def _format_size(size_bytes: int) -> str:
    """Formate une taille de fichier lisiblement."""
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / 1024 / 1024:.1f} MB"

    return f"{size_bytes // 1024} KB"


def _write_response_to_cache(response: requests.Response, cache_path: Path) -> Path | None:
    """
    Écrit une réponse HTTP dans le cache de manière atomique.

    On écrit d'abord dans un fichier temporaire, puis on remplace le fichier cible
    uniquement si le téléchargement est complet.
    """
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    temp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            dir=cache_path.parent,
            suffix=".tmp",
        ) as temp_file:
            temp_path = Path(temp_file.name)

            for chunk in response.iter_content(DOWNLOAD_CHUNK_SIZE):
                if chunk:
                    temp_file.write(chunk)

        if temp_path.stat().st_size < MIN_VALID_DOWNLOAD_SIZE_BYTES:
            temp_path.unlink(missing_ok=True)
            return None

        temp_path.replace(cache_path)
        return cache_path

    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def download_cached(
    url: str,
    filename: str,
    force: bool = False,
) -> Path | None:
    """
    Télécharge une URL dans data/cache avec réutilisation du cache.

    Retourne le chemin du fichier téléchargé, ou None en cas d'échec.
    """
    cache_path = _safe_cache_path(filename)

    if cache_path.exists() and not force:
        console.print(
            f"[dim]Cache : {cache_path.name} ({_format_size(cache_path.stat().st_size)})[/dim]"
        )
        return cache_path

    console.print(f"[blue]⬇️  Téléchargement : {cache_path.name}...[/blue]")

    try:
        with requests.get(
            url,
            timeout=HTTP_TIMEOUT,
            stream=True,
            headers=_request_headers(),
        ) as response:
            if response.status_code != 200:
                console.print(f"[yellow]⚠️  HTTP {response.status_code} pour {url}[/yellow]")
                return None

            downloaded_path = _write_response_to_cache(
                response=response,
                cache_path=cache_path,
            )

        if downloaded_path is None:
            console.print(f"[yellow]⚠️  Fichier vide : {cache_path.name}[/yellow]")
            return None

        console.print(
            f"[green]✅ {cache_path.name} ({_format_size(cache_path.stat().st_size)})[/green]"
        )
        return cache_path

    except requests.RequestException as exc:
        console.print(f"[yellow]⚠️  Erreur réseau téléchargement {cache_path.name}: {exc}[/yellow]")
        return None

    except OSError as exc:
        console.print(f"[yellow]⚠️  Erreur fichier téléchargement {cache_path.name}: {exc}[/yellow]")
        return None


def _is_usable_data_gouv_resource(resource: dict[str, Any]) -> bool:
    """Détermine si une ressource data.gouv est exploitable par l'ingestion."""
    url = str(resource.get("url") or "").lower()
    title = str(resource.get("title") or resource.get("description") or "").lower()
    fmt = str(resource.get("format") or "").lower()
    mime = str(resource.get("mime") or resource.get("mime_type") or "").lower()

    return (
        url.endswith((".csv", ".csv.gz", ".zip"))
        or fmt in {"csv", "zip", "gzip", "gz"}
        or "csv" in title
        or "zip" in title
        or "csv" in mime
        or "zip" in mime
    )


def download_data_gouv_csv_resource(
    query: str,
    filename: str,
    force: bool = False,
) -> Path | None:
    """
    Cherche sur data.gouv.fr un jeu de données correspondant à query et télécharge
    la première ressource CSV/ZIP exploitable.

    Utilisé comme fallback pour les sources dont l'URL exacte change côté producteur.
    """
    cache_path = _safe_cache_path(filename)

    if cache_path.exists() and not force:
        console.print(
            f"[dim]Cache : {cache_path.name} ({_format_size(cache_path.stat().st_size)})[/dim]"
        )
        return cache_path

    try:
        api_url = "https://www.data.gouv.fr/api/1/datasets/"

        response = requests.get(
            api_url,
            params={"q": query, "page_size": 10},
            timeout=HTTP_TIMEOUT,
            headers=_request_headers(),
        )

        if response.status_code != 200:
            console.print(f"[yellow]⚠️  data.gouv HTTP {response.status_code} pour {query}[/yellow]")
            return None

        payload = response.json()
        datasets = payload.get("data", [])

        if not isinstance(datasets, list):
            console.print(f"[yellow]⚠️  Réponse data.gouv inattendue pour : {query}[/yellow]")
            return None

        for dataset in datasets:
            if not isinstance(dataset, dict):
                continue

            resources = dataset.get("resources", [])

            if not isinstance(resources, list):
                continue

            for resource in resources:
                if not isinstance(resource, dict):
                    continue

                if not _is_usable_data_gouv_resource(resource):
                    continue

                resource_url = str(resource.get("url") or "").strip()

                if not resource_url:
                    continue

                console.print(f"[blue]⬇️  Téléchargement data.gouv : {cache_path.name}...[/blue]")

                try:
                    with requests.get(
                        resource_url,
                        timeout=HTTP_TIMEOUT,
                        stream=True,
                        headers=_request_headers(),
                    ) as resource_response:
                        if resource_response.status_code != 200:
                            continue

                        downloaded_path = _write_response_to_cache(
                            response=resource_response,
                            cache_path=cache_path,
                        )

                    if downloaded_path is not None and cache_path.exists():
                        console.print(
                            f"[green]✅ {cache_path.name} ({_format_size(cache_path.stat().st_size)})[/green]"
                        )
                        return cache_path

                except requests.RequestException:
                    continue

        console.print(f"[yellow]⚠️  Aucune ressource CSV/ZIP trouvée sur data.gouv pour : {query}[/yellow]")
        return None

    except requests.RequestException as exc:
        console.print(f"[yellow]⚠️  Recherche data.gouv impossible pour {query}: {exc}[/yellow]")
        return None

    except ValueError as exc:
        console.print(f"[yellow]⚠️  Réponse JSON data.gouv invalide pour {query}: {exc}[/yellow]")
        return None

    except OSError as exc:
        console.print(f"[yellow]⚠️  Écriture cache impossible pour {filename}: {exc}[/yellow]")
        return None


def read_csv_flexible(
    path: Path,
    **kwargs: Any,
) -> pd.DataFrame:
    """
    Lit un CSV avec plusieurs séparateurs et encodages possibles.

    Paramètres spéciaux acceptés dans kwargs :
    - seps : liste de séparateurs à tester ;
    - encs : liste d'encodages à tester ;
    - min_columns : nombre minimal de colonnes attendu.
    """
    seps = kwargs.pop("seps", [";", ",", "\t"])
    encs = kwargs.pop("encs", ["utf-8", "utf-8-sig", "latin-1", "cp1252"])
    min_columns = int(kwargs.pop("min_columns", 2))

    path = Path(path)

    if not path.exists():
        console.print(f"[yellow]⚠️  CSV introuvable : {path}[/yellow]")
        return pd.DataFrame()

    for encoding in encs:
        for separator in seps:
            try:
                dataframe = pd.read_csv(
                    path,
                    sep=separator,
                    encoding=encoding,
                    dtype=str,
                    low_memory=False,
                    on_bad_lines="skip",
                    **kwargs,
                )

                if len(dataframe.columns) >= min_columns:
                    return dataframe

            except UnicodeDecodeError:
                continue
            except pd.errors.ParserError:
                continue
            except ValueError:
                continue
            except OSError as exc:
                console.print(f"[yellow]⚠️  Lecture CSV impossible {path}: {exc}[/yellow]")
                return pd.DataFrame()

    console.print(f"[yellow]⚠️  Impossible de lire correctement le CSV : {path}[/yellow]")
    return pd.DataFrame()


def to_float(value: Any) -> float | None:
    """
    Convertit une valeur en float.

    Gère notamment :
    - espaces classiques ;
    - espaces insécables ;
    - virgule décimale française ;
    - valeurs nulles textuelles.
    """
    if value is None:
        return None

    if isinstance(value, float):
        return value if math.isfinite(value) else None

    if isinstance(value, int):
        return float(value)

    text = str(value).strip()

    if not text:
        return None

    normalized = (
        text.replace("\u202f", "")
        .replace("\xa0", "")
        .replace(" ", "")
        .replace(",", ".")
        .strip()
    )

    if normalized.lower() in {"", "nan", "none", "null", "na", "n/a", "-", "nd", "nc"}:
        return None

    try:
        number = float(normalized)
    except ValueError:
        return None

    if not math.isfinite(number):
        return None

    return number


def to_int(value: Any) -> int | None:
    """Convertit une valeur en entier si possible."""
    number = to_float(value)

    if number is None:
        return None

    return int(number)