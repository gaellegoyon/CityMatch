"""
data/ingest/index.py

Chargement de l'index des communes à traiter.
"""

from __future__ import annotations

import json

from data.ingest.config import CACHE_DIR
from data.ingest.utils import console


def load_communes_index(seuil_pop: int = 10000) -> list:
    """
    Charge l'index des communes depuis communes_index.json (généré par build_communes_index.py).
    Si absent, utilise la liste de secours codée en dur (57 villes).
    """
    index_path = CACHE_DIR / "communes_index.json"
    if index_path.exists():
        with open(index_path, encoding="utf-8") as f:
            data = json.load(f)
        communes = [
            (c["code_insee"], c["nom"], c["departement"],
             c["region"], c["latitude"], c["longitude"])
            for c in data["communes"]
        ]
        console.print(
            f"[green]✅ Index communes : {len(communes):,} villes "
            f"(pop ≥ {data['seuil_pop']:,}, "
            f"généré le {data['generated_at'][:10]})[/green]"
        )
        return communes
    else:
        console.print(
            "[yellow]⚠️  communes_index.json absent — "
            "liste de secours utilisée (57 villes).[/yellow]"
        )
        console.print(
            "[dim]  → Exécutez : python data/build_communes_index.py[/dim]"
        )
        return COMMUNES_FALLBACK


# Liste de secours — utilisée uniquement si build_communes_index.py n'a pas été exécuté
COMMUNES_FALLBACK = [
    ("01053","Bourg-en-Bresse","01","Auvergne-Rhône-Alpes",46.205,5.228),
    ("06088","Nice","Alpes-Maritimes","Provence-Alpes-Côte d'Azur",43.710,7.262),
    ("13055","Marseille","Bouches-du-Rhône","Provence-Alpes-Côte d'Azur",43.296,5.381),
    ("14118","Caen","Calvados","Normandie",49.183,-0.370),
    ("17300","Rochefort","Charente-Maritime","Nouvelle-Aquitaine",45.942,-0.958),
    ("17415","Saintes","Charente-Maritime","Nouvelle-Aquitaine",45.745,-0.632),
    ("21231","Dijon","Côte-d'Or","Bourgogne-Franche-Comté",47.322,5.041),
    ("22278","Saint-Brieuc","Côtes-d'Armor","Bretagne",48.514,-2.765),
    ("25056","Besançon","Doubs","Bourgogne-Franche-Comté",47.237,6.024),
    ("26362","Valence","Drôme","Auvergne-Rhône-Alpes",44.934,4.892),
    ("29019","Brest","Finistère","Bretagne",48.390,-4.486),
    ("29232","Quimper","Finistère","Bretagne",47.997,-4.097),
    ("30189","Nîmes","Gard","Occitanie",43.836,4.360),
    ("31555","Toulouse","Haute-Garonne","Occitanie",43.604,1.444),
    ("33063","Bordeaux","Gironde","Nouvelle-Aquitaine",44.841,-0.580),
    ("33318","Mérignac","Gironde","Nouvelle-Aquitaine",44.836,-0.645),
    ("34172","Montpellier","Hérault","Occitanie",43.610,3.877),
    ("35047","Saint-Malo","Ille-et-Vilaine","Bretagne",48.649,-2.025),
    ("35238","Rennes","Ille-et-Vilaine","Bretagne",48.117,-1.677),
    ("37261","Tours","Indre-et-Loire","Centre-Val de Loire",47.394,0.684),
    ("38185","Grenoble","Isère","Auvergne-Rhône-Alpes",45.188,5.724),
    ("40192","Mont-de-Marsan","Landes","Nouvelle-Aquitaine",43.890,-0.497),
    ("42218","Saint-Étienne","Loire","Auvergne-Rhône-Alpes",45.439,4.387),
    ("44109","Nantes","Loire-Atlantique","Pays de la Loire",47.218,-1.554),
    ("44180","Saint-Nazaire","Loire-Atlantique","Pays de la Loire",47.273,-2.213),
    ("45234","Orléans","Loiret","Centre-Val de Loire",47.902,1.909),
    ("49007","Angers","Maine-et-Loire","Pays de la Loire",47.474,-0.554),
    ("50129","Cherbourg-en-Cotentin","Manche","Normandie",49.633,-1.616),
    ("51454","Reims","Marne","Grand Est",49.258,4.032),
    ("54395","Nancy","Meurthe-et-Moselle","Grand Est",48.693,6.184),
    ("56260","Vannes","Morbihan","Bretagne",47.658,-2.760),
    ("57463","Metz","Moselle","Grand Est",49.120,6.175),
    ("59350","Lille","Nord","Hauts-de-France",50.629,3.057),
    ("59512","Roubaix","Nord","Hauts-de-France",50.690,3.181),
    ("60057","Beauvais","Oise","Hauts-de-France",49.431,2.081),
    ("62041","Arras","Pas-de-Calais","Hauts-de-France",50.291,2.778),
    ("63113","Clermont-Ferrand","Puy-de-Dôme","Auvergne-Rhône-Alpes",45.777,3.087),
    ("64024","Anglet","Pyrénées-Atlantiques","Nouvelle-Aquitaine",43.486,-1.517),
    ("64102","Bayonne","Pyrénées-Atlantiques","Nouvelle-Aquitaine",43.493,-1.474),
    ("64445","Pau","Pyrénées-Atlantiques","Nouvelle-Aquitaine",43.299,-0.370),
    ("67482","Strasbourg","Bas-Rhin","Grand Est",48.573,7.752),
    ("68066","Colmar","Haut-Rhin","Grand Est",48.079,7.358),
    ("69123","Lyon","Rhône","Auvergne-Rhône-Alpes",45.748,4.847),
    ("69149","Villeurbanne","Rhône","Auvergne-Rhône-Alpes",45.767,4.880),
    ("74010","Annecy","Haute-Savoie","Auvergne-Rhône-Alpes",45.900,6.117),
    ("74278","Thonon-les-Bains","Haute-Savoie","Auvergne-Rhône-Alpes",46.370,6.478),
    ("76095","Le Havre","Seine-Maritime","Normandie",49.494,0.107),
    ("76540","Rouen","Seine-Maritime","Normandie",49.443,1.099),
    ("80021","Amiens","Somme","Hauts-de-France",49.894,2.302),
    ("83137","Toulon","Var","Provence-Alpes-Côte d'Azur",43.125,5.930),
    ("84007","Avignon","Vaucluse","Provence-Alpes-Côte d'Azur",43.950,4.806),
    ("85047","La Roche-sur-Yon","Vendée","Pays de la Loire",46.670,-1.426),
    ("86194","Poitiers","Vienne","Nouvelle-Aquitaine",46.580,0.340),
    ("87085","Limoges","Haute-Vienne","Nouvelle-Aquitaine",45.833,1.262),
    ("88160","Épinal","Vosges","Grand Est",48.174,6.449),
    ("13200","Arles","Bouches-du-Rhône","Provence-Alpes-Côte d'Azur",43.677,4.627),
    ("38235","Meylan","Isère","Auvergne-Rhône-Alpes",45.213,5.778),
    ("62498","Lens","Pas-de-Calais","Hauts-de-France",50.432,2.833),
]

# Dédoublonnage de la liste de secours
_seen = set()
COMMUNES_FALLBACK_UNIQUES = []
for c in COMMUNES_FALLBACK:
    if c[0] not in _seen:
        _seen.add(c[0])
        COMMUNES_FALLBACK_UNIQUES.append(c)
COMMUNES_FALLBACK = COMMUNES_FALLBACK_UNIQUES
