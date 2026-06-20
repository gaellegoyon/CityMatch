"""
agents/reporting/core.py
───────────────────────
Agent de génération de rapports CityMatch.

Produit :
- un rapport Markdown ;
- un rapport PDF propre avec ReportLab ;
- une analyse lisible des points forts et points de vigilance.
"""

from __future__ import annotations

import html
import io
import logging
import math
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config.settings import AVAILABLE_CRITERIA, MAX_CITIES_IN_REPORT, REPORTS_DIR
from db.models import AgentLog, SearchSession, SessionLocal
from graph.state import CityMatchState


logger = logging.getLogger(__name__)

REPORT_AGENT_NAME = "ReportAgent"
REPORT_AGENT_ACTION = "generate_report"
MAX_AGENT_TRACE_ENTRIES = 200


def _append_agent_trace(state: CityMatchState, message: str) -> None:
    """Ajoute une trace courte dans l'état LangGraph."""
    trace = list(state.get("agent_trace") or [])
    trace.append(message)
    state["agent_trace"] = trace[-MAX_AGENT_TRACE_ENTRIES:]


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

    if not math.isfinite(value):
        return "N/A"

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

    if not math.isfinite(number):
        return "N/A"

    if decimals == 0:
        return f"{number:,.0f}{suffix}"

    return f"{number:,.{decimals}f}{suffix}"


def _safe_score(value: Any) -> float | None:
    """Convertit un score normalisé nullable."""
    try:
        if value is None:
            return None

        score = float(value)
        return score if math.isfinite(score) else None
    except (TypeError, ValueError):
        return None


def _raw_float(value: Any) -> float | None:
    """Convertit une valeur brute nullable."""
    try:
        if value is None:
            return None

        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _escape_reportlab(text: Any) -> str:
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
    """Supprime les emojis/icônes qui rendent parfois des carrés dans ReportLab."""
    replacements = {
        "🏙️": "",
        "📋": "",
        "🏆": "",
        "📊": "",
        "🔍": "",
        "📚": "",
        "⚠️": "",
        "⚠": "",
        "✅": "",
        "⭐": "*",
        "🌿": "",
        "🎯": "",
        "🏠": "",
        "🏙": "",
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

    titled = normalized.title()

    replacements = {
        "Lès": "lès",
        " De ": " de ",
        " Du ": " du ",
        " Des ": " des ",
        " D'": " d'",
        " L'": " l'",
    }

    for old, new in replacements.items():
        titled = titled.replace(old, new)

    return titled[:1].upper() + titled[1:]


def _criteria_keys(user_criteria: dict[str, Any]) -> set[str]:
    """Retourne les clés de critères utilisateur."""
    criteres = user_criteria.get("criteres", {})
    return set(criteres.keys()) if isinstance(criteres, dict) else set()


def _wants_criterion(user_criteria: dict[str, Any], criterion_key: str) -> bool:
    """Indique si un critère a été explicitement retenu pour le scoring."""
    return criterion_key in _criteria_keys(user_criteria)


def _combined_user_text(user_criteria: dict[str, Any]) -> str:
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


def _get_user_notes(user_criteria: dict[str, Any]) -> list[str]:
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


def _detect_requested_regions(user_criteria: dict[str, Any]) -> set[str]:
    """
    Détecte les régions explicitement demandées.

    On évite de transformer une ville de référence en région demandée.
    Exemple : "proche de Paris" n'est pas forcément une demande "Île-de-France".
    """
    requested_from_rules = user_criteria.get("regions_demandees") or []
    requested = {str(region) for region in requested_from_rules if region}

    text = _combined_user_text(user_criteria)

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
        "Île-de-France": ["île-de-france", "ile-de-france", "ile de france"],
        "Grand Est": ["grand est"],
        "Hauts-de-France": ["hauts-de-france", "hauts de france"],
        "Centre-Val de Loire": ["centre-val de loire", "centre val de loire"],
        "Bourgogne-Franche-Comté": ["bourgogne", "franche-comté", "franche comté"],
        "Corse": ["corse"],
    }

    for region, aliases in region_aliases.items():
        if any(alias in text for alias in aliases):
            requested.add(region)

    if "sud" in text:
        requested.add("Provence-Alpes-Côte d'Azur")
        requested.add("Occitanie")

    return requested


def _build_region_coverage_note(top_cities: list[dict[str, Any]], user_criteria: dict[str, Any]) -> str:
    """Explique si une région explicitement demandée n'apparaît pas dans le classement affiché."""
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

    if top_regions.intersection(southern_regions):
        missing = [region for region in missing if region not in southern_regions]

    if not missing:
        return ""

    if len(missing) == 1:
        subject = f"La région {missing[0]}"
        verb = "a"
    else:
        subject = "Les régions " + ", ".join(missing)
        verb = "ont"

    return (
        f"\n\nNote : {subject} {verb} bien été prise en compte, mais n'apparaît pas "
        "dans le classement affiché car d'autres villes obtiennent de meilleurs scores "
        "sur vos critères prioritaires."
    )


def _detect_unhandled_requested_criteria(user_criteria: dict[str, Any]) -> list[str]:
    """
    Détecte les besoins exprimés par l'utilisateur mais non intégrés directement
    au score.
    """
    text = _combined_user_text(user_criteria)
    active = _criteria_keys(user_criteria)

    indirect = {
        str(item).strip().lower()
        for item in user_criteria.get("criteres_indirects", [])
        if item
    }

    unavailable = {
        str(item).strip().lower()
        for item in user_criteria.get("criteres_non_disponibles", [])
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
            "terms": ["transport", "transports", "tram", "metro", "métro", "bus", "gare", "train", "ter", "tgv"],
            "accepted_keys": {"transport_score"},
            "reason": "aucun critère transport fiable n'a été retenu pour cette analyse",
        },
        {
            "label": "commerces de proximité",
            "indirect_labels": {"commerces", "commerces de proximité"},
            "terms": ["commerce", "commerces", "centre-ville", "centre ville", "services de proximité", "marché", "marche"],
            "accepted_keys": {"supermarches_pour_1000", "score_restauration"},
            "reason": "ce besoin n'a pas été transformé en critère pondéré dans cette analyse",
        },
        {
            "label": "culture / loisirs",
            "indirect_labels": {"culture", "loisirs"},
            "terms": ["culture", "loisirs", "cinéma", "cinema", "théâtre", "theatre", "musée", "musee", "sport", "sports"],
            "accepted_keys": {"culture_score", "sports_loisirs_score", "equipements_loisirs_pour_1000"},
            "reason": "ce besoin n'a pas été transformé en critère pondéré dans cette analyse",
        },
        {
            "label": "hôpital / accès hospitalier",
            "indirect_labels": {"hôpital", "hopital", "accès hospitalier"},
            "terms": ["hopital", "hôpital", "clinique", "urgences", "chu"],
            "accepted_keys": {"medecins_pour_1000", "medecins_specialistes_pour_1000", "nb_pharmacies_pour_1000"},
            "reason": "l'analyse utilise les équipements médicaux disponibles mais pas un accès hospitalier détaillé",
        },
        {
            "label": "calme",
            "indirect_labels": {"calme"},
            "terms": ["calme", "tranquille", "paisible", "silencieux"],
            "accepted_keys": {"score_securite", "criminalite_pour_1000"},
            "reason": "le calme est seulement approximé par la sécurité et la taille de ville",
        },
        {
            "label": "fibre",
            "indirect_labels": {"fibre", "internet", "télétravail", "teletravail"},
            "terms": ["fibre", "internet", "télétravail", "teletravail", "remote"],
            "accepted_keys": {"fibre_pct"},
            "reason": "aucun critère fibre n'a été retenu dans le score final",
        },
    ]

    notes: list[str] = []

    for check in checks:
        label = str(check["label"]).lower()

        if not any(term in text for term in check["terms"]):
            continue

        if active.intersection(check["accepted_keys"]):
            continue

        if indirect.intersection(check.get("indirect_labels", set())):
            continue

        if label in unavailable or unavailable.intersection(check.get("indirect_labels", set())):
            continue

        notes.append(
            f"Le besoin « {check['label']} » a été compris, mais n'est pas utilisé directement "
            f"dans le score final : {check['reason']}."
        )

    notes.extend(_get_user_notes(user_criteria))

    unique: list[str] = []
    seen: set[str] = set()

    for note in notes:
        normalized = note.strip()
        if normalized and normalized not in seen:
            unique.append(normalized)
            seen.add(normalized)

    return unique


def _build_unhandled_criteria_note(user_criteria: dict[str, Any]) -> str:
    """Construit le bloc de notes sur les critères non pris en compte directement."""
    notes = _detect_unhandled_requested_criteria(user_criteria)
    if not notes:
        return ""

    lines = "\n".join(f"- {note}" for note in notes)
    return f"\n\nNotes méthodologiques sur les critères :\n{lines}"


def _score_suffix_for_display(criterion_key: str, normalized_score: Any) -> str:
    """Suffixe affiché après une valeur brute dans l'analyse."""
    score = _safe_score(normalized_score)

    if criterion_key == "qualite_air_score":
        return ""

    if score is None:
        return ""

    return f" (score {score:.1f}/10)"


def _is_real_vigilance(criterion_key: str, raw_value: Any, normalized_score: Any) -> bool:
    """Détermine si un critère doit vraiment apparaître en point de vigilance."""
    score = _safe_score(normalized_score)
    raw = _raw_float(raw_value)

    if score is None:
        return False

    if criterion_key == "qualite_air_score":
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


def _get_reference_city_name(user_criteria: dict[str, Any]) -> str | None:
    """Récupère le nom d'une ville de référence si disponible."""
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


def _get_reference_distance(city: dict[str, Any]) -> tuple[str | None, float | None]:
    """Récupère une distance à une ville de référence si elle est présente."""
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
                candidate = float(value)
                if math.isfinite(candidate):
                    distance = candidate
                    break
        except (TypeError, ValueError):
            continue

    return name, distance


def _should_show_sea_distance(user_criteria: dict[str, Any]) -> bool:
    """Affiche la distance à la mer seulement si elle est pertinente."""
    if _wants_criterion(user_criteria, "distance_mer_km"):
        return True

    text = _combined_user_text(user_criteria)
    return any(word in text for word in ["mer", "littoral", "océan", "ocean", "côte", "cote", "bord de mer"])


def _should_show_mountain_distance(user_criteria: dict[str, Any]) -> bool:
    """Affiche la distance à la montagne seulement si elle est pertinente."""
    if _wants_criterion(user_criteria, "distance_montagne_km"):
        return True

    text = _combined_user_text(user_criteria)
    return any(word in text for word in ["montagne", "alpes", "pyrénées", "pyrenees"])


def _append_reference_distance_if_available(
    details: list[str],
    best: dict[str, Any],
    user_criteria: dict[str, Any],
) -> None:
    """Ajoute la distance à la ville de référence si disponible."""
    reference_name_from_city, distance = _get_reference_distance(best)
    reference_name = _smart_title_place(reference_name_from_city or _get_reference_city_name(user_criteria))

    if reference_name and distance is not None:
        details.append(f"à **{distance:.0f} km de {reference_name}**")


# ─────────────────────────────────────────────────────────────────────────────
# Sections Markdown
# ─────────────────────────────────────────────────────────────────────────────
def build_resume_executif(
    top_cities: list[dict[str, Any]],
    user_criteria: dict[str, Any],
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

    details_best: list[str] = []

    _append_reference_distance_if_available(details_best, best, user_criteria)

    if _should_show_sea_distance(user_criteria) and best.get("distance_mer_km") is not None:
        details_best.append(f"à **{float(best['distance_mer_km']):.0f} km de la mer**")

    if _should_show_mountain_distance(user_criteria) and best.get("distance_montagne_km") is not None:
        details_best.append(f"à **{float(best['distance_montagne_km']):.0f} km de la montagne**")

    if best.get("prix_immo_m2") is not None:
        details_best.append(f"prix immobilier moyen **{float(best['prix_immo_m2']):,.0f} €/m²**")

    if (
        best.get("ecoles_pour_1000_enfants") is not None
        and _wants_criterion(user_criteria, "ecoles_pour_1000_enfants")
    ):
        details_best.append(f"écoles primaires **{float(best['ecoles_pour_1000_enfants']):.2f} ‰**")

    if best.get("creches_pour_1000") is not None and _wants_criterion(user_criteria, "creches_pour_1000"):
        details_best.append(f"crèches **{float(best['creches_pour_1000']):.2f} ‰**")

    if best.get("taux_chomage") is not None and _wants_criterion(user_criteria, "taux_chomage"):
        details_best.append(f"taux de chômage **{float(best['taux_chomage']):.1f}%**")

    if best.get("score_securite") is not None and _wants_criterion(user_criteria, "score_securite"):
        details_best.append(f"score sécurité **{float(best['score_securite']):.1f}/10**")

    if best.get("qualite_air_score") is not None and _wants_criterion(user_criteria, "qualite_air_score"):
        details_best.append(f"qualité de l'air **{float(best['qualite_air_score']):.1f}/10**")

    details_str = " — ".join(details_best)
    region_coverage_note = _build_region_coverage_note(top_cities, user_criteria)
    unhandled_criteria_note = _build_unhandled_criteria_note(user_criteria)

    best_name = best.get("nom", "?")
    best_region = best.get("region", "?")
    best_score = _fmt_number(best.get("total_score"), 1)

    details_paragraph = f"\n{details_str}\n" if details_str else "\n"

    return f"""Sur la base de votre profil **{profil}**, {candidate_count} villes candidates ont été analysées,
dont les {display_count} meilleures sont présentées ci-dessous.

Le classement tient compte de vos critères prioritaires : **{criteres_texte}**.

**La ville recommandée en premier choix est {best_name}** ({best_region})
avec un score global de **{best_score}/100**.{details_paragraph}

{pref_texte if pref_texte else ''}{region_coverage_note}{unhandled_criteria_note}

Les premières villes sont les meilleurs compromis trouvés selon vos critères.
Certains filtres peuvent être relâchés progressivement lorsqu'ils sont trop restrictifs.
Consultez l'analyse détaillée ci-dessous pour affiner votre choix."""


def build_tableau_villes(top_cities: list[dict[str, Any]]) -> str:
    """Génère les lignes du tableau Markdown des villes."""
    rows = []

    for city in top_cities:
        rows.append(
            f"| #{city.get('rank', '?')} | **{city.get('nom', '?')}** | {city.get('region', '?')} | "
            f"**{_fmt_number(city.get('total_score'), 1)}/100** | {_fmt_number(city.get('population'), 0)} | "
            f"{_fmt_number(city.get('taux_chomage'), 1, '%')} | "
            f"{_fmt_number(city.get('prix_immo_m2'), 0, ' €/m²')} |"
        )

    return "\n".join(rows)


def build_analyse_ville(city: dict[str, Any]) -> str:
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
    web_section = f"\n**Informations récentes :** {str(web_insights)[:300]}..." if web_insights else ""

    return f"""### #{city.get('rank', '?')} — {city.get('nom', '?')} ({city.get('region', '?')})

> **Score global : {_fmt_number(city.get('total_score'), 1)}/100** | Population : {_fmt_number(city.get('population'), 0)} hab.

**Points forts :**
{forts_text if forts_text else "  - Performances globalement équilibrées"}

**Points de vigilance :**
{faibles_text if faibles_text else "  - Aucun point critique détecté"}
{web_section}

---
"""


def build_tableau_criteres(user_criteria: dict[str, Any]) -> str:
    """Génère le tableau Markdown des critères utilisateur."""
    criteres = user_criteria.get("criteres", {})

    rows = []

    for key, weight in sorted(criteres.items(), key=lambda item: item[1], reverse=True):
        try:
            weight_int = max(1, min(5, int(float(weight))))
        except (TypeError, ValueError):
            weight_int = 3

        rows.append(
            f"| {AVAILABLE_CRITERIA.get(key, {}).get('label', key)} | "
            f"{'⭐' * weight_int} ({weight_int}/5) | "
            f"{AVAILABLE_CRITERIA.get(key, {}).get('description', '?')} |"
        )

    return "\n".join(rows)


def generate_markdown_report(state: CityMatchState) -> str:
    """Génère le rapport complet en Markdown."""
    top_cities = state.get("top_cities", [])[:MAX_CITIES_IN_REPORT]
    scored_cities = state.get("scored_cities") or top_cities
    user_criteria = state.get("user_criteria", {})
    session_id = state.get("session_id", "unknown")

    return MARKDOWN_TEMPLATE.format(
        date=_now_paris().strftime("%d/%m/%Y à %H:%M"),
        session_id=str(session_id)[:8] + "...",
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
def generate_radar_chart(top_cities: list[dict[str, Any]], user_criteria: dict[str, Any]) -> bytes | None:
    """Génère un graphique radar comparant les TOP 5 villes en PNG."""
    try:
        import matplotlib

        matplotlib.use("Agg")

        import matplotlib.pyplot as plt

        criteres = user_criteria.get("criteres", {})
        if not criteres or not top_cities:
            return None

        top_criteres = sorted(criteres.items(), key=lambda item: item[1], reverse=True)[:7]
        labels = [AVAILABLE_CRITERIA.get(key, {}).get("label", key)[:20] for key, _ in top_criteres]

        n_labels = len(labels)
        if n_labels < 3:
            return None

        angles = [index / float(n_labels) * 2 * math.pi for index in range(n_labels)]
        angles += angles[:1]

        colors_list = ["#4299e1", "#48bb78", "#ed8936", "#9f7aea", "#f56565"]

        fig, ax = plt.subplots(figsize=(7, 6), subplot_kw={"projection": "polar"})
        ax.set_facecolor("#f8fafc")
        fig.patch.set_facecolor("white")

        for index, city in enumerate(top_cities[:5]):
            details = city.get("score_details", {})
            values = []

            for key, _ in top_criteres:
                detail = details.get(key, {})
                value = detail.get("normalized_score", 5.0) if detail else 5.0
                values.append(float(value or 5.0))

            values += values[:1]
            color = colors_list[index % len(colors_list)]

            ax.plot(
                angles,
                values,
                "o-",
                linewidth=2,
                color=color,
                label=city.get("nom", f"Ville {index + 1}"),
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

        buffer = io.BytesIO()
        fig.savefig(
            buffer,
            format="png",
            dpi=150,
            bbox_inches="tight",
            facecolor="white",
            edgecolor="none",
        )
        buffer.seek(0)
        png_bytes = buffer.read()
        plt.close(fig)

        return png_bytes

    except ImportError:
        logger.info("matplotlib non disponible : radar ignoré")
        return None
    except Exception:
        logger.exception("Erreur pendant la génération du graphique radar")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# PDF helpers
# ─────────────────────────────────────────────────────────────────────────────
def _pdf_paragraph(text: str, style):
    """Crée un paragraphe ReportLab à partir de texte Markdown inline."""
    from reportlab.platypus import Paragraph

    return Paragraph(_markdown_inline_to_reportlab(_plain_text_for_pdf(text)), style)


def _add_pdf_city_table(story: list[Any], top_cities: list[dict[str, Any]], styles: dict[str, Any]) -> None:
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
                f"{_fmt_number(city.get('total_score'), 1)}/100",
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


def _add_pdf_criteria_table(story: list[Any], user_criteria: dict[str, Any], styles: dict[str, Any]) -> None:
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

        try:
            weight_int = max(1, min(5, int(float(weight))))
        except (TypeError, ValueError):
            weight_int = 3

        data.append(
            [
                Paragraph(_escape_reportlab(meta.get("label", key)), styles["table_cell"]),
                f"{weight_int}/5",
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


def _add_pdf_sources_and_limits(story: list[Any], styles: dict[str, Any]) -> None:
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


def _add_pdf_bullets(story: list[Any], block: str, style) -> None:
    """Ajoute des lignes bullet au PDF en nettoyant Markdown et emojis."""
    if not block.strip():
        story.append(_pdf_paragraph("• Aucun élément à signaler", style))
        return

    lines = [line.strip() for line in block.splitlines() if line.strip()]
    added = False

    for line in lines:
        clean = line.strip()
        clean = re.sub(r"^[-•]\s*", "", clean)
        clean = re.sub(r"^\s*-\s*", "", clean)
        clean = _plain_text_for_pdf(clean).strip()

        title_like = clean.strip("* ").lower()
        if title_like in {
            "points forts",
            "points forts :",
            "points de vigilance",
            "points de vigilance :",
        }:
            continue

        if clean in {"**", "*", ":", ":**"}:
            continue

        if clean:
            story.append(_pdf_paragraph(f"• {clean}", style))
            added = True

    if not added:
        story.append(_pdf_paragraph("• Aucun élément à signaler", style))


# ─────────────────────────────────────────────────────────────────────────────
# PDF
# ─────────────────────────────────────────────────────────────────────────────
def generate_pdf_report(
    markdown_content: str,
    output_path: Path,
    top_cities: list[dict[str, Any]] | None = None,
    user_criteria: dict[str, Any] | None = None,
) -> bool:
    """Convertit le rapport en PDF propre avec ReportLab."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

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
                radar_buffer = io.BytesIO(radar_png)
                story.append(RLImage(radar_buffer, width=12 * cm, height=10 * cm))
                story.append(
                    _pdf_paragraph(
                        "Axes = critères pondérés par l'utilisateur. "
                        "Valeurs normalisées 0–10 (10 = optimal).",
                        styles["body"],
                    )
                )
                story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0")))

        story.append(_pdf_paragraph("Résumé Exécutif", styles["h1"]))

        resume_match = re.search(
            r"##\s+.*?Résumé Exécutif\s+(.*?)\s+---\s+##\s+.*?Classement",
            markdown_content,
            flags=re.DOTALL,
        )
        resume_text = resume_match.group(1).strip() if resume_match else "Rapport généré selon les critères utilisateur."

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
                    f"Score global : {_fmt_number(city.get('total_score'), 1)}/100 | "
                    f"Population : {_fmt_number(city.get('population'), 0)} hab.",
                    styles["body"],
                )
            )

            analysis_markdown = build_analyse_ville(city)
            strengths = _extract_markdown_block(analysis_markdown, "Points forts :", "Points de vigilance :")
            warnings = _extract_markdown_block(analysis_markdown, "Points de vigilance :", "---")

            story.append(_pdf_paragraph("Points forts :", styles["body"]))
            _add_pdf_bullets(story, strengths, styles["body"])

            story.append(_pdf_paragraph("Points de vigilance :", styles["body"]))
            _add_pdf_bullets(story, warnings, styles["body"])

        story.append(_pdf_paragraph("Critères Utilisés", styles["h1"]))
        _add_pdf_criteria_table(story, user_criteria, styles)

        _add_pdf_sources_and_limits(story, styles)

        doc.build(story)
        return True

    except Exception:
        logger.exception("Erreur pendant la génération du PDF")

        markdown_fallback_path = output_path.with_suffix(".md")
        with open(markdown_fallback_path, "w", encoding="utf-8") as handle:
            handle.write(markdown_content)

        return False


def _safe_report_filename(session_id: str, timestamp: str) -> str:
    """Construit un nom de fichier sûr pour le rapport."""
    session_short = re.sub(r"[^a-zA-Z0-9_-]", "_", str(session_id))[:8] or "unknown"
    return f"citymatch_report_{session_short}_{timestamp}"


def _save_search_session_report(
    db,
    session_id: str,
    top_cities: list[dict[str, Any]],
    report_path: str,
) -> None:
    """Met à jour la session métier avec le rapport généré."""
    session = db.query(SearchSession).filter_by(id=session_id).first()

    if not session:
        return

    session.top_cities = [
        {
            "nom": city.get("nom"),
            "score": city.get("total_score"),
            "rank": city.get("rank"),
        }
        for city in top_cities
    ]
    session.report_path = report_path
    session.state = "completed"


def run_report_agent(state: CityMatchState) -> CityMatchState:
    """Nœud LangGraph : génère le rapport Markdown/PDF."""
    start_time = time.perf_counter()

    session_id = state.get("session_id", "unknown")
    top_cities = state.get("top_cities", []) or []

    db = SessionLocal()

    log_entry = AgentLog(
        session_id=session_id,
        agent_name=REPORT_AGENT_NAME,
        action=REPORT_AGENT_ACTION,
        input_data={
            "top_cities_count": len(top_cities),
        },
        success=False,
    )

    try:
        if not top_cities:
            state["analysis_complete"] = True
            state["report_markdown"] = ""
            state["report_pdf_path"] = ""

            duration_ms = int((time.perf_counter() - start_time) * 1000)
            log_entry.output_data = {"report_generated": False, "reason": "no_top_cities"}
            log_entry.duration_ms = duration_ms
            log_entry.success = True

            _append_agent_trace(state, f"{REPORT_AGENT_NAME}: aucune ville top pour le rapport")
            return state

        markdown_content = generate_markdown_report(state)
        state["report_markdown"] = markdown_content

        timestamp = _now_paris().strftime("%Y%m%d_%H%M%S")
        filename = _safe_report_filename(str(session_id), timestamp)

        reports_dir = Path(REPORTS_DIR)
        reports_dir.mkdir(parents=True, exist_ok=True)

        markdown_path = reports_dir / f"{filename}.md"
        with open(markdown_path, "w", encoding="utf-8") as handle:
            handle.write(markdown_content)

        pdf_path = reports_dir / f"{filename}.pdf"
        pdf_success = generate_pdf_report(
            markdown_content=markdown_content,
            output_path=pdf_path,
            top_cities=top_cities,
            user_criteria=state.get("user_criteria", {}),
        )

        report_path = str(pdf_path if pdf_success else markdown_path)
        state["report_pdf_path"] = report_path

        _save_search_session_report(
            db=db,
            session_id=session_id,
            top_cities=top_cities,
            report_path=report_path,
        )

        state["analysis_complete"] = True

        duration_ms = int((time.perf_counter() - start_time) * 1000)

        log_entry.output_data = {
            "report_generated": True,
            "markdown_path": str(markdown_path),
            "pdf_path": str(pdf_path) if pdf_success else None,
            "final_report_path": report_path,
            "pdf_success": pdf_success,
        }
        log_entry.duration_ms = duration_ms
        log_entry.success = True

        _append_agent_trace(
            state,
            f"{REPORT_AGENT_NAME}: rapport généré en {duration_ms} ms → {filename}",
        )

    except Exception as exc:
        db.rollback()

        duration_ms = int((time.perf_counter() - start_time) * 1000)

        logger.exception("Erreur pendant l'exécution du ReportAgent")

        state["error"] = f"{REPORT_AGENT_NAME}: {exc}"
        state["analysis_complete"] = True

        log_entry.duration_ms = duration_ms
        log_entry.success = False
        log_entry.error_message = str(exc)

        _append_agent_trace(
            state,
            f"{REPORT_AGENT_NAME}: erreur après {duration_ms} ms",
        )

    finally:
        try:
            db.add(log_entry)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Impossible d'enregistrer le log du ReportAgent")
        finally:
            db.close()

    return state