"""
data/ingest/index.py
────────────────────
Chargement de l'index des communes à traiter.

L'index principal est généré par :
    python data/build_communes_index.py

Fichier attendu :
    data/cache/communes_index.json

Si l'index est absent ou illisible, une liste de secours limitée est utilisée.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from data.ingest.config import CACHE_DIR
from data.ingest.utils import console


CommuneTuple = tuple[str, str, str, str, float, float]

DEFAULT_MIN_POPULATION: Final[int] = 10_000
INDEX_FILENAME: Final[str] = "communes_index.json"


def _index_path() -> Path:
    """Retourne le chemin du fichier d'index des communes."""
    return CACHE_DIR / INDEX_FILENAME


def _as_int(value: Any, default: int = 0) -> int:
    """Convertit une valeur en entier robuste."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any) -> float | None:
    """Convertit une valeur en float si possible."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    return number


def _parse_commune(raw_commune: dict[str, Any]) -> tuple[CommuneTuple, int] | None:
    """Parse et valide une commune issue du JSON cache."""
    try:
        code_insee = str(raw_commune["code_insee"])
        name = str(raw_commune["nom"])
        department = str(raw_commune["departement"])
        region = str(raw_commune["region"])
    except KeyError:
        return None

    latitude = _as_float(raw_commune.get("latitude"))
    longitude = _as_float(raw_commune.get("longitude"))
    population = _as_int(raw_commune.get("population"), default=0)

    if not code_insee or not name or not department or not region:
        return None

    if latitude is None or longitude is None:
        return None

    commune: CommuneTuple = (
        code_insee,
        name,
        department,
        region,
        latitude,
        longitude,
    )

    return commune, population


def _load_json_index(index_path: Path) -> dict[str, Any] | None:
    """Charge le fichier JSON d'index."""
    try:
        with index_path.open(encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        console.print(f"[yellow]⚠️  Index communes illisible : {index_path}[/yellow]")
        return None
    except OSError as exc:
        console.print(f"[yellow]⚠️  Impossible de lire l'index communes : {exc}[/yellow]")
        return None

    return data if isinstance(data, dict) else None


def _load_communes_from_json(
    data: dict[str, Any],
    seuil_pop: int,
) -> list[CommuneTuple]:
    """Convertit le JSON d'index en liste de communes."""
    raw_communes = data.get("communes", [])

    if not isinstance(raw_communes, list):
        return []

    communes: list[CommuneTuple] = []
    invalid_count = 0
    filtered_count = 0

    for raw_commune in raw_communes:
        if not isinstance(raw_commune, dict):
            invalid_count += 1
            continue

        parsed = _parse_commune(raw_commune)

        if parsed is None:
            invalid_count += 1
            continue

        commune, population = parsed

        if population < seuil_pop:
            filtered_count += 1
            continue

        communes.append(commune)

    if invalid_count:
        console.print(f"[yellow]⚠️  {invalid_count} communes invalides ignorées dans l'index[/yellow]")

    if filtered_count:
        console.print(f"[dim]  → {filtered_count:,} communes ignorées car population < {seuil_pop:,}[/dim]")

    return communes


def _deduplicate_communes(communes: list[CommuneTuple]) -> list[CommuneTuple]:
    """Dédoublonne les communes par code INSEE."""
    seen: set[str] = set()
    unique_communes: list[CommuneTuple] = []

    for commune in communes:
        code_insee = commune[0]

        if code_insee in seen:
            continue

        seen.add(code_insee)
        unique_communes.append(commune)

    return unique_communes


def _load_fallback_communes(seuil_pop: int) -> list[CommuneTuple]:
    """
    Retourne la liste de secours.

    La population n'est pas connue dans le fallback, donc seuil_pop ne peut pas
    être appliqué précisément. Cette liste est uniquement un mode dégradé.
    """
    communes = _deduplicate_communes(list(COMMUNES_FALLBACK))

    console.print(
        "[yellow]⚠️  communes_index.json absent ou inutilisable — "
        f"liste de secours utilisée ({len(communes)} villes).[/yellow]"
    )
    console.print(
        "[dim]  → Exécutez : python data/build_communes_index.py --force[/dim]"
    )

    if seuil_pop != DEFAULT_MIN_POPULATION:
        console.print(
            "[yellow]⚠️  Le seuil de population ne peut pas être appliqué "
            "sur la liste de secours, car elle ne contient pas les populations.[/yellow]"
        )

    return communes


def load_communes_index(seuil_pop: int = DEFAULT_MIN_POPULATION) -> list[CommuneTuple]:
    """
    Charge l'index des communes depuis communes_index.json.

    Si absent ou illisible, utilise une liste de secours limitée.
    """
    seuil_pop = max(1, _as_int(seuil_pop, default=DEFAULT_MIN_POPULATION))
    index_path = _index_path()
    data = _load_json_index(index_path)

    if data is None:
        return _load_fallback_communes(seuil_pop=seuil_pop)

    communes = _load_communes_from_json(
        data=data,
        seuil_pop=seuil_pop,
    )
    communes = _deduplicate_communes(communes)

    if not communes:
        console.print("[yellow]⚠️  Aucune commune exploitable dans l'index JSON.[/yellow]")
        return _load_fallback_communes(seuil_pop=seuil_pop)

    generated_at = str(data.get("generated_at", "date inconnue"))
    index_threshold = data.get("seuil_pop", "?")

    console.print(
        f"[green]✅ Index communes : {len(communes):,} villes "
        f"(seuil demandé ≥ {seuil_pop:,}, "
        f"index généré avec seuil={index_threshold}, "
        f"généré le {generated_at[:10]})[/green]"
    )

    return communes


# Liste de secours — utilisée uniquement si build_communes_index.py n'a pas été exécuté.
COMMUNES_FALLBACK: Final[tuple[CommuneTuple, ...]] = (
    ("01053", "Bourg-en-Bresse", "01", "Auvergne-Rhône-Alpes", 46.205, 5.228),
    ("06088", "Nice", "06", "Provence-Alpes-Côte d'Azur", 43.710, 7.262),
    ("13055", "Marseille", "13", "Provence-Alpes-Côte d'Azur", 43.296, 5.381),
    ("14118", "Caen", "14", "Normandie", 49.183, -0.370),
    ("17300", "Rochefort", "17", "Nouvelle-Aquitaine", 45.942, -0.958),
    ("17415", "Saintes", "17", "Nouvelle-Aquitaine", 45.745, -0.632),
    ("21231", "Dijon", "21", "Bourgogne-Franche-Comté", 47.322, 5.041),
    ("22278", "Saint-Brieuc", "22", "Bretagne", 48.514, -2.765),
    ("25056", "Besançon", "25", "Bourgogne-Franche-Comté", 47.237, 6.024),
    ("26362", "Valence", "26", "Auvergne-Rhône-Alpes", 44.934, 4.892),
    ("29019", "Brest", "29", "Bretagne", 48.390, -4.486),
    ("29232", "Quimper", "29", "Bretagne", 47.997, -4.097),
    ("30189", "Nîmes", "30", "Occitanie", 43.836, 4.360),
    ("31555", "Toulouse", "31", "Occitanie", 43.604, 1.444),
    ("33063", "Bordeaux", "33", "Nouvelle-Aquitaine", 44.841, -0.580),
    ("33318", "Mérignac", "33", "Nouvelle-Aquitaine", 44.836, -0.645),
    ("34172", "Montpellier", "34", "Occitanie", 43.610, 3.877),
    ("35047", "Saint-Malo", "35", "Bretagne", 48.649, -2.025),
    ("35238", "Rennes", "35", "Bretagne", 48.117, -1.677),
    ("37261", "Tours", "37", "Centre-Val de Loire", 47.394, 0.684),
    ("38185", "Grenoble", "38", "Auvergne-Rhône-Alpes", 45.188, 5.724),
    ("40192", "Mont-de-Marsan", "40", "Nouvelle-Aquitaine", 43.890, -0.497),
    ("42218", "Saint-Étienne", "42", "Auvergne-Rhône-Alpes", 45.439, 4.387),
    ("44109", "Nantes", "44", "Pays de la Loire", 47.218, -1.554),
    ("44180", "Saint-Nazaire", "44", "Pays de la Loire", 47.273, -2.213),
    ("45234", "Orléans", "45", "Centre-Val de Loire", 47.902, 1.909),
    ("49007", "Angers", "49", "Pays de la Loire", 47.474, -0.554),
    ("50129", "Cherbourg-en-Cotentin", "50", "Normandie", 49.633, -1.616),
    ("51454", "Reims", "51", "Grand Est", 49.258, 4.032),
    ("54395", "Nancy", "54", "Grand Est", 48.693, 6.184),
    ("56260", "Vannes", "56", "Bretagne", 47.658, -2.760),
    ("57463", "Metz", "57", "Grand Est", 49.120, 6.175),
    ("59350", "Lille", "59", "Hauts-de-France", 50.629, 3.057),
    ("59512", "Roubaix", "59", "Hauts-de-France", 50.690, 3.181),
    ("60057", "Beauvais", "60", "Hauts-de-France", 49.431, 2.081),
    ("62041", "Arras", "62", "Hauts-de-France", 50.291, 2.778),
    ("63113", "Clermont-Ferrand", "63", "Auvergne-Rhône-Alpes", 45.777, 3.087),
    ("64024", "Anglet", "64", "Nouvelle-Aquitaine", 43.486, -1.517),
    ("64102", "Bayonne", "64", "Nouvelle-Aquitaine", 43.493, -1.474),
    ("64445", "Pau", "64", "Nouvelle-Aquitaine", 43.299, -0.370),
    ("67482", "Strasbourg", "67", "Grand Est", 48.573, 7.752),
    ("68066", "Colmar", "68", "Grand Est", 48.079, 7.358),
    ("69123", "Lyon", "69", "Auvergne-Rhône-Alpes", 45.748, 4.847),
    ("69149", "Villeurbanne", "69", "Auvergne-Rhône-Alpes", 45.767, 4.880),
    ("74010", "Annecy", "74", "Auvergne-Rhône-Alpes", 45.900, 6.117),
    ("74278", "Thonon-les-Bains", "74", "Auvergne-Rhône-Alpes", 46.370, 6.478),
    ("76095", "Le Havre", "76", "Normandie", 49.494, 0.107),
    ("76540", "Rouen", "76", "Normandie", 49.443, 1.099),
    ("80021", "Amiens", "80", "Hauts-de-France", 49.894, 2.302),
    ("83137", "Toulon", "83", "Provence-Alpes-Côte d'Azur", 43.125, 5.930),
    ("84007", "Avignon", "84", "Provence-Alpes-Côte d'Azur", 43.950, 4.806),
    ("85047", "La Roche-sur-Yon", "85", "Pays de la Loire", 46.670, -1.426),
    ("86194", "Poitiers", "86", "Nouvelle-Aquitaine", 46.580, 0.340),
    ("87085", "Limoges", "87", "Nouvelle-Aquitaine", 45.833, 1.262),
    ("88160", "Épinal", "88", "Grand Est", 48.174, 6.449),
    ("13200", "Arles", "13", "Provence-Alpes-Côte d'Azur", 43.677, 4.627),
    ("38235", "Meylan", "38", "Auvergne-Rhône-Alpes", 45.213, 5.778),
    ("62498", "Lens", "62", "Hauts-de-France", 50.432, 2.833),
)