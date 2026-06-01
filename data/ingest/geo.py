"""
data/ingest/geo.py

Calculs géographiques simples utilisés par CityMatch.
"""

from __future__ import annotations

import math


POINTS_LITTORAL = [
    # Bretagne Nord
    (48.649, -2.025),  # Saint-Malo
    (48.390, -4.486),  # Brest
    (47.660, -2.760),  # Lorient
    (47.273, -2.213),  # La Baule
    # Loire-Atlantique / Vendée
    (46.670, -1.943),  # Saint-Jean-de-Monts
    (46.498, -1.784),  # Les Sables-d'Olonne
    # Charente-Maritime
    (46.161, -1.151),  # La Rochelle
    (45.937, -1.058),  # Rochefort (estuaire)
    (45.620, -1.050),  # Royan
    # Gironde / Landes
    (44.660, -1.250),  # Lacanau-Océan
    (43.660, -1.440),  # Capbreton
    # Pays Basque
    (43.483, -1.558),  # Biarritz
    # Méditerranée
    (43.296,  5.381),  # Marseille
    (43.125,  5.930),  # Toulon
    (43.710,  7.262),  # Nice
    (43.527,  3.896),  # Sète
    (43.291,  3.493),  # Agde
    (42.688,  2.897),  # Argelès-sur-Mer
    (42.507,  3.030),  # Port-Vendres
    # Normandie
    (49.336, -0.457),  # Courseulles-sur-Mer (côtier réel — remplace le faux point Caen)
    (49.494,  0.107),  # Le Havre
    (49.924,  1.085),  # Dieppe
    (50.730,  1.600),  # Boulogne-sur-Mer
    (50.731,  2.536),  # Calais
    # Manche Ouest
    (48.837, -1.580),  # Granville
    (49.633, -1.616),  # Cherbourg
    # Corse
    (41.920,  8.740),  # Bonifacio
    (42.700,  9.450),  # Bastia
]

ZONES_MONTAGNE = [
    (45.83, 6.87), (45.19, 5.72), (43.60, 1.44), (43.11, 0.15),
    (45.46, 6.21), (46.07, 6.40), (47.95, 7.35), (47.24, 6.02),
    (45.04, 2.87), (45.78, 3.09),
]


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371
    dlat = math.radians(float(lat2) - float(lat1))
    dlon = math.radians(float(lon2) - float(lon1))
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(float(lat1))) * math.cos(math.radians(float(lat2))) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def distance_to_nearest(lat, lon, points) -> float:
    return min(haversine_km(lat, lon, p[0], p[1]) for p in points)


def classify_territoire(lat, lon, altitude, dist_mer, dist_montagne) -> str:
    if dist_mer < 30:
        return "littoral"
    if altitude and altitude > 600:
        return "montagne"
    if dist_montagne < 40 and altitude and altitude > 300:
        return "piémont"
    return "urbain"
