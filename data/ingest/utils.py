"""
data/ingest/utils.py

Fonctions utilitaires partagées :
- téléchargement avec cache ;
- lecture CSV robuste ;
- conversion numérique.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from rich.console import Console

from data.ingest.config import CACHE_DIR, HTTP_TIMEOUT

console = Console()


def download_cached(url: str, filename: str, force: bool = False) -> Optional[Path]:
    cache_path = CACHE_DIR / filename
    if cache_path.exists() and not force:
        console.print(f"[dim]Cache : {filename} ({cache_path.stat().st_size // 1024} KB)[/dim]")
        return cache_path

    console.print(f"[blue]⬇️  Téléchargement : {filename}...[/blue]")
    try:
        with requests.get(url, timeout=HTTP_TIMEOUT, stream=True, headers={"User-Agent": "CityMatch/1.0"}) as resp:
            if resp.status_code != 200:
                console.print(f"[yellow]⚠️  HTTP {resp.status_code} pour {url}[/yellow]")
                return None
            with open(cache_path, "wb") as f:
                for chunk in resp.iter_content(8192):
                    if chunk:
                        f.write(chunk)
        console.print(f"[green]✅ {filename} ({cache_path.stat().st_size / 1024 / 1024:.1f} MB)[/green]")
        return cache_path
    except Exception as e:
        console.print(f"[yellow]⚠️  Erreur téléchargement {filename}: {e}[/yellow]")
        return None


def download_data_gouv_csv_resource(query: str, filename: str) -> Optional[Path]:
    """
    Cherche sur data.gouv.fr un jeu de données correspondant à query et télécharge
    la première ressource CSV/ZIP exploitable.

    Utilisé comme fallback pour les sources dont l'URL exacte change côté producteur.
    """
    cache_path = CACHE_DIR / filename
    if cache_path.exists():
        console.print(f"[dim]Cache : {filename} ({cache_path.stat().st_size // 1024} KB)[/dim]")
        return cache_path

    try:
        api_url = "https://www.data.gouv.fr/api/1/datasets/"
        resp = requests.get(
            api_url,
            params={"q": query, "page_size": 10},
            timeout=HTTP_TIMEOUT,
            headers={"User-Agent": "CityMatch/1.0"},
        )
        if resp.status_code != 200:
            console.print(f"[yellow]⚠️  data.gouv HTTP {resp.status_code} pour {query}[/yellow]")
            return None

        datasets = resp.json().get("data", [])
        for dataset in datasets:
            for res in dataset.get("resources", []):
                url = res.get("url") or ""
                title = (res.get("title") or res.get("description") or "").lower()
                fmt = (res.get("format") or "").lower()

                is_usable = (
                    url.lower().endswith((".csv", ".csv.gz", ".zip"))
                    or fmt in {"csv", "zip", "gzip", "gz"}
                    or "csv" in title
                )
                if not is_usable:
                    continue

                console.print(f"[blue]⬇️  Téléchargement data.gouv : {filename}...[/blue]")
                with requests.get(url, timeout=HTTP_TIMEOUT, stream=True, headers={"User-Agent": "CityMatch/1.0"}) as r:
                    if r.status_code != 200:
                        continue
                    with open(cache_path, "wb") as f:
                        for chunk in r.iter_content(8192):
                            if chunk:
                                f.write(chunk)

                if cache_path.exists() and cache_path.stat().st_size > 0:
                    console.print(f"[green]✅ {filename} ({cache_path.stat().st_size / 1024 / 1024:.1f} MB)[/green]")
                    return cache_path

        console.print(f"[yellow]⚠️  Aucune ressource CSV trouvée sur data.gouv pour : {query}[/yellow]")
        return None

    except Exception as exc:
        console.print(f"[yellow]⚠️  Recherche data.gouv impossible pour {query}: {exc}[/yellow]")
        return None


def read_csv_flexible(path: Path, **kwargs) -> pd.DataFrame:
    seps = kwargs.pop("seps", [";", ",", "\t"])
    encs = kwargs.pop("encs", ["utf-8", "utf-8-sig", "latin-1", "cp1252"])
    for enc in encs:
        for sep in seps:
            try:
                df = pd.read_csv(path, sep=sep, encoding=enc, dtype=str, low_memory=False, on_bad_lines="skip", **kwargs)
                if len(df.columns) > 2:
                    return df
            except Exception:
                continue
    return pd.DataFrame()


def to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        s = str(v).replace("\u202f", "").replace(" ", "").replace(",", ".")
        if s in ("", "nan", "None"):
            return None
        return float(s)
    except Exception:
        return None
