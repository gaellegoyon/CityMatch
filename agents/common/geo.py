"""
agents/common/geo.py
────────────────────
Helpers géographiques partagés par les agents.
"""

import math
import re
import unicodedata

REFERENCE_CITY_COORDS = {
    "lyon": (45.748, 4.847), "paris": (48.866, 2.333), "marseille": (43.296, 5.381),
    "bordeaux": (44.841, -0.580), "toulouse": (43.604, 1.444), "nantes": (47.218, -1.554),
    "lille": (50.629, 3.057), "strasbourg": (48.573, 7.752), "rennes": (48.117, -1.677),
    "montpellier": (43.610, 3.877), "nice": (43.710, 7.262), "grenoble": (45.188, 5.724),
    "toulon": (43.125, 5.930), "saint-etienne": (45.439, 4.387), "brest": (48.390, -4.486),
    "le havre": (49.494, 0.107), "reims": (49.258, 4.032), "dijon": (47.322, 5.041),
    "angers": (47.474, -0.554), "tours": (47.394, 0.684), "rouen": (49.443, 1.099),
    "caen": (49.183, -0.370), "orleans": (47.902, 1.909), "orléans": (47.902, 1.909),
    "metz": (49.119, 6.175), "nancy": (48.692, 6.184), "mulhouse": (47.750, 7.335),
    "annecy": (45.899, 6.129), "avignon": (43.949, 4.805), "poitiers": (46.580, 0.340),
    "limoges": (45.833, 1.261), "perpignan": (42.688, 2.894), "bayonne": (43.493, -1.474),
    "la rochelle": (46.160, -1.151), "clermont-ferrand": (45.777, 3.087),
}


def normalize_place_name(name: str) -> str:
    """Normalise un nom de ville pour comparaison robuste."""
    if not name:
        return ""
    s = str(name).strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("'", " ").replace("’", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s.replace("saint ", "saint-")


def resolve_reference_city(name: str) -> tuple[float, float] | None:
    aliases = {
        "saint-etienne": "saint-etienne", "saint-étienne": "saint-etienne",
        "clermont": "clermont-ferrand", "orleans": "orléans",
    }
    key = normalize_place_name(name)
    key = aliases.get(key, key)
    return REFERENCE_CITY_COORDS.get(key)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return radius * 2 * math.asin(math.sqrt(a))
