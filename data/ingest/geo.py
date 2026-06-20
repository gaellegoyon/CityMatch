"""
data/ingest/geo.py
──────────────────
Calculs géographiques simples utilisés par l'ingestion CityMatch.

Ce module fournit :
- une distance haversine en kilomètres ;
- une distance approximative au littoral ;
- une distance approximative aux grands massifs ;
- une classification territoriale indicative.

Important :
les distances à la mer et à la montagne sont calculées à partir de points
d'ancrage représentatifs. Ce ne sont pas des distances géographiques parfaites
au trait de côte ou aux limites officielles des massifs.
"""

from __future__ import annotations

import math
from typing import Final, Iterable


Point = tuple[float, float]

EARTH_RADIUS_KM: Final[float] = 6371.0088


# ─────────────────────────────────────────────────────────────────────────────
# Points littoraux représentatifs
# ─────────────────────────────────────────────────────────────────────────────
# Format : (latitude, longitude)
POINTS_LITTORAL: Final[tuple[Point, ...]] = (
    # Bretagne / Atlantique nord
    (48.649, -2.025),  # Saint-Malo
    (48.390, -4.486),  # Brest
    (47.660, -2.760),  # Lorient
    (47.273, -2.213),  # La Baule

    # Vendée / Charente-Maritime
    (46.670, -1.943),  # Saint-Jean-de-Monts
    (46.498, -1.784),  # Les Sables-d'Olonne
    (46.161, -1.151),  # La Rochelle
    (45.937, -1.058),  # Rochefort / estuaire
    (45.620, -1.050),  # Royan

    # Gironde / Landes / Pays basque
    (44.660, -1.250),  # Lacanau-Océan
    (44.394, -1.164),  # Biscarrosse
    (43.660, -1.440),  # Capbreton
    (43.483, -1.558),  # Biarritz

    # Méditerranée
    (43.296, 5.381),   # Marseille
    (43.125, 5.930),   # Toulon
    (43.710, 7.262),   # Nice
    (43.527, 3.896),   # Montpellier / littoral proche
    (43.291, 3.493),   # Agde
    (42.688, 2.897),   # Argelès-sur-Mer
    (42.507, 3.030),   # Port-Vendres

    # Normandie / Manche / Hauts-de-France
    (49.336, -0.457),  # Courseulles-sur-Mer
    (49.494, 0.107),   # Le Havre
    (49.924, 1.085),   # Dieppe
    (50.730, 1.600),   # Boulogne-sur-Mer
    (50.731, 2.536),   # Calais
    (48.837, -1.580),  # Granville
    (49.633, -1.616),  # Cherbourg

    # Corse
    (41.920, 8.740),   # Bonifacio
    (41.919, 8.738),   # Sud Corse
    (42.700, 9.450),   # Bastia
    (42.150, 9.083),   # Ajaccio
)


# ─────────────────────────────────────────────────────────────────────────────
# Points montagne représentatifs
# ─────────────────────────────────────────────────────────────────────────────
# Ces points représentent les principaux massifs français.
# On évite volontairement les grandes villes de plaine comme Toulouse,
# Clermont-Ferrand ou Grenoble centre comme points montagne.
ZONES_MONTAGNE: Final[tuple[Point, ...]] = (
    # Alpes
    (45.923, 6.869),   # Chamonix / Mont-Blanc
    (45.297, 6.580),   # Vanoise
    (44.899, 6.642),   # Briançonnais
    (44.566, 6.496),   # Queyras
    (44.837, 6.264),   # Écrins
    (44.310, 6.650),   # Mercantour nord

    # Pyrénées
    (42.787, 0.147),   # Hautes-Pyrénées
    (42.872, -0.003),  # Gavarnie
    (42.701, 1.838),   # Ariège
    (42.501, 2.035),   # Pyrénées-Orientales
    (43.012, -0.768),  # Pyrénées-Atlantiques

    # Massif central
    (45.528, 2.814),   # Sancy
    (45.059, 2.738),   # Cantal
    (44.426, 3.739),   # Cévennes / Lozère
    (44.866, 4.220),   # Ardèche montagne
    (45.885, 3.548),   # Livradois-Forez

    # Jura
    (46.454, 6.030),   # Haut-Jura
    (46.809, 6.247),   # Jura nord

    # Vosges
    (48.063, 7.022),   # Hautes Vosges
    (47.903, 6.875),   # Ballons des Vosges

    # Corse montagneuse
    (42.304, 9.150),   # Centre Corse
)


def _to_float(value: object) -> float | None:
    """Convertit une valeur en float si possible."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


def haversine_km(lat1: object, lon1: object, lat2: object, lon2: object) -> float:
    """
    Calcule la distance haversine entre deux points GPS.

    Les coordonnées sont attendues en degrés décimaux.
    """
    latitude_1 = _to_float(lat1)
    longitude_1 = _to_float(lon1)
    latitude_2 = _to_float(lat2)
    longitude_2 = _to_float(lon2)

    if None in (latitude_1, longitude_1, latitude_2, longitude_2):
        return float("inf")

    dlat = math.radians(latitude_2 - latitude_1)
    dlon = math.radians(longitude_2 - longitude_1)

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(latitude_1))
        * math.cos(math.radians(latitude_2))
        * math.sin(dlon / 2) ** 2
    )

    a = min(1.0, max(0.0, a))

    return EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(a))


def distance_to_nearest(
    lat: object,
    lon: object,
    points: Iterable[Point],
) -> float:
    """Calcule la distance au point le plus proche."""
    latitude = _to_float(lat)
    longitude = _to_float(lon)

    if latitude is None or longitude is None:
        return float("inf")

    distances = [
        haversine_km(latitude, longitude, point_lat, point_lon)
        for point_lat, point_lon in points
    ]

    if not distances:
        return float("inf")

    return round(min(distances), 1)


def distance_to_sea_km(lat: object, lon: object) -> float:
    """Distance approximative au littoral le plus proche."""
    return distance_to_nearest(lat, lon, POINTS_LITTORAL)


def distance_to_mountain_km(lat: object, lon: object) -> float:
    """Distance approximative au massif montagneux le plus proche."""
    return distance_to_nearest(lat, lon, ZONES_MONTAGNE)


def classify_territoire(
    lat: object,
    lon: object,
    altitude: object,
    dist_mer: object,
    dist_montagne: object,
) -> str:
    """
    Classe très simplement le territoire.

    Cette classification est indicative et ne doit pas être utilisée comme
    critère fort de scoring.
    """
    altitude_value = _to_float(altitude)
    dist_mer_value = _to_float(dist_mer)
    dist_montagne_value = _to_float(dist_montagne)

    if dist_mer_value is not None and dist_mer_value <= 30:
        return "littoral"

    if altitude_value is not None and altitude_value >= 700:
        return "montagne"

    if (
        dist_montagne_value is not None
        and dist_montagne_value <= 50
        and altitude_value is not None
        and altitude_value >= 300
    ):
        return "piémont"

    return "intérieur"