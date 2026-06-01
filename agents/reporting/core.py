"""
agents/reporting/core.py
───────────────────────
Agent de génération de rapports CityMatch.

Produit :
- un rapport Markdown ;
- un rapport PDF propre avec ReportLab ;
- une analyse lisible des points forts et points de vigilance.

Corrections incluses :
- pas de doublon de titre dans le PDF ;
- vrais tableaux PDF pour le classement et les critères ;
- suppression des emojis dans le PDF pour éviter les carrés noirs ;
- nombre réel de villes candidates analysées ;
- formulation honnête si les filtres ont été relâchés ;
- qualité de l'air 7/10 ou 8/10 non affichée comme vigilance ;
- qualité de l'air brute affichée sans score normalisé contradictoire.
"""

from __future__ import annotations

import html
import io
import math
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config.settings import AVAILABLE_CRITERIA, MAX_CITIES_IN_REPORT, REPORTS_DIR
from graph.state import CityMatchState
from rich.console import Console


console = Console()


# ─────────────────────────────────────────────────────────────────────────────
# Temps local
# ─────────────────────────────────────────────────────────────────────────────
def _now_paris() -> datetime:
    """Retourne l'heure locale Europe/Paris."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Europe/Paris")).replace(tzinfo=None)
    except Exception:
        try:
            import pytz

            return datetime.now(pytz.timezone("Europe/Paris")).replace(tzinfo=None)
        except Exception:
            utc = datetime.now(timezone.utc).replace(tzinfo=None)
            return utc + timedelta(hours=2 if 4 <= utc.month <= 10 else 1)


# ─────────────────────────────────────────────────────────────────────────────
# Template Markdown
# ─────────────────────────────────────────────────────────────────────────────
MARKDOWN_TEMPLATE = """# 🏙️ CityMatch — Rapport de Recommandation de Villes

**Généré le :** {date}
**Session :** {session_id}
**Profil :** {profil}

---

## 📋 Résumé Exécutif

{resume_executif}

---

## 🏆 Classement des Villes Recommandées

| Rang | Ville | Région | Score | Population | Chômage | Prix m² |
|------|-------|--------|-------|------------|---------|---------|
{tableau_villes}

---

## 📊 Analyse Détaillée

{analyses_detaillees}

---

## 🔍 Critères Utilisés

| Critère | Poids | Description |
|---------|-------|-------------|
{tableau_criteres}

---

## 📚 Sources de Données

- **INSEE — Base Permanente des Équipements (BPE 2024)**
  Crèches, écoles, médecins, transports
- **INSEE — Recensement de la Population**
  Taux de chômage, démographie, revenus, logements
- **DVF — Demandes de Valeurs Foncières** (data.gouv.fr)
  Prix immobiliers réels issus des transactions enregistrées
- **Ministère de l'Intérieur / SSMSI** (data.gouv.fr)
  Statistiques de criminalité par commune
- **ARCEP**
  Couverture fibre par commune
- **ATMO / associations régionales agréées**
  Qualité de l'air quand disponible

## ⚠️ Limites & Avertissements

- Les données publiques peuvent avoir un décalage de 1 à 2 ans selon les sources
- Les scores sont relatifs à l'échantillon de villes candidates
- Une valeur manquante ne signifie pas une mauvaise performance
- Ce rapport est une aide à la décision, pas une vérité absolue
- Toujours compléter par une visite sur place, une recherche immobilière réelle et l'analyse des transports quotidiens

---

*Rapport généré par CityMatch v1.0*
"""


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de formatage
# ─────────────────────────────────────────────────────────────────────────────
def _fmt_value(raw: Any, unit: str, critere_key: str) -> str:
    """Formate une valeur brute lisiblement selon le type de critère."""
    if raw is None:
        return "N/A"

    try:
        value = float(raw)
    except (TypeError, ValueError):
        return str(raw)

    if "km" in unit or "distance" in critere_key:
        return f"{value:.0f} km"
    if "pct" in critere_key or "taux" in critere_key or "%" in unit:
        return f"{value:.1f}%"
    if "m2" in critere_key or "immo" in critere_key:
        return f"{value:,.0f} €/m²"
    if "pour_1000" in critere_key:
        return f"{value:.2f} ‰"
    if "score" in critere_key or "/10" in unit:
        return f"{value:.1f}/10"
    if "h_an" in critere_key:
        return f"{value:.0f} h/an"
    if "mm" in unit:
        return f"{value:.0f} mm"
    if "°C" in unit:
        return f"{value:.1f}°C"

    return f"{value:.1f} {unit}".strip()


def _fmt_number(value: Any, decimals: int = 1, suffix: str = "") -> str:
    """Formate un nombre nullable."""
    if value is None:
        return "N/A"

    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"

    if decimals == 0:
        return f"{number:,.0f}{suffix}"

    return f"{number:,.{decimals}f}{suffix}"


def _safe_score(value: Any) -> float | None:
    """Convertit un score normalisé nullable."""
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _raw_float(value: Any) -> float | None:
    """Convertit une valeur brute nullable."""
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _escape_reportlab(text: str) -> str:
    """Échappe du texte pour ReportLab."""
    return html.escape(str(text), quote=False)


def _markdown_inline_to_reportlab(text: str) -> str:
    """
    Convertit un Markdown inline minimal vers un markup ReportLab valide.

    **texte** devient <b>texte</b>.
    *texte* devient <i>texte</i>.
    """
    escaped = _escape_reportlab(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", escaped)
    return escaped


def _plain_text_for_pdf(text: str) -> str:
    """Supprime les emojis/icônes qui rendent des carrés dans ReportLab."""
    replacements = {
        "🏙️": "",
        "📋": "",
        "🏆": "",
        "📊": "",
        "🔍": "",
        "📚": "",
        "⚠️": "",
        "✅": "",
        "⭐": "*",
        "—": "—",
    }

    clean = str(text)
    for old, new in replacements.items():
        clean = clean.replace(old, new)

    return clean.strip()



def _smart_title_place(name: str | None) -> str | None:
    """
    Met proprement en forme un nom de ville / lieu.

    Exemple :
        lyon -> Lyon
        saint-etienne -> Saint-Etienne
    """
    if not name:
        return None

    normalized = str(name).strip()
    if not normalized:
        return None

    # Cas simples : "lyon", "paris", "marseille"
    titled = normalized.title()

    # Petites corrections typographiques fréquentes.
    replacements = {
        "Lès": "lès",
        "Les": "les",
        "Le ": "Le ",
        "La ": "La ",
        "De ": "de ",
        "Du ": "du ",
        "Des ": "des ",
        "D'": "d'",
        "L'": "l'",
    }

    for old, new in replacements.items():
        titled = titled.replace(old, new)

    # La première lettre doit rester majuscule même si la chaîne commence par un article.
    return titled[:1].upper() + titled[1:]


def _detect_requested_regions(user_criteria: dict) -> set[str]:
    """
    Détecte les régions explicitement demandées.

    On lit plusieurs champs car `preferences_texte` peut être résumé par le LLM
    et perdre une information importante comme "Bretagne ou Sud".
    """
    requested_from_rules = user_criteria.get("regions_demandees") or []
    requested = {
        str(region)
        for region in requested_from_rules
        if region
    }

    text = " ".join(
        str(user_criteria.get(key, ""))
        for key in [
            "preferences_texte",
            "user_input_raw",
            "raw_user_input",
            "original_query",
            "message",
        ]
    ).lower()

    region_aliases = {
        "Bretagne": ["bretagne", "breton", "bretonne"],
        "Normandie": ["normandie", "normand", "normande"],
        "Nouvelle-Aquitaine": ["nouvelle-aquitaine", "nouvelle aquitaine", "aquitaine"],
        "Pays de la Loire": ["pays de la loire"],
        "Occitanie": ["occitanie"],
        "Provence-Alpes-Côte d'Azur": [
            "paca",
            "provence",
            "côte d'azur",
            "cote d'azur",
            "sud",
            "sud de la france",
        ],
        "Auvergne-Rhône-Alpes": [
            "auvergne-rhône-alpes",
            "auvergne rhône alpes",
            "auvergne rhone alpes",
            "rhone-alpes",
            "rhône-alpes",
        ],
        "Île-de-France": ["île-de-france", "ile-de-france", "ile de france", "paris"],
        "Grand Est": ["grand est"],
        "Hauts-de-France": ["hauts-de-france", "hauts de france"],
        "Centre-Val de Loire": ["centre-val de loire", "centre val de loire"],
        "Bourgogne-Franche-Comté": ["bourgogne", "franche-comté", "franche comté"],
        "Corse": ["corse"],
    }

    for region, aliases in region_aliases.items():
        if any(alias in text for alias in aliases):
            requested.add(region)

    # "Sud" est large : si l'utilisateur dit "Bretagne ou Sud", on veut surtout
    # expliquer l'absence de Bretagne si le top est dominé par PACA/Occitanie.
    if "sud" in text:
        requested.add("Provence-Alpes-Côte d'Azur")
        requested.add("Occitanie")

    return requested


def _build_region_coverage_note(top_cities: list[dict], user_criteria: dict) -> str:
    """
    Explique si une région explicitement demandée n'apparaît pas dans le TOP 10.

    Exemple :
        "Bretagne ou Sud" → si aucune ville bretonne dans le TOP 10,
        on indique que la Bretagne a bien été prise en compte.
    """
    requested_regions = _detect_requested_regions(user_criteria)
    if not requested_regions:
        return ""

    top_regions = {city.get("region") for city in top_cities if city.get("region")}

    southern_regions = {
        "Provence-Alpes-Côte d'Azur",
        "Occitanie",
        "Nouvelle-Aquitaine",
    }

    missing = sorted(region for region in requested_regions if region not in top_regions)

    # Si l'utilisateur a demandé "Sud", on ne signale pas une région sud
    # manquante si une autre région sud est déjà représentée.
    if top_regions.intersection(southern_regions):
        missing = [
            region
            for region in missing
            if region not in southern_regions
        ]

    if not missing:
        return ""

    if len(missing) == 1:
        subject = f"La région {missing[0]}"
        verb = "a"
    else:
        subject = "Les régions " + ", ".join(missing)
        verb = "ont"

    return (
        f"\n\nNote : {subject} {verb} bien été prise en compte, mais n'apparaît pas dans le top 10 "
        "car d'autres villes obtiennent de meilleurs scores sur vos critères prioritaires."
    )



def _combined_user_text(user_criteria: dict) -> str:
    """Concatène tous les textes disponibles décrivant la demande utilisateur."""
    return " ".join(
        str(user_criteria.get(key, ""))
        for key in [
            "preferences_texte",
            "user_input_raw",
            "raw_user_input",
            "original_query",
            "message",
        ]
    ).lower()


def _get_user_notes(user_criteria: dict) -> list[str]:
    """
    Récupère les notes déjà produites par les agents amont.

    Ces notes peuvent correspondre à :
    - des critères non disponibles ;
    - des critères pris en compte indirectement ;
    - des limites méthodologiques spécifiques à la demande.
    """
    notes = user_criteria.get("notes") or user_criteria.get("limitations") or []
    if isinstance(notes, str):
        return [notes]
    if isinstance(notes, list):
        return [str(note) for note in notes if note]
    return []


def _detect_unhandled_requested_criteria(user_criteria: dict) -> list[str]:
    """
    Détecte les besoins exprimés par l'utilisateur mais non intégrés directement
    au score.

    Si un besoin est indiqué dans `criteres_indirects`, il n'est pas signalé
    comme ignoré : la note méthodologique produite par l'agent amont explique
    comment il est approximé.
    """
    text = _combined_user_text(user_criteria)
    active = _criteria_keys(user_criteria)
    indirect = {
        str(item).strip().lower()
        for item in user_criteria.get("criteres_indirects", [])
        if item
    }

    checks = [
        {
            "label": "nature proche / espaces verts",
            "indirect_labels": {"nature proche", "nature", "cadre peu urbain"},
            "terms": [
                "nature",
                "verdure",
                "ville verte",
                "forêt",
                "foret",
                "bois",
                "campagne",
                "espaces naturels",
                "parc naturel",
                "randonnée",
                "randonnee",
            ],
            "accepted_keys": {
                "part_espaces_naturels_pct",
                "espaces_verts_score",
                "score_nature",
                "distance_parc_naturel_km",
            },
            "reason": (
                "la source nature/espaces verts n'est pas assez fiable en V1 "
                "pour être utilisée directement dans le score final"
            ),
        },
        {
            "label": "transports en commun",
            "indirect_labels": {"transports", "transport"},
            "terms": [
                "transport",
                "transports",
                "tram",
                "metro",
                "métro",
                "bus",
                "gare",
                "train",
                "ter",
                "tgv",
            ],
            "accepted_keys": {
                "score_transports",
                "gares_pour_1000",
                "distance_gare_km",
            },
            "reason": "aucun critère transport fiable n'a été retenu pour cette analyse",
        },
        {
            "label": "commerces de proximité",
            "indirect_labels": {"commerces", "commerces de proximité"},
            "terms": [
                "commerce",
                "commerces",
                "centre-ville",
                "centre ville",
                "services de proximité",
                "services de proximite",
                "marché",
                "marche",
            ],
            "accepted_keys": {
                "commerces_pour_1000",
                "supermarches_pour_1000",
                "restaurants_score",
            },
            "reason": "ce besoin n'a pas été transformé en critère pondéré dans cette analyse",
        },
        {
            "label": "culture / loisirs",
            "indirect_labels": {"culture", "loisirs"},
            "terms": [
                "culture",
                "loisirs",
                "cinéma",
                "cinema",
                "théâtre",
                "theatre",
                "musée",
                "musee",
                "sport",
                "sports",
            ],
            "accepted_keys": {
                "culture_score",
                "sports_loisirs_score",
                "equipements_loisirs_pour_1000",
            },
            "reason": "ce besoin n'a pas été transformé en critère pondéré dans cette analyse",
        },
        {
            "label": "hôpital / accès hospitalier",
            "indirect_labels": {"hôpital", "hopital", "accès hospitalier"},
            "terms": [
                "hopital",
                "hôpital",
                "clinique",
                "urgences",
                "chu",
            ],
            "accepted_keys": {
                "hopitaux_pour_1000",
                "distance_hopital_km",
                "medecins_generalistes_pour_1000",
                "medecins_specialistes_pour_1000",
            },
            "reason": "l'analyse utilise les équipements médicaux disponibles mais pas un accès hospitalier détaillé",
        },
        {
            "label": "calme",
            "indirect_labels": {"calme"},
            "terms": [
                "calme",
                "tranquille",
                "paisible",
                "silencieux",
            ],
            "accepted_keys": {
                "score_securite",
                "criminalite_pour_1000",
                "population",
            },
            "reason": "le calme est seulement approximé par la sécurité et la taille de ville",
        },
        {
            "label": "fibre",
            "indirect_labels": {"fibre", "internet", "télétravail", "teletravail"},
            "terms": [
                "fibre",
                "internet",
                "télétravail",
                "teletravail",
                "remote",
            ],
            "accepted_keys": {
                "fibre_pct",
            },
            "reason": "aucun critère fibre n'a été retenu dans le score final",
        },
    ]

    notes: list[str] = []

    for check in checks:
        if not any(term in text for term in check["terms"]):
            continue

        if active.intersection(check["accepted_keys"]):
            continue

        if indirect.intersection(check.get("indirect_labels", set())):
            continue

        label = check["label"]
        reason = check["reason"]
        notes.append(
            f"Le besoin « {label} » a été compris, mais n'est pas utilisé directement "
            f"dans le score final : {reason}."
        )

    # Notes ajoutées explicitement par les agents amont, notamment les critères
    # pris en compte indirectement comme "nature proche".
    notes.extend(_get_user_notes(user_criteria))

    # Déduplication stable.
    unique = []
    seen = set()
    for note in notes:
        normalized = note.strip()
        if normalized and normalized not in seen:
            unique.append(normalized)
            seen.add(normalized)

    return unique


def _build_unhandled_criteria_note(user_criteria: dict) -> str:
    """
    Construit le bloc de notes sur les critères non pris en compte directement.

    Le titre reste volontairement général : il couvre aussi les critères pris en
    compte indirectement via proxy.
    """
    notes = _detect_unhandled_requested_criteria(user_criteria)
    if not notes:
        return ""

    lines = "\n".join(f"- {note}" for note in notes)
    return f"\n\nNotes méthodologiques sur les critères :\n{lines}"


def _score_suffix_for_display(criterion_key: str, normalized_score: Any) -> str:
    """
    Suffixe affiché après une valeur brute dans l'analyse.

    Pour la qualité de l'air, on évite :
        Qualité de l'air : 8.0/10 (score 0.0/10)
    car c'est contradictoire pour l'utilisateur.
    """
    score = _safe_score(normalized_score)

    if criterion_key == "qualite_air_score":
        return ""

    if score is None:
        return ""

    return f" (score {score:.1f}/10)"


def _is_real_vigilance(criterion_key: str, raw_value: Any, normalized_score: Any) -> bool:
    """
    Détermine si un critère doit vraiment apparaître en point de vigilance.

    On évite d'afficher une bonne valeur brute comme critique uniquement parce
    qu'elle est moins bonne que les meilleures villes de l'échantillon.
    """
    score = _safe_score(normalized_score)
    raw = _raw_float(raw_value)

    if score is None:
        return False

    if criterion_key == "qualite_air_score":
        # 7/10 ou 8/10 est correct et ne doit pas apparaître comme critique.
        return raw is not None and raw < 7

    if criterion_key == "score_securite":
        return raw is not None and raw < 5.5

    if criterion_key == "distance_mer_km":
        return raw is not None and raw > 30

    if criterion_key == "distance_montagne_km":
        return raw is not None and raw > 80

    if criterion_key == "fibre_pct":
        return raw is not None and raw < 80

    if criterion_key == "prix_immo_m2":
        # Pour le prix, la valeur brute dépend du budget : le score normalisé reste utile.
        return score < 4

    return score < 4


def _is_real_strength(criterion_key: str, raw_value: Any, normalized_score: Any) -> bool:
    """Détermine si un critère peut être affiché comme point fort."""
    score = _safe_score(normalized_score)
    raw = _raw_float(raw_value)

    if score is None:
        return False

    if criterion_key == "qualite_air_score":
        return raw is not None and raw >= 7

    if criterion_key == "score_securite":
        return raw is not None and raw >= 6

    return score >= 6



def _criteria_keys(user_criteria: dict) -> set[str]:
    """Retourne les clés de critères utilisateur."""
    criteres = user_criteria.get("criteres", {})
    return set(criteres.keys()) if isinstance(criteres, dict) else set()


def _wants_criterion(user_criteria: dict, criterion_key: str) -> bool:
    """Indique si un critère a été explicitement retenu pour le scoring."""
    return criterion_key in _criteria_keys(user_criteria)


def _get_reference_city_name(user_criteria: dict) -> str | None:
    """
    Récupère le nom d'une ville de référence si l'utilisateur a demandé
    une proximité géographique, par exemple "proche de Lyon".
    """
    candidates = [
        user_criteria.get("ville_reference"),
        user_criteria.get("reference_city"),
        user_criteria.get("reference_city_name"),
        user_criteria.get("near_city"),
        user_criteria.get("proche_de"),
    ]

    location = user_criteria.get("location") or {}
    if isinstance(location, dict):
        candidates.extend(
            [
                location.get("ville_reference"),
                location.get("reference_city"),
                location.get("nom"),
                location.get("city"),
            ]
        )

    for value in candidates:
        if value:
            return str(value)

    return None


def _get_reference_distance(city: dict) -> tuple[str | None, float | None]:
    """
    Récupère une distance à une ville de référence si elle est présente
    dans les données ville.

    Plusieurs noms de clés sont supportés pour rester compatible avec les
    agents existants.
    """
    name_candidates = [
        city.get("reference_city"),
        city.get("reference_city_name"),
        city.get("ville_reference"),
        city.get("distance_reference_name"),
        city.get("near_city"),
    ]
    distance_candidates = [
        city.get("distance_reference_km"),
        city.get("distance_to_reference_km"),
        city.get("distance_ville_reference_km"),
        city.get("distance_to_city_km"),
        city.get("distance_proximite_km"),
    ]

    name = next((str(value) for value in name_candidates if value), None)

    distance = None
    for value in distance_candidates:
        try:
            if value is not None:
                distance = float(value)
                break
        except (TypeError, ValueError):
            continue

    return name, distance


def _should_show_sea_distance(user_criteria: dict) -> bool:
    """Affiche la distance à la mer seulement si elle est pertinente."""
    if _wants_criterion(user_criteria, "distance_mer_km"):
        return True

    text = str(user_criteria.get("preferences_texte", "")).lower()
    return any(
        word in text
        for word in ["mer", "littoral", "océan", "ocean", "côte", "cote", "bord de mer"]
    )


def _should_show_mountain_distance(user_criteria: dict) -> bool:
    """Affiche la distance à la montagne seulement si elle est pertinente."""
    if _wants_criterion(user_criteria, "distance_montagne_km"):
        return True

    text = str(user_criteria.get("preferences_texte", "")).lower()
    return any(word in text for word in ["montagne", "alpes", "pyrénées", "pyrenees"])


def _append_reference_distance_if_available(
    details: list[str],
    best: dict,
    user_criteria: dict,
) -> None:
    """Ajoute la distance à la ville de référence si disponible."""
    reference_name_from_city, distance = _get_reference_distance(best)
    reference_name = _smart_title_place(
        reference_name_from_city or _get_reference_city_name(user_criteria)
    )

    if reference_name and distance is not None:
        details.append(f"à **{distance:.0f} km de {reference_name}**")


# ─────────────────────────────────────────────────────────────────────────────
# Sections Markdown
# ─────────────────────────────────────────────────────────────────────────────
def build_resume_executif(
    top_cities: list[dict],
    user_criteria: dict,
    candidate_count: int | None = None,
) -> str:
    """Génère un résumé exécutif en langage naturel."""
    if not top_cities:
        return "Aucune ville trouvée correspondant à vos critères."

    best = top_cities[0]
    profil = user_criteria.get("profil", "inconnu")
    pref_texte = user_criteria.get("preferences_texte", "")

    display_count = len(top_cities)
    candidate_count = candidate_count or display_count

    criteres = user_criteria.get("criteres", {})
    top_criteres = sorted(criteres.items(), key=lambda item: item[1], reverse=True)[:3]
    criteres_texte = ", ".join(
        AVAILABLE_CRITERIA.get(key, {}).get("label", key)
        for key, _ in top_criteres
    ) or "vos critères"

    details_best = []

    _append_reference_distance_if_available(details_best, best, user_criteria)

    if _should_show_sea_distance(user_criteria) and best.get("distance_mer_km") is not None:
        details_best.append(f"à **{best['distance_mer_km']:.0f} km de la mer**")

    if _should_show_mountain_distance(user_criteria) and best.get("distance_montagne_km") is not None:
        details_best.append(f"à **{best['distance_montagne_km']:.0f} km de la montagne**")

    if best.get("prix_immo_m2") is not None:
        details_best.append(f"prix immobilier moyen **{best['prix_immo_m2']:,.0f} €/m²**")

    if (
        best.get("ecoles_pour_1000_enfants") is not None
        and _wants_criterion(user_criteria, "ecoles_pour_1000_enfants")
    ):
        details_best.append(f"écoles primaires **{best['ecoles_pour_1000_enfants']:.2f} ‰**")

    if best.get("creches_pour_1000") is not None and _wants_criterion(user_criteria, "creches_pour_1000"):
        details_best.append(f"crèches **{best['creches_pour_1000']:.2f} ‰**")

    if best.get("taux_chomage") is not None and _wants_criterion(user_criteria, "taux_chomage"):
        details_best.append(f"taux de chômage **{best['taux_chomage']:.1f}%**")

    if best.get("score_securite") is not None and _wants_criterion(user_criteria, "score_securite"):
        details_best.append(f"score sécurité **{best['score_securite']:.1f}/10**")

    if best.get("qualite_air_score") is not None and _wants_criterion(user_criteria, "qualite_air_score"):
        details_best.append(f"qualité de l'air **{best['qualite_air_score']:.1f}/10**")

    details_str = " — ".join(details_best)
    region_coverage_note = _build_region_coverage_note(top_cities, user_criteria)
    unhandled_criteria_note = _build_unhandled_criteria_note(user_criteria)

    return f"""Sur la base de votre profil **{profil}**, {candidate_count} villes candidates ont été analysées,
dont les {display_count} meilleures sont présentées ci-dessous.

Le classement tient compte de vos critères prioritaires : **{criteres_texte}**.

**La ville recommandée en premier choix est {best['nom']}** ({best.get('region', '?')})
avec un score global de **{best['total_score']:.1f}/100**.
{details_str}

{pref_texte if pref_texte else ''}{region_coverage_note}{unhandled_criteria_note}

Les premières villes sont les meilleurs compromis trouvés selon vos critères.
Certains filtres peuvent être relâchés progressivement lorsqu'ils sont trop restrictifs.
Consultez l'analyse détaillée ci-dessous pour affiner votre choix."""


def build_tableau_villes(top_cities: list[dict]) -> str:
    """Génère les lignes du tableau Markdown des villes."""
    rows = []

    for city in top_cities:
        chomage = city.get("taux_chomage")
        prix = city.get("prix_immo_m2")

        rows.append(
            f"| #{city.get('rank', '?')} | **{city.get('nom', '?')}** | {city.get('region', '?')} | "
            f"**{city.get('total_score', 0):.1f}/100** | {city.get('population', 0):,} | "
            f"{_fmt_number(chomage, 1, '%')} | "
            f"{_fmt_number(prix, 0, ' €/m²')} |"
        )

    return "\n".join(rows)


def build_analyse_ville(city: dict) -> str:
    """Génère l'analyse détaillée d'une ville."""
    score_details = city.get("score_details", {})

    sorted_criteres = sorted(
        score_details.items(),
        key=lambda item: item[1].get("normalized_score") or 0,
        reverse=True,
    )

    points_forts = [
        (key, detail)
        for key, detail in sorted_criteres
        if detail.get("has_data", True)
        and detail.get("normalized_score") is not None
        and _is_real_strength(key, detail.get("raw_value"), detail.get("normalized_score"))
    ][:5]

    points_faibles = [
        (key, detail)
        for key, detail in sorted_criteres
        if detail.get("has_data", True)
        and detail.get("normalized_score") is not None
        and detail.get("raw_value") not in (None, 0, 0.0)
        and _is_real_vigilance(key, detail.get("raw_value"), detail.get("normalized_score"))
    ][:3]

    forts_text = "\n".join(
        f"  - ✅ **{detail['label']}** : "
        f"{_fmt_value(detail.get('raw_value'), detail.get('unit', ''), key)}"
        f"{_score_suffix_for_display(key, detail.get('normalized_score'))}"
        for key, detail in points_forts
    )

    faibles_text = "\n".join(
        f"  - ⚠️  **{detail['label']}** : "
        f"{_fmt_value(detail.get('raw_value'), detail.get('unit', ''), key)}"
        f"{_score_suffix_for_display(key, detail.get('normalized_score'))}"
        for key, detail in points_faibles
    )

    web_insights = city.get("web_insights", "")
    web_section = (
        f"\n**Informations récentes :** {web_insights[:300]}..."
        if web_insights
        else ""
    )

    return f"""### #{city.get('rank', '?')} — {city.get('nom', '?')} ({city.get('region', '?')})

> **Score global : {city.get('total_score', 0):.1f}/100** | Population : {city.get('population', 0):,} hab.

**Points forts :**
{forts_text if forts_text else "  - Performances globalement équilibrées"}

**Points de vigilance :**
{faibles_text if faibles_text else "  - Aucun point critique détecté"}
{web_section}

---
"""


def build_tableau_criteres(user_criteria: dict) -> str:
    """Génère le tableau Markdown des critères utilisateur."""
    criteres = user_criteria.get("criteres", {})

    return "\n".join(
        f"| {AVAILABLE_CRITERIA.get(key, {}).get('label', key)} | "
        f"{'⭐' * int(weight)} ({int(weight)}/5) | "
        f"{AVAILABLE_CRITERIA.get(key, {}).get('description', '?')} |"
        for key, weight in sorted(criteres.items(), key=lambda item: item[1], reverse=True)
    )


def generate_markdown_report(state: CityMatchState) -> str:
    """Génère le rapport complet en Markdown."""
    top_cities = state.get("top_cities", [])[:MAX_CITIES_IN_REPORT]
    scored_cities = state.get("scored_cities") or top_cities
    user_criteria = state.get("user_criteria", {})
    session_id = state.get("session_id", "unknown")

    return MARKDOWN_TEMPLATE.format(
        date=_now_paris().strftime("%d/%m/%Y à %H:%M"),
        session_id=session_id[:8] + "...",
        profil=user_criteria.get("profil", "Non défini"),
        resume_executif=build_resume_executif(
            top_cities=top_cities,
            user_criteria=user_criteria,
            candidate_count=len(scored_cities),
        ),
        tableau_villes=build_tableau_villes(top_cities),
        analyses_detaillees="\n".join(build_analyse_ville(city) for city in top_cities),
        tableau_criteres=build_tableau_criteres(user_criteria),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Graphique radar
# ─────────────────────────────────────────────────────────────────────────────
def generate_radar_chart(top_cities: list[dict], user_criteria: dict) -> bytes | None:
    """Génère un graphique radar comparant les TOP 5 villes en PNG."""
    try:
        import matplotlib

        matplotlib.use("Agg")

        import matplotlib.pyplot as plt
        import numpy as np  # noqa: F401

        criteres = user_criteria.get("criteres", {})
        if not criteres or not top_cities:
            return None

        top_criteres = sorted(criteres.items(), key=lambda item: item[1], reverse=True)[:7]
        labels = [
            AVAILABLE_CRITERIA.get(key, {}).get("label", key)[:20]
            for key, _ in top_criteres
        ]

        n_labels = len(labels)
        if n_labels < 3:
            return None

        angles = [index / float(n_labels) * 2 * math.pi for index in range(n_labels)]
        angles += angles[:1]

        colors_list = ["#4299e1", "#48bb78", "#ed8936", "#9f7aea", "#f56565"]

        fig, ax = plt.subplots(figsize=(7, 6), subplot_kw={"projection": "polar"})
        ax.set_facecolor("#f8fafc")
        fig.patch.set_facecolor("white")

        for idx, city in enumerate(top_cities[:5]):
            details = city.get("score_details", {})
            values = []

            for key, _ in top_criteres:
                detail = details.get(key, {})
                value = detail.get("normalized_score", 5.0) if detail else 5.0
                values.append(float(value))

            values += values[:1]
            color = colors_list[idx % len(colors_list)]

            ax.plot(
                angles,
                values,
                "o-",
                linewidth=2,
                color=color,
                label=city.get("nom", f"Ville {idx + 1}"),
                alpha=0.9,
            )
            ax.fill(angles, values, alpha=0.08, color=color)

        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(labels, size=8, color="#2d3748")
        ax.set_ylim(0, 10)
        ax.set_yticks([2, 4, 6, 8, 10])
        ax.set_yticklabels(["2", "4", "6", "8", "10"], size=7, color="#718096")
        ax.grid(color="#e2e8f0", linestyle="--", linewidth=0.7, alpha=0.8)
        ax.spines["polar"].set_color("#cbd5e0")
        ax.set_title(
            "Comparaison des villes — critères pondérés",
            size=11,
            color="#1a365d",
            pad=20,
            fontweight="bold",
        )
        ax.legend(
            loc="upper right",
            bbox_to_anchor=(1.35, 1.15),
            fontsize=8,
            framealpha=0.9,
            edgecolor="#cbd5e0",
        )

        plt.tight_layout()

        buf = io.BytesIO()
        fig.savefig(
            buf,
            format="png",
            dpi=150,
            bbox_inches="tight",
            facecolor="white",
            edgecolor="none",
        )
        buf.seek(0)
        png_bytes = buf.read()
        plt.close(fig)
        return png_bytes

    except ImportError:
        console.print("[yellow]⚠️  matplotlib non disponible — radar ignoré[/yellow]")
        return None
    except Exception as exc:
        console.print(f"[yellow]⚠️  Erreur radar : {exc}[/yellow]")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# PDF helpers
# ─────────────────────────────────────────────────────────────────────────────
def _pdf_paragraph(text: str, style):
    """Crée un paragraphe ReportLab à partir de texte Markdown inline."""
    from reportlab.platypus import Paragraph

    return Paragraph(_markdown_inline_to_reportlab(_plain_text_for_pdf(text)), style)


def _add_pdf_city_table(story: list, top_cities: list[dict], styles: dict) -> None:
    """Ajoute un vrai tableau de classement au PDF."""
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    data = [
        [
            Paragraph("<b>Rang</b>", styles["table_header"]),
            Paragraph("<b>Ville</b>", styles["table_header"]),
            Paragraph("<b>Région</b>", styles["table_header"]),
            Paragraph("<b>Score</b>", styles["table_header"]),
            Paragraph("<b>Pop.</b>", styles["table_header"]),
            Paragraph("<b>Prix m²</b>", styles["table_header"]),
        ]
    ]

    for city in top_cities:
        data.append(
            [
                f"#{city.get('rank', '?')}",
                Paragraph(_escape_reportlab(city.get("nom", "?")), styles["table_cell"]),
                Paragraph(_escape_reportlab(city.get("region", "?")), styles["table_cell"]),
                f"{city.get('total_score', 0):.1f}/100",
                _fmt_number(city.get("population"), 0),
                _fmt_number(city.get("prix_immo_m2"), 0, " €"),
            ]
        )

    table = Table(
        data,
        colWidths=[1.2 * cm, 4.0 * cm, 4.2 * cm, 2.0 * cm, 2.0 * cm, 2.2 * cm],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a365d")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ]
        )
    )

    story.append(table)
    story.append(Spacer(1, 0.4 * cm))


def _add_pdf_criteria_table(story: list, user_criteria: dict, styles: dict) -> None:
    """Ajoute un vrai tableau des critères au PDF."""
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    criteres = user_criteria.get("criteres", {})
    if not criteres:
        story.append(_pdf_paragraph("Aucun critère spécifique renseigné.", styles["body"]))
        return

    data = [
        [
            Paragraph("<b>Critère</b>", styles["table_header"]),
            Paragraph("<b>Poids</b>", styles["table_header"]),
            Paragraph("<b>Description</b>", styles["table_header"]),
        ]
    ]

    for key, weight in sorted(criteres.items(), key=lambda item: item[1], reverse=True):
        meta = AVAILABLE_CRITERIA.get(key, {})
        data.append(
            [
                Paragraph(_escape_reportlab(meta.get("label", key)), styles["table_cell"]),
                f"{int(weight)}/5",
                Paragraph(_escape_reportlab(meta.get("description", "?")), styles["table_cell"]),
            ]
        )

    table = Table(
        data,
        colWidths=[5.0 * cm, 1.5 * cm, 9.0 * cm],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a365d")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ]
        )
    )

    story.append(table)
    story.append(Spacer(1, 0.4 * cm))


def _add_pdf_sources_and_limits(story: list, styles: dict) -> None:
    """Ajoute les sources et limites au PDF."""
    story.append(_pdf_paragraph("Sources de Données", styles["h1"]))

    sources = [
        "INSEE — Base Permanente des Équipements (BPE 2024) : crèches, écoles, médecins, transports",
        "INSEE — Recensement de la Population : chômage, démographie, revenus, logements",
        "DVF — Prix immobiliers réels issus des transactions enregistrées",
        "SSMSI / Ministère de l'Intérieur : statistiques de criminalité par commune",
        "ARCEP : couverture fibre par commune",
        "ATMO / associations régionales agréées : qualité de l'air quand disponible",
    ]
    for source in sources:
        story.append(_pdf_paragraph(f"• {source}", styles["body"]))

    story.append(_pdf_paragraph("Limites & Avertissements", styles["h1"]))
    limits = [
        "Les données publiques peuvent avoir un décalage de 1 à 2 ans selon les sources.",
        "Les scores sont relatifs à l'échantillon de villes candidates.",
        "Une valeur manquante ne signifie pas une mauvaise performance.",
        "Ce rapport est une aide à la décision, pas une vérité absolue.",
        "Toujours compléter par une visite sur place, une recherche immobilière réelle et l'analyse des transports quotidiens.",
    ]
    for limit in limits:
        story.append(_pdf_paragraph(f"• {limit}", styles["body"]))


# ─────────────────────────────────────────────────────────────────────────────
# PDF
# ─────────────────────────────────────────────────────────────────────────────
def generate_pdf_report(
    markdown_content: str,
    output_path: Path,
    top_cities: list | None = None,
    user_criteria: dict | None = None,
) -> bool:
    """Convertit le rapport en PDF propre avec ReportLab."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import HRFlowable, Image as RLImage, SimpleDocTemplate, Spacer

        top_cities = top_cities or []
        user_criteria = user_criteria or {}

        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            rightMargin=2 * cm,
            leftMargin=2 * cm,
            topMargin=2.2 * cm,
            bottomMargin=2 * cm,
        )

        sample_styles = getSampleStyleSheet()
        styles = {
            "title": ParagraphStyle(
                "CityTitle",
                parent=sample_styles["Title"],
                fontSize=20,
                leading=24,
                textColor=colors.HexColor("#1a365d"),
                spaceAfter=10,
            ),
            "h1": ParagraphStyle(
                "CityH1",
                parent=sample_styles["Heading1"],
                fontSize=14,
                leading=18,
                textColor=colors.HexColor("#2b6cb0"),
                spaceBefore=14,
                spaceAfter=8,
            ),
            "h2": ParagraphStyle(
                "CityH2",
                parent=sample_styles["Heading2"],
                fontSize=11,
                leading=14,
                textColor=colors.HexColor("#2c5282"),
                spaceBefore=10,
                spaceAfter=5,
            ),
            "body": ParagraphStyle(
                "CityBody",
                parent=sample_styles["Normal"],
                fontSize=9,
                leading=13,
                spaceAfter=5,
            ),
            "table_header": ParagraphStyle(
                "TableHeader",
                parent=sample_styles["Normal"],
                fontSize=7,
                leading=9,
                textColor=colors.white,
            ),
            "table_cell": ParagraphStyle(
                "TableCell",
                parent=sample_styles["Normal"],
                fontSize=7,
                leading=9,
            ),
        }

        story = [
            _pdf_paragraph("CityMatch — Rapport de Recommandation", styles["title"]),
            _pdf_paragraph(f"**Généré le :** {_now_paris().strftime('%d/%m/%Y à %H:%M')}", styles["body"]),
            HRFlowable(width="100%", thickness=2, color=colors.HexColor("#2b6cb0")),
            Spacer(1, 0.4 * cm),
        ]

        if top_cities and user_criteria:
            radar_png = generate_radar_chart(top_cities, user_criteria)
            if radar_png:
                story.append(_pdf_paragraph("Comparaison radar des TOP villes", styles["h1"]))
                radar_buf = io.BytesIO(radar_png)
                story.append(RLImage(radar_buf, width=12 * cm, height=10 * cm))
                story.append(
                    _pdf_paragraph(
                        "Axes = critères pondérés par l'utilisateur. "
                        "Valeurs normalisées 0–10 (10 = optimal).",
                        styles["body"],
                    )
                )
                story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0")))

        # PDF structuré directement depuis les données, pas en reconvertissant
        # tout le Markdown. Cela évite le doublon de titre et les tableaux cassés.
        story.append(_pdf_paragraph("Résumé Exécutif", styles["h1"]))
        resume_match = re.search(
            r"## 📋 Résumé Exécutif\s+(.*?)\s+---\s+## 🏆",
            markdown_content,
            flags=re.DOTALL,
        )
        if resume_match:
            resume_text = resume_match.group(1).strip()
        else:
            resume_text = "Rapport généré selon les critères utilisateur."

        for paragraph in resume_text.split("\n\n"):
            if paragraph.strip():
                story.append(_pdf_paragraph(paragraph.strip(), styles["body"]))

        story.append(_pdf_paragraph("Classement des Villes Recommandées", styles["h1"]))
        _add_pdf_city_table(story, top_cities, styles)

        story.append(_pdf_paragraph("Analyse Détaillée", styles["h1"]))
        for city in top_cities[:MAX_CITIES_IN_REPORT]:
            story.append(
                _pdf_paragraph(
                    f"#{city.get('rank', '?')} — {city.get('nom', '?')} ({city.get('region', '?')})",
                    styles["h2"],
                )
            )
            story.append(
                _pdf_paragraph(
                    f"Score global : {city.get('total_score', 0):.1f}/100 | "
                    f"Population : {_fmt_number(city.get('population'), 0)} hab.",
                    styles["body"],
                )
            )

            analysis_md = build_analyse_ville(city)
            forts = _extract_markdown_block(analysis_md, "Points forts :", "Points de vigilance :")
            vigilances = _extract_markdown_block(analysis_md, "Points de vigilance :", "---")

            story.append(_pdf_paragraph("Points forts :", styles["body"]))
            _add_pdf_bullets(story, forts, styles["body"])

            story.append(_pdf_paragraph("Points de vigilance :", styles["body"]))
            _add_pdf_bullets(story, vigilances, styles["body"])

        story.append(_pdf_paragraph("Critères Utilisés", styles["h1"]))
        _add_pdf_criteria_table(story, user_criteria, styles)

        _add_pdf_sources_and_limits(story, styles)

        doc.build(story)
        return True

    except Exception as exc:
        console.print(f"[yellow]⚠️  Erreur génération PDF : {exc}[/yellow]")

        md_path = output_path.with_suffix(".md")
        with open(md_path, "w", encoding="utf-8") as handle:
            handle.write(markdown_content)

        console.print(f"[dim]Rapport sauvegardé en Markdown : {md_path}[/dim]")
        return False


def _extract_markdown_block(markdown_text: str, start_label: str, end_label: str) -> str:
    """Extrait un bloc simple entre deux labels Markdown."""
    start = markdown_text.find(start_label)
    if start == -1:
        return ""

    start += len(start_label)
    end = markdown_text.find(end_label, start)
    if end == -1:
        end = len(markdown_text)

    return markdown_text[start:end].strip()


def _add_pdf_bullets(story: list, block: str, style) -> None:
    """Ajoute des lignes bullet au PDF en nettoyant Markdown et emojis."""
    if not block.strip():
        story.append(_pdf_paragraph("• Aucun élément à signaler", style))
        return

    lines = [line.strip() for line in block.splitlines() if line.strip()]
    added = False

    for line in lines:
        clean = line.strip()

        # Retire les préfixes de liste Markdown.
        clean = re.sub(r"^[-•]\s*", "", clean)
        clean = re.sub(r"^\s*-\s*", "", clean)

        # Retire les emojis/icônes qui deviennent des carrés en PDF.
        clean = _plain_text_for_pdf(clean).strip()

        # Supprime les restes de titres de section Markdown.
        title_like = clean.strip("* ").lower()
        if title_like in {
            "points forts",
            "points forts :",
            "points de vigilance",
            "points de vigilance :",
        }:
            continue

        # Cas parasite issu de **Points forts :** / **Points de vigilance :**
        # après extraction de bloc.
        if clean in {"**", "*", ":", ":**"}:
            continue

        if clean:
            story.append(_pdf_paragraph(f"• {clean}", style))
            added = True

    if not added:
        story.append(_pdf_paragraph("• Aucun élément à signaler", style))


def run_report_agent(state: CityMatchState) -> CityMatchState:
    """Nœud LangGraph : génère le rapport Markdown/PDF."""
    start_time = time.time()
    console.print("\n[bold cyan]📄 ReportAgent activé[/bold cyan]")

    if not state.get("top_cities"):
        console.print("[yellow]⚠️  Aucune ville top pour le rapport.[/yellow]")
        state["analysis_complete"] = True
        return state

    markdown_content = generate_markdown_report(state)
    state["report_markdown"] = markdown_content

    timestamp = _now_paris().strftime("%Y%m%d_%H%M%S")
    session_short = state.get("session_id", "unknown")[:8]
    filename = f"citymatch_report_{session_short}_{timestamp}"

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    md_path = REPORTS_DIR / f"{filename}.md"
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(markdown_content)

    console.print(f"[green]✅ Rapport Markdown : {md_path}[/green]")

    pdf_path = REPORTS_DIR / f"{filename}.pdf"
    pdf_success = generate_pdf_report(
        markdown_content=markdown_content,
        output_path=pdf_path,
        top_cities=state.get("top_cities", []),
        user_criteria=state.get("user_criteria", {}),
    )

    if pdf_success:
        state["report_pdf_path"] = str(pdf_path)
        console.print(f"[green]✅ Rapport PDF : {pdf_path}[/green]")
    else:
        state["report_pdf_path"] = str(md_path)

    from db.models import SearchSession, SessionLocal

    db = SessionLocal()
    try:
        session = db.query(SearchSession).filter_by(id=state.get("session_id")).first()
        if session:
            session.top_cities = [
                {
                    "nom": city.get("nom"),
                    "score": city.get("total_score"),
                    "rank": city.get("rank"),
                }
                for city in state.get("top_cities", [])
            ]
            session.report_path = state["report_pdf_path"]
            session.state = "completed"
            db.commit()
    except Exception as exc:
        console.print(f"[yellow]⚠️  Erreur sauvegarde session : {exc}[/yellow]")
    finally:
        db.close()

    state["analysis_complete"] = True

    duration_ms = int((time.time() - start_time) * 1000)
    console.print(f"[green]✅ Rapport généré en {duration_ms}ms[/green]")

    trace = list(state.get("agent_trace", []))
    trace.append(f"ReportAgent: rapport généré en {duration_ms}ms → {filename}")
    state["agent_trace"] = trace

    return state
