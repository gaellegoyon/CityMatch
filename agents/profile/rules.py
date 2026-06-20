"""
agents/profile/rules.py
───────────────────────
Règles déterministes qui complètent/corrigent le JSON produit par le LLM.

Ces règles bornent la sortie du LLM avant qu'elle ne soit utilisée par les
agents suivants : DatabaseAgent, ScoringAgent et ReportAgent.
"""

from __future__ import annotations

import copy
import logging
import re
from typing import Any

from agents.common.criteria import VALID_CRITERIA_KEYS
from agents.common.geo import normalize_place_name


logger = logging.getLogger(__name__)

DEFAULT_REFERENCE_RADIUS_KM = 80
MIN_REAL_ESTATE_BUDGET = 50_000
MAX_REAL_ESTATE_BUDGET = 2_000_000


def normalize_reference_name(name: str | None) -> str:
    """
    Normalise un nom de ville pour comparaison robuste.

    Cette fonction reste disponible pour compatibilité, mais délègue à
    agents.common.geo.normalize_place_name afin d'éviter deux logiques de
    normalisation différentes dans le projet.
    """
    return normalize_place_name(name)


def _clean_reference_city_candidate(raw_city: str | None) -> str:
    """Nettoie une ville extraite après une expression du type 'proche de X'."""
    city = normalize_place_name(raw_city)

    if not city:
        return ""

    stop_pattern = re.compile(
        r"\b("
        r"avec|mais|pour|budget|securise|securisee|calme|tranquille|"
        r"pas cher|fibre|internet|teletravail|ecoles|ecole|medecins|"
        r"sante|mer|montagne|nature|verdure|proche|pres|autour"
        r")\b"
    )

    match = stop_pattern.search(city)
    if match:
        city = city[: match.start()].strip()

    city = re.sub(r"\s+", " ", city).strip()
    city = re.sub(r"\b(de|d|du|des|en|dans)\s*$", "", city).strip()

    non_city_terms = {
        "mer",
        "la mer",
        "bord de mer",
        "plage",
        "montagne",
        "nature",
        "campagne",
        "foret",
    }

    if city in non_city_terms:
        return ""

    return city


def _detect_exclude_reference(text: str, city: str) -> bool:
    """Détecte les formulations du type 'près de Paris mais pas Paris'."""
    city_norm = normalize_place_name(city)

    if not city_norm:
        return False

    patterns = [
        rf"mais pas\s+{re.escape(city_norm)}",
        rf"pas\s+{re.escape(city_norm)}",
        rf"hors\s+{re.escape(city_norm)}",
        rf"sauf\s+{re.escape(city_norm)}",
        rf"eviter\s+{re.escape(city_norm)}",
    ]

    return any(re.search(pattern, text) for pattern in patterns)


def extract_reference_city_from_text(user_text: str | None) -> dict[str, Any]:
    """
    Détecte une demande de proximité à une ville dans le texte utilisateur.

    La fonction ne vérifie pas ici que la ville existe en base : cette
    responsabilité appartient au DatabaseAgent / repository, car la base
    cities contient déjà les coordonnées GPS des communes.
    """
    if not user_text:
        return {}

    text = normalize_place_name(user_text)

    km_patterns = [
        r"(?:a\s+)?moins de\s+(\d{1,3})\s*km\s+(?:de|d )\s+([a-z0-9\- ]+)",
        r"(\d{1,3})\s*km\s+(?:de|d )\s+([a-z0-9\- ]+)",
    ]

    for pattern in km_patterns:
        match = re.search(pattern, text)
        if match:
            radius = int(match.group(1))
            city = _clean_reference_city_candidate(match.group(2))

            if city:
                return {
                    "ville_reference": city,
                    "rayon_km": radius,
                    "exclure_ville_reference": _detect_exclude_reference(text, city),
                }

    time_patterns = [
        (
            r"(?:a\s+)?(?:moins d ?1h|moins de 1h|une heure|1h)\s+"
            r"(?:de|d |autour de|pres de|proche de)?\s*([a-z0-9\- ]+)",
            80,
        ),
        (
            r"(?:a\s+)?(?:30\s*min|demi heure|demi-heure)\s+"
            r"(?:de|d |autour de|pres de|proche de)?\s*([a-z0-9\- ]+)",
            40,
        ),
        (
            r"(?:a\s+)?(?:45\s*min)\s+"
            r"(?:de|d |autour de|pres de|proche de)?\s*([a-z0-9\- ]+)",
            60,
        ),
        (
            r"(?:a\s+)?(?:1h30|une heure et demie)\s+"
            r"(?:de|d |autour de|pres de|proche de)?\s*([a-z0-9\- ]+)",
            120,
        ),
        (
            r"(?:a\s+)?(?:2h|deux heures)\s+"
            r"(?:de|d |autour de|pres de|proche de)?\s*([a-z0-9\- ]+)",
            160,
        ),
    ]

    for pattern, radius in time_patterns:
        match = re.search(pattern, text)
        if match:
            city = _clean_reference_city_candidate(match.group(1))

            if city:
                return {
                    "ville_reference": city,
                    "rayon_km": radius,
                    "exclure_ville_reference": _detect_exclude_reference(text, city),
                }

    proximity_patterns = [
        (r"proche\s+(?:de|d )\s+([a-z0-9\- ]+)", 50),
        (r"pres\s+(?:de|d )\s+([a-z0-9\- ]+)", 50),
        (r"autour\s+(?:de|d )\s+([a-z0-9\- ]+)", 80),
        (r"banlieue\s+(?:de|d )\s+([a-z0-9\- ]+)", 80),
        (r"region\s+(?:de|d )\s+([a-z0-9\- ]+)", 80),
    ]

    for pattern, radius in proximity_patterns:
        match = re.search(pattern, text)
        if match:
            city = _clean_reference_city_candidate(match.group(1))

            if city:
                return {
                    "ville_reference": city,
                    "rayon_km": radius,
                    "exclure_ville_reference": _detect_exclude_reference(text, city),
                }

    return {}


def extract_requested_regions_from_text(user_text: str | None) -> list[str]:
    """
    Détecte les régions explicitement demandées par l'utilisateur.

    Exemple :
        "Bretagne ou Sud" → ["Bretagne", "Provence-Alpes-Côte d'Azur", "Occitanie"]
    """
    if not user_text:
        return []

    text = normalize_place_name(user_text)
    regions: list[str] = []

    def add(region: str) -> None:
        if region not in regions:
            regions.append(region)

    if any(term in text for term in ["bretagne", "breton", "bretonne"]):
        add("Bretagne")

    if any(term in text for term in ["normandie", "normand", "normande"]):
        add("Normandie")

    if any(term in text for term in ["nouvelle aquitaine", "aquitaine"]):
        add("Nouvelle-Aquitaine")

    if "pays de la loire" in text:
        add("Pays de la Loire")

    if "occitanie" in text:
        add("Occitanie")

    if any(term in text for term in ["paca", "provence", "cote d azur"]):
        add("Provence-Alpes-Côte d'Azur")

    if any(term in text for term in ["sud", "sud de la france"]):
        add("Provence-Alpes-Côte d'Azur")
        add("Occitanie")

    if any(term in text for term in ["auvergne rhone alpes", "rhone alpes"]):
        add("Auvergne-Rhône-Alpes")

    if "ile de france" in text:
        add("Île-de-France")

    if "grand est" in text:
        add("Grand Est")

    if any(term in text for term in ["hauts de france", "haut de france"]):
        add("Hauts-de-France")

    if "centre val de loire" in text:
        add("Centre-Val de Loire")

    if any(term in text for term in ["bourgogne", "franche comte"]):
        add("Bourgogne-Franche-Comté")

    if "corse" in text:
        add("Corse")

    return regions


def extract_real_estate_budget_from_text(user_text: str | None) -> dict[str, Any]:
    """
    Détecte un budget immobilier et une surface minimale réaliste.

    Retour :
        {
            "budget_immobilier": 350000,
            "surface_min_m2": 90,
            "type_bien": "maison"
        }
    """
    if not user_text:
        return {}

    text = normalize_place_name(user_text)
    raw = user_text.lower()

    budget: int | None = None

    k_match = re.search(
        r"\b(\d{2,4})\s*k\s*(?:€|eur|euros)?\b",
        raw,
        flags=re.IGNORECASE,
    )
    if k_match:
        candidate_budget = int(k_match.group(1)) * 1000
        if MIN_REAL_ESTATE_BUDGET <= candidate_budget <= MAX_REAL_ESTATE_BUDGET:
            budget = candidate_budget

    if budget is None:
        amount_matches = re.findall(
            r"(\d{2,3}(?:[\s\u202f.]?\d{3})+|\d{5,7})\s*(?:€|eur|euros)?",
            raw,
            flags=re.IGNORECASE,
        )

        for amount in amount_matches:
            digits = re.sub(r"[^\d]", "", amount)

            if not digits:
                continue

            candidate_budget = int(digits)

            if MIN_REAL_ESTATE_BUDGET <= candidate_budget <= MAX_REAL_ESTATE_BUDGET:
                budget = candidate_budget
                break

    if budget is None:
        return {}

    type_bien = ""

    if any(word in text for word in ["maison", "pavillon", "villa"]):
        type_bien = "maison"
    elif any(word in text for word in ["appartement", "appart", "studio", "t2", "t3", "t4"]):
        type_bien = "appartement"

    surface: int | None = None
    surface_match = re.search(r"(\d{2,3})\s*m\s*[²2]", raw)

    if surface_match:
        surface = int(surface_match.group(1))

    if surface is None:
        if type_bien == "maison":
            surface = 80
        elif type_bien == "appartement":
            surface = 45
        elif any(word in text for word in ["famille", "enfant", "enfants", "ados", "ado"]):
            surface = 90
        elif any(word in text for word in ["couple sans enfants", "couple sans enfant"]):
            surface = 55
        elif any(word in text for word in ["senior", "retraite", "67 ans", "65 ans"]):
            surface = 55
        else:
            surface = 60

    surface = max(25, min(200, int(surface)))

    return {
        "budget_immobilier": int(budget),
        "surface_min_m2": surface,
        "type_bien": type_bien,
    }


def extract_population_bounds_from_text(user_text: str | None) -> dict[str, Any]:
    """
    Détecte les préférences de taille de ville.

    Retour :
        {"population_min": 10000, "population_max": 50000}
    """
    if not user_text:
        return {}

    text = normalize_place_name(user_text)
    result: dict[str, Any] = {}

    less_match = re.search(
        r"(?:moins de|maximum|max|sous|inferieur a)\s+"
        r"(\d{1,3}(?:[\s\u202f.]?\d{3})*|\d+)\s*(?:habitants|hab)?",
        text,
    )
    if less_match:
        digits = re.sub(r"[^\d]", "", less_match.group(1))

        if digits:
            value = int(digits)

            if 1_000 <= value <= 2_000_000:
                result["population_max"] = value

    more_match = re.search(
        r"(?:plus de|minimum|min|au moins|superieur a)\s+"
        r"(\d{1,3}(?:[\s\u202f.]?\d{3})*|\d+)\s*(?:habitants|hab)?",
        text,
    )
    if more_match:
        digits = re.sub(r"[^\d]", "", more_match.group(1))

        if digits:
            value = int(digits)

            if 1_000 <= value <= 2_000_000:
                result["population_min"] = value

    if any(expr in text for expr in ["village", "tres petite ville"]):
        result.setdefault("population_min", 0)
        result.setdefault("population_max", 10_000)

    elif "petite ville" in text:
        result.setdefault("population_min", 10_000)
        result.setdefault("population_max", 50_000)

    elif "ville moyenne" in text or "taille moyenne" in text:
        result.setdefault("population_min", 50_000)
        result.setdefault("population_max", 150_000)

    elif "grande ville" in text or "metropole" in text:
        result.setdefault("population_min", 150_000)
        result.setdefault("population_max", 1_000_000)

    if any(expr in text for expr in ["ville calme", "endroit calme", "tranquille", "calme"]):
        result.setdefault("population_max", 50_000)
        result["force_score_securite"] = True

    if any(expr in text for expr in ["pas trop isole", "pas isole", "pas perdu"]):
        result.setdefault("population_min", 20_000)

    if "population_min" in result:
        result["population_min"] = max(0, int(result["population_min"]))

    if "population_max" in result:
        result["population_max"] = max(1_000, int(result["population_max"]))

    if (
        "population_min" in result
        and "population_max" in result
        and result["population_min"] > result["population_max"]
    ):
        result["population_min"], result["population_max"] = (
            result["population_max"],
            result["population_min"],
        )

    return result


def _clean_criteria_weights(criteria_weights: dict[str, Any] | None) -> dict[str, int]:
    """Supprime les critères invalides et borne les poids entre 1 et 5."""
    cleaned: dict[str, int] = {}

    for key, raw_weight in (criteria_weights or {}).items():
        if key not in VALID_CRITERIA_KEYS:
            logger.debug("Critère ignoré car non disponible : %s", key)
            continue

        try:
            weight = int(float(raw_weight))
        except (TypeError, ValueError):
            weight = 3

        weight = max(1, min(5, weight))
        cleaned[key] = max(cleaned.get(key, 0), weight)

    return cleaned


def _add_note(criteria: dict[str, Any], note: str) -> None:
    """Ajoute une note utilisateur sans doublon."""
    criteria.setdefault("notes", [])

    if note not in criteria["notes"]:
        criteria["notes"].append(note)


def _mark_unavailable_criterion(criteria: dict[str, Any], label: str) -> None:
    """Marque un besoin utilisateur comme non disponible en scoring direct."""
    criteria.setdefault("criteres_non_disponibles", [])

    if label not in criteria["criteres_non_disponibles"]:
        criteria["criteres_non_disponibles"].append(label)


def post_correct_criteria(criteria: dict[str, Any], user_messages: str) -> dict[str, Any]:
    """
    Corrige déterministiquement le profil généré par le LLM.

    Objectifs :
    - supprimer les critères inventés ;
    - corriger les poids ;
    - détecter budget, population, régions et ville de référence ;
    - éviter de transformer des souhaits subjectifs en faux indicateurs.
    """
    corrected = copy.deepcopy(criteria or {})
    corrected.setdefault("criteres", {})

    normalized_text = normalize_place_name(user_messages)
    raw_text = user_messages or ""

    requested_regions = extract_requested_regions_from_text(raw_text)
    if requested_regions:
        corrected["regions_demandees"] = requested_regions
        corrected["regions_preferees"] = requested_regions

    reference = extract_reference_city_from_text(raw_text)
    if reference:
        corrected["ville_reference"] = reference["ville_reference"]
        corrected["rayon_km"] = reference["rayon_km"]
        corrected["exclure_ville_reference"] = reference.get(
            "exclure_ville_reference",
            False,
        )

        logger.debug(
            "Proximité détectée : %s, rayon=%s km, exclure=%s",
            corrected["ville_reference"],
            corrected["rayon_km"],
            corrected.get("exclure_ville_reference", False),
        )

    mer_terms = [
        "mer",
        "littoral",
        "cote",
        "plage",
        "ocean",
        "maritime",
        "bord de",
    ]
    if "distance_mer_km" in corrected.get("criteres", {}):
        if not any(term in normalized_text for term in mer_terms):
            del corrected["criteres"]["distance_mer_km"]
            logger.debug("distance_mer_km supprimé : mer non demandée")

    montagne_terms = [
        "montagne",
        "ski",
        "alpes",
        "pyrenees",
        "randonnee",
        "altitude",
    ]
    if "distance_montagne_km" in corrected.get("criteres", {}):
        if not any(term in normalized_text for term in montagne_terms):
            del corrected["criteres"]["distance_montagne_km"]
            logger.debug("distance_montagne_km supprimé : montagne non demandée")

    nature_terms = [
        "nature",
        "verdure",
        "ville verte",
        "foret",
        "bois",
        "campagne",
        "espaces naturels",
        "parc naturel",
    ]
    if any(term in normalized_text for term in nature_terms):
        _mark_unavailable_criterion(corrected, "nature proche")
        _add_note(
            corrected,
            (
                "Votre envie de nature a bien été prise en compte, mais CityMatch "
                "ne dispose pas encore d'un indicateur fiable sur les espaces verts "
                "ou la proximité immédiate de la nature. Ce souhait est donc signalé "
                "dans le rapport, sans être transformé en score artificiel."
            ),
        )

    population_info = extract_population_bounds_from_text(raw_text)
    if population_info:
        force_security = bool(population_info.pop("force_score_securite", False))

        if "population_min" in population_info:
            corrected["population_min"] = population_info["population_min"]

        if "population_max" in population_info:
            corrected["population_max"] = population_info["population_max"]

        if force_security:
            corrected.setdefault("criteres", {})
            corrected["criteres"]["score_securite"] = max(
                int(corrected["criteres"].get("score_securite", 0) or 0),
                5,
            )

    budget_info = extract_real_estate_budget_from_text(raw_text)
    if budget_info:
        corrected.update(budget_info)

        corrected.setdefault("criteres", {})
        corrected["criteres"]["prix_immo_m2"] = max(
            int(corrected["criteres"].get("prix_immo_m2", 0) or 0),
            5,
        )

    if "rayon_reference_km" in corrected and "rayon_km" not in corrected:
        corrected["rayon_km"] = corrected["rayon_reference_km"]

    corrected["criteres"] = _clean_criteria_weights(corrected.get("criteres", {}))

    if "population_min" in corrected and "population_max" in corrected:
        try:
            population_min = int(corrected["population_min"])
            population_max = int(corrected["population_max"])

            if population_min > population_max:
                population_min, population_max = population_max, population_min

            corrected["population_min"] = max(0, population_min)
            corrected["population_max"] = max(1_000, population_max)

        except (TypeError, ValueError):
            corrected.pop("population_min", None)
            corrected.pop("population_max", None)

    if "rayon_km" in corrected and corrected["rayon_km"] not in (None, ""):
        try:
            corrected["rayon_km"] = max(1, int(float(corrected["rayon_km"])))
        except (TypeError, ValueError):
            corrected["rayon_km"] = DEFAULT_REFERENCE_RADIUS_KM

    return corrected