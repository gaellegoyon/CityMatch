"""
agents/profile/rules.py
───────────────────────
Règles déterministes qui complètent/corrigent le JSON produit par le LLM.
"""

import re
import unicodedata

from rich.console import Console


# Coordonnées des grandes villes pour détection proximité (déterministe)
_VILLES_REF = {
    "paris", "lyon", "marseille", "bordeaux", "toulouse", "nantes", "lille",
    "strasbourg", "rennes", "montpellier", "nice", "grenoble", "toulon",
    "saint-etienne", "saint-étienne", "brest", "le havre", "reims", "dijon",
    "angers", "tours", "clermont-ferrand", "clermont", "rouen", "caen",
    "orleans", "orléans", "metz", "nancy", "mulhouse", "annecy", "avignon",
    "poitiers", "limoges", "perpignan", "bayonne", "la rochelle",
}

# Régions par grande ville (pour cohérence régionale)
_VILLE_TO_REGION = {
    "lyon": "Auvergne-Rhône-Alpes", "grenoble": "Auvergne-Rhône-Alpes",
    "saint-etienne": "Auvergne-Rhône-Alpes", "clermont-ferrand": "Auvergne-Rhône-Alpes",
    "paris": "Île-de-France",
    "marseille": "Provence-Alpes-Côte d\'Azur", "nice": "Provence-Alpes-Côte d\'Azur",
    "toulon": "Provence-Alpes-Côte d\'Azur",
    "bordeaux": "Nouvelle-Aquitaine",
    "toulouse": "Occitanie", "montpellier": "Occitanie",
    "nantes": "Pays de la Loire", "angers": "Pays de la Loire",
    "lille": "Hauts-de-France",
    "strasbourg": "Grand Est", "reims": "Grand Est",
    "rennes": "Bretagne", "brest": "Bretagne",
    "dijon": "Bourgogne-Franche-Comté",
}

from agents.common.criteria import VALID_CRITERIA_KEYS


console = Console()



def normalize_reference_name(name: str) -> str:
    """Normalise un nom de ville pour comparaison robuste."""
    if not name:
        return ""

    s = name.strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("'", " ")
    s = s.replace("’", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace("saint ", "saint-")
    return s


def extract_reference_city_from_text(user_text: str) -> dict:
    """
    Détecte une demande de proximité à une ville dans le texte utilisateur.

    Retourne par exemple :
        {"ville_reference": "lyon", "rayon_km": 80, "exclure_ville_reference": False}
    """
    if not user_text:
        return {}

    text = normalize_reference_name(user_text)

    city_aliases = {
        "saint-étienne": "saint-etienne",
        "clermont": "clermont-ferrand",
        "orleans": "orléans",
    }

    known_cities = sorted(_VILLES_REF, key=len, reverse=True)
    known_cities_norm = [(normalize_reference_name(c), city_aliases.get(c, c)) for c in known_cities]

    def find_city(raw: str) -> str | None:
        raw_norm = normalize_reference_name(raw)
        for city_norm, canonical in known_cities_norm:
            if city_norm and city_norm in raw_norm:
                return normalize_reference_name(canonical)
        return None

    # Cas explicite : "moins de 30 km de Bordeaux"
    km_match = re.search(r"moins de\s+(\d{1,3})\s*km\s+(?:de|d )\s+([a-z0-9\- ]+)", text)
    if km_match:
        city = find_city(km_match.group(2))
        if city:
            return {
                "ville_reference": city,
                "rayon_km": int(km_match.group(1)),
                "exclure_ville_reference": _detect_exclude_reference(text, city),
            }

    # Cas temps : 30 min, 45 min, 1h, 1h30, 2h
    time_patterns = [
        (r"(?:moins d ?1h|moins de 1h|une heure|1h)\s+(?:de|d |autour de|pres de|proche de)?\s*([a-z0-9\- ]+)", 80),
        (r"(?:30\s*min|demi heure|demi-heure)\s+(?:de|d |autour de|pres de|proche de)?\s*([a-z0-9\- ]+)", 40),
        (r"(?:45\s*min)\s+(?:de|d |autour de|pres de|proche de)?\s*([a-z0-9\- ]+)", 60),
        (r"(?:1h30|une heure et demie)\s+(?:de|d |autour de|pres de|proche de)?\s*([a-z0-9\- ]+)", 120),
        (r"(?:2h|deux heures)\s+(?:de|d |autour de|pres de|proche de)?\s*([a-z0-9\- ]+)", 160),
    ]
    for pattern, rayon in time_patterns:
        match = re.search(pattern, text)
        if match:
            city = find_city(match.group(1))
            if city:
                return {
                    "ville_reference": city,
                    "rayon_km": rayon,
                    "exclure_ville_reference": _detect_exclude_reference(text, city),
                }

    # Cas "proche de X", "près de X", "autour de X", "région de X"
    proximity_patterns = [
        (r"proche\s+(?:de|d )\s+([a-z0-9\- ]+)", 50),
        (r"pres\s+(?:de|d )\s+([a-z0-9\- ]+)", 50),
        (r"près\s+(?:de|d )\s+([a-z0-9\- ]+)", 50),
        (r"autour\s+(?:de|d )\s+([a-z0-9\- ]+)", 80),
        (r"banlieue\s+(?:de|d )\s+([a-z0-9\- ]+)", 80),
        (r"region\s+(?:de|d )\s+([a-z0-9\- ]+)", 80),
        (r"région\s+(?:de|d )\s+([a-z0-9\- ]+)", 80),
    ]
    for pattern, rayon in proximity_patterns:
        match = re.search(pattern, text)
        if match:
            city = find_city(match.group(1))
            if city:
                return {
                    "ville_reference": city,
                    "rayon_km": rayon,
                    "exclure_ville_reference": _detect_exclude_reference(text, city),
                }

    return {}


def extract_requested_regions_from_text(user_text: str) -> list[str]:
    """
    Détecte les régions explicitement demandées par l'utilisateur.

    Exemple :
        "Bretagne ou Sud" → ["Bretagne", "Provence-Alpes-Côte d'Azur", "Occitanie"]
    """
    if not user_text:
        return []

    text = normalize_reference_name(user_text)
    raw_lower = user_text.lower()

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

    if any(term in raw_lower for term in ["paca", "provence", "côte d'azur", "cote d'azur"]):
        add("Provence-Alpes-Côte d'Azur")

    if any(term in text for term in ["sud", "sud de la france"]):
        add("Provence-Alpes-Côte d'Azur")
        add("Occitanie")

    if any(term in text for term in ["auvergne rhone alpes", "rhone alpes", "rhône alpes"]):
        add("Auvergne-Rhône-Alpes")

    if any(term in text for term in ["ile de france", "paris"]):
        add("Île-de-France")

    if "grand est" in text:
        add("Grand Est")

    if any(term in text for term in ["hauts de france", "haut de france"]):
        add("Hauts-de-France")

    if "centre val de loire" in text:
        add("Centre-Val de Loire")

    if any(term in text for term in ["bourgogne", "franche comte", "franche comté"]):
        add("Bourgogne-Franche-Comté")

    if "corse" in text:
        add("Corse")

    return regions

def _detect_exclude_reference(text: str, city: str) -> bool:
    """Détecte les formulations du type 'près de Paris mais pas Paris'."""
    city_norm = normalize_reference_name(city)
    patterns = [
        rf"mais pas\s+{re.escape(city_norm)}",
        rf"pas\s+{re.escape(city_norm)}",
        rf"hors\s+{re.escape(city_norm)}",
        rf"sauf\s+{re.escape(city_norm)}",
        rf"eviter\s+{re.escape(city_norm)}",
        rf"éviter\s+{re.escape(city_norm)}",
    ]
    return any(re.search(p, text) for p in patterns)


def extract_real_estate_budget_from_text(user_text: str) -> dict:
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

    text = normalize_reference_name(user_text)
    raw = user_text.lower()

    # Montants acceptés :
    # - 200 000€, 200000 euros
    # - 200k, 350 k, 350K€
    # - budget 350 000
    budget = None

    k_match = re.search(r"\b(\d{2,4})\s*k\s*(?:€|eur|euros)?\b", raw, flags=re.IGNORECASE)
    if k_match:
        budget = int(k_match.group(1)) * 1000

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
            value = int(digits)
            if 50000 <= value <= 2000000:
                budget = value
                break

    if budget is None:
        return {}

    # Type de bien
    type_bien = ""
    if any(w in text for w in ["maison", "pavillon", "villa"]):
        type_bien = "maison"
    elif any(w in text for w in ["appartement", "appart", "studio", "t2", "t3", "t4"]):
        type_bien = "appartement"

    # Surface explicite si l'utilisateur la donne.
    surface = None
    surface_match = re.search(r"(\d{2,3})\s*m\s*[²2]", raw)
    if surface_match:
        surface = int(surface_match.group(1))

    if surface is None:
        # Règles par défaut.
        if type_bien == "maison":
            surface = 80
        elif type_bien == "appartement":
            surface = 45
        elif any(w in text for w in ["famille", "enfant", "enfants", "ados", "ado"]):
            surface = 90
        elif any(w in text for w in ["couple sans enfants", "couple sans enfant"]):
            surface = 55
        elif any(w in text for w in ["senior", "retraite", "retraité", "67 ans", "65 ans"]):
            surface = 55
        else:
            surface = 60

    # Bornes anti-erreur.
    surface = max(25, min(200, int(surface)))

    return {
        "budget_immobilier": int(budget),
        "surface_min_m2": surface,
        "type_bien": type_bien,
    }


def extract_population_bounds_from_text(user_text: str) -> dict:
    """
    Détecte les préférences de taille de ville.

    Retour :
        {"population_min": 10000, "population_max": 50000}
    """
    if not user_text:
        return {}

    text = normalize_reference_name(user_text)
    result = {}

    # Nombres explicites : "moins de 50 000 habitants", "plus de 100000 habitants"
    less_match = re.search(
        r"(?:moins de|maximum|max|sous|inferieur a|inférieur à)\s+(\d{1,3}(?:[\s\u202f.]?\d{3})*|\d+)\s*(?:habitants|hab)?",
        text,
    )
    if less_match:
        digits = re.sub(r"[^\d]", "", less_match.group(1))
        if digits:
            value = int(digits)
            if 1000 <= value <= 2000000:
                result["population_max"] = value

    more_match = re.search(
        r"(?:plus de|minimum|min|au moins|superieur a|supérieur à)\s+(\d{1,3}(?:[\s\u202f.]?\d{3})*|\d+)\s*(?:habitants|hab)?",
        text,
    )
    if more_match:
        digits = re.sub(r"[^\d]", "", more_match.group(1))
        if digits:
            value = int(digits)
            if 1000 <= value <= 2000000:
                result["population_min"] = value

    # Mapping sémantique. Les nombres explicites restent prioritaires.
    if any(expr in text for expr in ["village", "tres petite ville", "très petite ville"]):
        result.setdefault("population_min", 0)
        result.setdefault("population_max", 10000)

    elif "petite ville" in text:
        result.setdefault("population_min", 10000)
        result.setdefault("population_max", 50000)

    elif "ville moyenne" in text or "taille moyenne" in text:
        result.setdefault("population_min", 50000)
        result.setdefault("population_max", 150000)

    elif "grande ville" in text or "metropole" in text or "métropole" in text:
        result.setdefault("population_min", 150000)
        result.setdefault("population_max", 1000000)

    # "Ville calme" : population max + sécurité élevée.
    # On ne met pas population_min pour éviter d'exclure trop fort.
    if any(expr in text for expr in ["ville calme", "endroit calme", "tranquille", "calme"]):
        result.setdefault("population_max", 50000)
        result["force_score_securite"] = True

    # "Pas trop isolé" : éviter les toutes petites villes.
    if any(expr in text for expr in ["pas trop isole", "pas trop isolé", "pas isole", "pas isolé", "pas perdu"]):
        result.setdefault("population_min", 20000)

    # Bornes cohérentes.
    if "population_min" in result:
        result["population_min"] = max(0, int(result["population_min"]))
    if "population_max" in result:
        result["population_max"] = max(1000, int(result["population_max"]))

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


def post_correct_criteria(criteria: dict, user_messages: str) -> dict:
    """
    Correction déterministe du profil généré par le LLM, indépendante de ses
    hallucinations. Détecte la proximité d'une ville et supprime les critères
    non demandés (ex: distance_mer ajoutée sans raison).
    """
    text = user_messages.lower()

    # Conserver les régions explicitement demandées pour pouvoir expliquer
    # ensuite pourquoi certaines n'apparaissent pas dans le top 10 du rapport.
    requested_regions = extract_requested_regions_from_text(user_messages)
    if requested_regions:
        criteria["regions_demandees"] = requested_regions

    # ── 1. Détecter proximité ville de référence ─────────────────────────────
    proximite_detectee = False
    reference = extract_reference_city_from_text(user_messages)
    if reference:
        criteria["ville_reference"] = reference["ville_reference"]
        criteria["rayon_km"] = reference["rayon_km"]
        criteria["exclure_ville_reference"] = reference.get("exclure_ville_reference", False)
        proximite_detectee = True

        # Forcer la région cohérente quand on connaît la correspondance.
        ville_key = reference["ville_reference"]
        if ville_key in _VILLE_TO_REGION:
            criteria["regions_preferees"] = [_VILLE_TO_REGION[ville_key]]

        console.print(
            f"[dim]🎯 Proximité détectée : {criteria['ville_reference']} "
            f"(rayon {criteria['rayon_km']}km, exclure={criteria.get('exclure_ville_reference', False)})[/dim]"
        )

    # ── 2. Supprimer distance_mer_km si la mer n'est pas mentionnée ──────────
    mer_mots = ["mer", "littoral", "côte", "cote", "plage", "océan", "ocean", "maritime", "bord de"]
    if "distance_mer_km" in criteria.get("criteres", {}):
        if not any(m in text for m in mer_mots):
            del criteria["criteres"]["distance_mer_km"]
            console.print("[dim]🧹 distance_mer_km supprimé (mer non demandée)[/dim]")

    # ── 3. Supprimer distance_montagne_km si montagne non mentionnée ─────────
    montagne_mots = ["montagne", "ski", "alpes", "pyrénées", "pyrenees", "randonnée", "altitude"]
    if "distance_montagne_km" in criteria.get("criteres", {}):
        if not any(m in text for m in montagne_mots):
            del criteria["criteres"]["distance_montagne_km"]
            console.print("[dim]🧹 distance_montagne_km supprimé (montagne non demandée)[/dim]")

    # ── 4. Si proximité ville détectée, retirer tout filtre mer résiduel ─────
    if proximite_detectee and "distance_mer_km" in criteria.get("criteres", {}):
        del criteria["criteres"]["distance_mer_km"]


    # ── 4b. Nature / verdure ────────────────────────────────────────────────
    # Pas de source fiable en V1 pour mesurer directement les espaces naturels.
    # On utilise donc un proxy assumé "cadre peu urbain" :
    # - taille de ville limitée ;
    # - calme/sécurité ;
    # - prix immobilier plutôt bas.
    nature_terms = [
        "nature", "verdure", "ville verte", "forêt", "foret", "bois",
        "campagne", "espaces naturels", "parc naturel", "randonnée", "randonnee"
    ]
    if any(term in text for term in nature_terms):
        criteria.setdefault("criteres", {})

        # Favoriser les villes plus calmes / moins urbaines.
        criteria["criteres"]["score_securite"] = max(
            int(criteria["criteres"].get("score_securite", 0) or 0),
            4,
        )

        # Le prix bas est un proxy indirect de zones moins tendues / moins urbaines.
        criteria["criteres"]["prix_immo_m2"] = max(
            int(criteria["criteres"].get("prix_immo_m2", 0) or 0),
            3,
        )

        # Si l'utilisateur n'a pas donné de borne plus stricte, limiter la taille.
        criteria.setdefault("population_max", 50000)

        criteria.setdefault("criteres_indirects", [])
        if "nature proche" not in criteria["criteres_indirects"]:
            criteria["criteres_indirects"].append("nature proche")

        criteria.setdefault("notes", [])
        nature_note = (
            "Votre envie de nature a bien été prise en compte. "
            "Comme nous n'avons pas encore une donnée fiable sur les espaces verts, "
            "CityMatch privilégie des villes plus petites, calmes et abordables."
        )
        if nature_note not in criteria["notes"]:
            criteria["notes"].append(nature_note)

        console.print("[dim]🌿 Nature mentionnée : proxy cadre peu urbain activé[/dim]")

    # ── 5. Supprimer strictement les critères invalides ─────────────────────
    cleaned = {}
    for key, weight in criteria.get("criteres", {}).items():
        if key not in VALID_CRITERIA_KEYS:
            console.print(f"[dim]🧹 critère ignoré car non disponible : {key}[/dim]")
            continue
        try:
            weight_int = int(weight)
        except Exception:
            weight_int = 3
        weight_int = max(1, min(5, weight_int))
        cleaned[key] = max(cleaned.get(key, 0), weight_int)

    criteria["criteres"] = cleaned

    # Normaliser les clés de proximité, même si le LLM a utilisé rayon_reference_km.
    if "rayon_reference_km" in criteria and "rayon_km" not in criteria:
        criteria["rayon_km"] = criteria["rayon_reference_km"]

    # ── 6. Détecter taille de ville / population ───────────────────────────
    population_info = extract_population_bounds_from_text(user_messages)
    if population_info:
        force_secu = population_info.pop("force_score_securite", False)

        if "population_min" in population_info:
            criteria["population_min"] = population_info["population_min"]
        if "population_max" in population_info:
            criteria["population_max"] = population_info["population_max"]

        if force_secu:
            criteria.setdefault("criteres", {})
            criteria["criteres"]["score_securite"] = max(
                int(criteria["criteres"].get("score_securite", 0) or 0),
                5,
            )

        console.print(
            f"[dim]🏙️ Population détectée : "
            f"min={criteria.get('population_min')} max={criteria.get('population_max')}[/dim]"
        )

    # ── 7. Détecter budget immobilier et surface minimale ───────────────────
    budget_info = extract_real_estate_budget_from_text(user_messages)
    if budget_info:
        criteria.update(budget_info)
        max_prix = budget_info["budget_immobilier"] / max(budget_info["surface_min_m2"], 1)
        console.print(
            f"[dim]🏠 Budget détecté : {budget_info['budget_immobilier']}€ "
            f"pour {budget_info['surface_min_m2']}m² "
            f"(prix max ≈ {max_prix:.0f}€/m²)[/dim]"
        )

        # Si le prix immobilier n'est pas déjà dans les critères, on l'ajoute
        # car le budget doit influencer le classement.
        criteria.setdefault("criteres", {})
        criteria["criteres"]["prix_immo_m2"] = max(
            int(criteria["criteres"].get("prix_immo_m2", 0) or 0),
            5,
        )

    return criteria

