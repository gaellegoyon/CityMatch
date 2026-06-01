"""
data/build_communes_index.py
─────────────────────────────
Script de bootstrap — à exécuter UNE SEULE FOIS (ou annuellement).

Interroge l'API Découpage Administratif (geo.api.gouv.fr) pour récupérer
uniquement les communes de France métropolitaine avec population >= seuil,
leurs coordonnées GPS officielles et leur région.

Produit : data/cache/communes_index.json

Usage :
    python data/build_communes_index.py                  # seuil 10 000 hab (≈900 communes)
    python data/build_communes_index.py --seuil 20000    # ≈350 communes
    python data/build_communes_index.py --seuil 5000     # ≈1800 communes
    python data/build_communes_index.py --force          # force régénération
"""

import json
import argparse
from pathlib import Path

import requests

# ── Mapping département → région métropolitaine (source : INSEE COG 2025) ─────
# Inclut uniquement :
# - les départements métropolitains 01 à 95
# - la Corse 2A / 2B
#
# Exclut volontairement :
# - DOM : 971, 972, 973, 974, 976
# - COM / TOM / autres collectivités : 975, 977, 978, 986, 987, 988, etc.
DEP_TO_REGION = {
    "01": "Auvergne-Rhône-Alpes",      "03": "Auvergne-Rhône-Alpes",
    "07": "Auvergne-Rhône-Alpes",      "15": "Auvergne-Rhône-Alpes",
    "26": "Auvergne-Rhône-Alpes",      "38": "Auvergne-Rhône-Alpes",
    "42": "Auvergne-Rhône-Alpes",      "43": "Auvergne-Rhône-Alpes",
    "63": "Auvergne-Rhône-Alpes",      "69": "Auvergne-Rhône-Alpes",
    "73": "Auvergne-Rhône-Alpes",      "74": "Auvergne-Rhône-Alpes",

    "21": "Bourgogne-Franche-Comté",   "25": "Bourgogne-Franche-Comté",
    "39": "Bourgogne-Franche-Comté",   "58": "Bourgogne-Franche-Comté",
    "70": "Bourgogne-Franche-Comté",   "71": "Bourgogne-Franche-Comté",
    "89": "Bourgogne-Franche-Comté",   "90": "Bourgogne-Franche-Comté",

    "22": "Bretagne",  "29": "Bretagne",  "35": "Bretagne",  "56": "Bretagne",

    "18": "Centre-Val de Loire",  "28": "Centre-Val de Loire",
    "36": "Centre-Val de Loire",  "37": "Centre-Val de Loire",
    "41": "Centre-Val de Loire",  "45": "Centre-Val de Loire",

    "2A": "Corse",  "2B": "Corse",

    "08": "Grand Est",  "10": "Grand Est",  "51": "Grand Est",
    "52": "Grand Est",  "54": "Grand Est",  "55": "Grand Est",
    "57": "Grand Est",  "67": "Grand Est",  "68": "Grand Est",  "88": "Grand Est",

    "02": "Hauts-de-France",  "59": "Hauts-de-France",  "60": "Hauts-de-France",
    "62": "Hauts-de-France",  "80": "Hauts-de-France",

    "75": "Île-de-France",  "77": "Île-de-France",  "78": "Île-de-France",
    "91": "Île-de-France",  "92": "Île-de-France",  "93": "Île-de-France",
    "94": "Île-de-France",  "95": "Île-de-France",

    "14": "Normandie",  "27": "Normandie",  "50": "Normandie",
    "61": "Normandie",  "76": "Normandie",

    "44": "Pays de la Loire",  "49": "Pays de la Loire",
    "53": "Pays de la Loire",  "72": "Pays de la Loire",  "85": "Pays de la Loire",

    "16": "Nouvelle-Aquitaine",  "17": "Nouvelle-Aquitaine",
    "19": "Nouvelle-Aquitaine",  "23": "Nouvelle-Aquitaine",
    "24": "Nouvelle-Aquitaine",  "33": "Nouvelle-Aquitaine",
    "40": "Nouvelle-Aquitaine",  "47": "Nouvelle-Aquitaine",
    "64": "Nouvelle-Aquitaine",  "79": "Nouvelle-Aquitaine",
    "86": "Nouvelle-Aquitaine",  "87": "Nouvelle-Aquitaine",

    "09": "Occitanie",  "11": "Occitanie",  "12": "Occitanie",
    "30": "Occitanie",  "31": "Occitanie",  "32": "Occitanie",
    "34": "Occitanie",  "46": "Occitanie",  "48": "Occitanie",
    "65": "Occitanie",  "66": "Occitanie",  "81": "Occitanie",  "82": "Occitanie",

    "04": "Provence-Alpes-Côte d'Azur",  "05": "Provence-Alpes-Côte d'Azur",
    "06": "Provence-Alpes-Côte d'Azur",  "13": "Provence-Alpes-Côte d'Azur",
    "83": "Provence-Alpes-Côte d'Azur",  "84": "Provence-Alpes-Côte d'Azur",
}

# Set utilisé pour filtrer explicitement la France métropolitaine.
DEPARTEMENTS_METRO = set(DEP_TO_REGION.keys())


def fetch_communes(seuil_pop: int = 10000) -> list[dict]:
    """
    Récupère les communes françaises métropolitaines avec population >= seuil
    via l'API Découpage Administratif geo.api.gouv.fr.

    Endpoint : GET /communes?fields=nom,code,codeDepartement,population,centre

    Important :
    L'API renvoie aussi les DOM-TOM / collectivités d'outre-mer.
    On filtre donc explicitement sur DEPARTEMENTS_METRO.
    """
    url = "https://geo.api.gouv.fr/communes"
    params = {
        "fields": "nom,code,codeDepartement,population,centre",
        "format": "json",
        "type": "commune-actuelle",  # exclut communes déléguées/associées
    }

    print("📡 Requête geo.api.gouv.fr — toutes communes françaises...")
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    all_communes = resp.json()
    print(f"   → {len(all_communes):,} communes reçues")

    result = []
    skipped_no_coords = 0
    skipped_pop = 0
    skipped_outre_mer = 0

    for c in all_communes:
        code = c.get("code", "")
        dep = c.get("codeDepartement") or code[:2]

        # Exclure DOM-TOM / collectivités hors France métropolitaine.
        # Exemples exclus : 971, 972, 973, 974, 976, 987, 988...
        if dep not in DEPARTEMENTS_METRO:
            skipped_outre_mer += 1
            continue

        pop = c.get("population") or 0
        if pop < seuil_pop:
            skipped_pop += 1
            continue

        centre = c.get("centre", {})
        coords = centre.get("coordinates", [])
        if len(coords) < 2:
            skipped_no_coords += 1
            continue

        lon, lat = float(coords[0]), float(coords[1])
        nom = c.get("nom", "")
        region = DEP_TO_REGION[dep]

        result.append({
            "code_insee": code,
            "nom": nom,
            "departement": dep,
            "region": region,
            "latitude": lat,
            "longitude": lon,
            "population": pop,
        })

    # Trier par population décroissante
    result.sort(key=lambda x: x["population"], reverse=True)

    print(f"   → {len(result):,} communes métropolitaines retenues (pop ≥ {seuil_pop:,})")
    print(
        f"   → {skipped_pop:,} ignorées (pop < seuil) | "
        f"{skipped_no_coords} sans coords | "
        f"{skipped_outre_mer:,} hors métropole"
    )

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--seuil",
        type=int,
        default=10000,
        help="Population minimale (défaut: 10 000)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Forcer la régénération même si le fichier existe",
    )
    args = parser.parse_args()

    # Chemin de sortie
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from config.settings import DATA_DIR

    output_path = DATA_DIR / "cache" / "communes_index.json"

    if output_path.exists() and not args.force:
        with open(output_path, encoding="utf-8") as f:
            existing = json.load(f)

        print(
            f"✅ Index existant : {len(existing['communes']):,} communes "
            f"(seuil={existing['seuil_pop']}, généré le {existing['generated_at'][:10]})"
        )
        print("   Utilisez --force pour régénérer.")
        return

    communes = fetch_communes(seuil_pop=args.seuil)

    output = {
        "generated_at": __import__("datetime").datetime.now().isoformat(),
        "seuil_pop": args.seuil,
        "source": "geo.api.gouv.fr — API Découpage Administratif",
        "scope": "France métropolitaine uniquement",
        "nb_communes": len(communes),
        "communes": communes,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Index sauvegardé : {output_path}")
    print(f"   {len(communes):,} communes métropolitaines, seuil={args.seuil:,} hab.")

    print("\nRépartition par région :")
    from collections import Counter

    regions = Counter(c["region"] for c in communes)
    for region, nb in sorted(regions.items(), key=lambda x: -x[1]):
        print(f"   {nb:3d}  {region}")


if __name__ == "__main__":
    main()
