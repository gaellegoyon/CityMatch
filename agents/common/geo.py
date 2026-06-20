"""
agents/common/geo.py
────────────────────
Helpers géographiques génériques partagés par les agents CityMatch.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Final


EARTH_RADIUS_KM: Final[float] = 6371.0088


def normalize_place_name(name: str | None) -> str:
    """Normalise un nom de ville pour une comparaison robuste."""
    if not name:
        return ""

    normalized = str(name).strip().lower()
    normalized = unicodedata.normalize("NFKD", normalized)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = normalized.replace("'", " ").replace("’", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()

    return normalized.replace("saint ", "saint-")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calcule la distance géodésique approximative entre deux points GPS."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )

    a = min(1.0, max(0.0, a))
    return EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(a))