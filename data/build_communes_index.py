"""
data/build_communes_index.py
────────────────────────────
Script de bootstrap — à exécuter une seule fois ou annuellement.

Interroge l'API Découpage Administratif geo.api.gouv.fr pour récupérer
uniquement les communes de France métropolitaine avec population >= seuil,
leurs coordonnées GPS officielles et leur région.

Produit :
    data/cache/communes_index.json

Usage :
    python data/build_communes_index.py
    python data/build_communes_index.py --seuil 20000
    python data/build_communes_index.py --seuil 5000
    python data/build_communes_index.py --force
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

import requests


# Permet d'exécuter le script directement :
#     python data/build_communes_index.py
PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import DATA_DIR  # noqa: E402
from data.ingest.config import HTTP_TIMEOUT  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Mapping département → région métropolitaine
# ─────────────────────────────────────────────────────────────────────────────
# Inclut uniquement :
# - les départements métropolitains 01 à 95 ;
# - la Corse 2A / 2B.
#
# Exclut volontairement :
# - DOM : 971, 972, 973, 974, 976 ;
# - COM / TOM / autres collectivités : 975, 977, 978, 986, 987, 988, etc.
DEP_TO_REGION: Final[dict[str, str]] = {
    "01": "Auvergne-Rhône-Alpes",
    "03": "Auvergne-Rhône-Alpes",
    "07": "Auvergne-Rhône-Alpes",
    "15": "Auvergne-Rhône-Alpes",
    "26": "Auvergne-Rhône-Alpes",
    "38": "Auvergne-Rhône-Alpes",
    "42": "Auvergne-Rhône-Alpes",
    "43": "Auvergne-Rhône-Alpes",
    "63": "Auvergne-Rhône-Alpes",
    "69": "Auvergne-Rhône-Alpes",
    "73": "Auvergne-Rhône-Alpes",
    "74": "Auvergne-Rhône-Alpes",

    "21": "Bourgogne-Franche-Comté",
    "25": "Bourgogne-Franche-Comté",
    "39": "Bourgogne-Franche-Comté",
    "58": "Bourgogne-Franche-Comté",
    "70": "Bourgogne-Franche-Comté",
    "71": "Bourgogne-Franche-Comté",
    "89": "Bourgogne-Franche-Comté",
    "90": "Bourgogne-Franche-Comté",

    "22": "Bretagne",
    "29": "Bretagne",
    "35": "Bretagne",
    "56": "Bretagne",

    "18": "Centre-Val de Loire",
    "28": "Centre-Val de Loire",
    "36": "Centre-Val de Loire",
    "37": "Centre-Val de Loire",
    "41": "Centre-Val de Loire",
    "45": "Centre-Val de Loire",

    "2A": "Corse",
    "2B": "Corse",

    "08": "Grand Est",
    "10": "Grand Est",
    "51": "Grand Est",
    "52": "Grand Est",
    "54": "Grand Est",
    "55": "Grand Est",
    "57": "Grand Est",
    "67": "Grand Est",
    "68": "Grand Est",
    "88": "Grand Est",

    "02": "Hauts-de-France",
    "59": "Hauts-de-France",
    "60": "Hauts-de-France",
    "62": "Hauts-de-France",
    "80": "Hauts-de-France",

    "75": "Île-de-France",
    "77": "Île-de-France",
    "78": "Île-de-France",
    "91": "Île-de-France",
    "92": "Île-de-France",
    "93": "Île-de-France",
    "94": "Île-de-France",
    "95": "Île-de-France",

    "14": "Normandie",
    "27": "Normandie",
    "50": "Normandie",
    "61": "Normandie",
    "76": "Normandie",

    "44": "Pays de la Loire",
    "49": "Pays de la Loire",
    "53": "Pays de la Loire",
    "72": "Pays de la Loire",
    "85": "Pays de la Loire",

    "16": "Nouvelle-Aquitaine",
    "17": "Nouvelle-Aquitaine",
    "19": "Nouvelle-Aquitaine",
    "23": "Nouvelle-Aquitaine",
    "24": "Nouvelle-Aquitaine",
    "33": "Nouvelle-Aquitaine",
    "40": "Nouvelle-Aquitaine",
    "47": "Nouvelle-Aquitaine",
    "64": "Nouvelle-Aquitaine",
    "79": "Nouvelle-Aquitaine",
    "86": "Nouvelle-Aquitaine",
    "87": "Nouvelle-Aquitaine",

    "09": "Occitanie",
    "11": "Occitanie",
    "12": "Occitanie",
    "30": "Occitanie",
    "31": "Occitanie",
    "32": "Occitanie",
    "34": "Occitanie",
    "46": "Occitanie",
    "48": "Occitanie",
    "65": "Occitanie",
    "66": "Occitanie",
    "81": "Occitanie",
    "82": "Occitanie",

    "04": "Provence-Alpes-Côte d'Azur",
    "05": "Provence-Alpes-Côte d'Azur",
    "06": "Provence-Alpes-Côte d'Azur",
    "13": "Provence-Alpes-Côte d'Azur",
    "83": "Provence-Alpes-Côte d'Azur",
    "84": "Provence-Alpes-Côte d'Azur",
}

DEPARTEMENTS_METRO: Final[frozenset[str]] = frozenset(DEP_TO_REGION)
DEFAULT_POPULATION_THRESHOLD: Final[int] = 10_000
OUTPUT_FILENAME: Final[str] = "communes_index.json"


def _positive_int(value: str) -> int:
    """Valide un entier strictement positif."""
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Valeur entière invalide : {value!r}"
        ) from exc

    if number < 1:
        raise argparse.ArgumentTypeError(
            "Le seuil de population doit être supérieur ou égal à 1."
        )

    return number


def build_parser() -> argparse.ArgumentParser:
    """Construit le parser CLI."""
    parser = argparse.ArgumentParser(
        description=(
            "Construit l'index des communes métropolitaines CityMatch "
            "à partir de geo.api.gouv.fr."
        )
    )

    parser.add_argument(
        "--seuil",
        type=_positive_int,
        default=DEFAULT_POPULATION_THRESHOLD,
        help=f"Population minimale. Défaut : {DEFAULT_POPULATION_THRESHOLD:,} habitants.",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Force la régénération même si le fichier existe déjà.",
    )

    return parser


def _get_department_code(commune: dict[str, Any]) -> str:
    """Récupère le code département depuis la réponse API."""
    code = str(commune.get("code", "") or "")
    department = str(commune.get("codeDepartement", "") or "")

    if department:
        return department

    if code.startswith(("2A", "2B")):
        return code[:2]

    return code[:2]


def _extract_coordinates(commune: dict[str, Any]) -> tuple[float, float] | None:
    """
    Extrait les coordonnées GPS d'une commune.

    L'API renvoie généralement :
        centre.coordinates = [longitude, latitude]
    """
    centre = commune.get("centre") or {}

    if not isinstance(centre, dict):
        return None

    coords = centre.get("coordinates") or []

    if not isinstance(coords, list) or len(coords) < 2:
        return None

    try:
        longitude = float(coords[0])
        latitude = float(coords[1])
    except (TypeError, ValueError):
        return None

    return latitude, longitude


def fetch_communes(seuil_pop: int = DEFAULT_POPULATION_THRESHOLD) -> list[dict[str, Any]]:
    """
    Récupère les communes françaises métropolitaines avec population >= seuil
    via l'API Découpage Administratif geo.api.gouv.fr.

    Endpoint :
        GET /communes?fields=nom,code,codeDepartement,population,centre

    L'API renvoie aussi les DOM-TOM et collectivités d'outre-mer.
    Le script filtre donc explicitement sur DEPARTEMENTS_METRO.
    """
    url = "https://geo.api.gouv.fr/communes"
    params = {
        "fields": "nom,code,codeDepartement,population,centre",
        "format": "json",
        "type": "commune-actuelle",
    }

    print("📡 Requête geo.api.gouv.fr — communes françaises...")

    response = requests.get(
        url,
        params=params,
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()

    all_communes = response.json()

    if not isinstance(all_communes, list):
        raise ValueError("Réponse API inattendue : la liste des communes est absente.")

    print(f"   → {len(all_communes):,} communes reçues")

    result: list[dict[str, Any]] = []
    skipped_no_coords = 0
    skipped_population = 0
    skipped_outre_mer = 0
    skipped_invalid_population = 0

    for commune in all_communes:
        if not isinstance(commune, dict):
            continue

        code = str(commune.get("code", "") or "")
        department = _get_department_code(commune)

        if department not in DEPARTEMENTS_METRO:
            skipped_outre_mer += 1
            continue

        try:
            population = int(commune.get("population") or 0)
        except (TypeError, ValueError):
            skipped_invalid_population += 1
            continue

        if population < seuil_pop:
            skipped_population += 1
            continue

        coordinates = _extract_coordinates(commune)

        if coordinates is None:
            skipped_no_coords += 1
            continue

        latitude, longitude = coordinates
        name = str(commune.get("nom", "") or "").strip()

        if not code or not name:
            continue

        result.append(
            {
                "code_insee": code,
                "nom": name,
                "departement": department,
                "region": DEP_TO_REGION[department],
                "latitude": latitude,
                "longitude": longitude,
                "population": population,
            }
        )

    result.sort(key=lambda city: city["population"], reverse=True)

    print(f"   → {len(result):,} communes métropolitaines retenues (pop ≥ {seuil_pop:,})")
    print(
        f"   → {skipped_population:,} ignorées (pop < seuil) | "
        f"{skipped_no_coords:,} sans coords | "
        f"{skipped_invalid_population:,} population invalide | "
        f"{skipped_outre_mer:,} hors métropole"
    )

    return result


def _load_existing_index(output_path: Path) -> dict[str, Any] | None:
    """Charge un index existant si possible."""
    try:
        with output_path.open(encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None

    return data if isinstance(data, dict) else None


def _build_output_payload(
    communes: list[dict[str, Any]],
    seuil_pop: int,
) -> dict[str, Any]:
    """Construit le JSON final sauvegardé dans le cache."""
    generated_at = datetime.now(timezone.utc).isoformat()

    return {
        "generated_at": generated_at,
        "seuil_pop": seuil_pop,
        "source": "geo.api.gouv.fr — API Découpage Administratif",
        "scope": "France métropolitaine uniquement",
        "nb_communes": len(communes),
        "communes": communes,
    }


def _print_existing_index_message(existing: dict[str, Any]) -> None:
    """Affiche les informations d'un index déjà présent."""
    communes = existing.get("communes") or []
    generated_at = str(existing.get("generated_at", "date inconnue"))
    seuil_pop = existing.get("seuil_pop", "?")

    print(
        f"✅ Index existant : {len(communes):,} communes "
        f"(seuil={seuil_pop}, généré le {generated_at[:10]})"
    )
    print("   Utilisez --force pour régénérer.")


def _print_region_summary(communes: list[dict[str, Any]]) -> None:
    """Affiche la répartition des communes retenues par région."""
    print("\nRépartition par région :")

    regions = Counter(city["region"] for city in communes)

    for region, count in sorted(regions.items(), key=lambda item: (-item[1], item[0])):
        print(f"   {count:3d}  {region}")


def main(argv: list[str] | None = None) -> None:
    """Point d'entrée CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    output_path = DATA_DIR / "cache" / OUTPUT_FILENAME

    if output_path.exists() and not args.force:
        existing = _load_existing_index(output_path)

        if existing is not None:
            _print_existing_index_message(existing)
            return

        print("⚠️  Index existant illisible, régénération...")

    communes = fetch_communes(seuil_pop=args.seuil)
    output = _build_output_payload(
        communes=communes,
        seuil_pop=args.seuil,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(output, file, ensure_ascii=False, indent=2)

    print(f"\n✅ Index sauvegardé : {output_path}")
    print(f"   {len(communes):,} communes métropolitaines, seuil={args.seuil:,} hab.")

    _print_region_summary(communes)


if __name__ == "__main__":
    main()