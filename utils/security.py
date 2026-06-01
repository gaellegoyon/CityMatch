"""
utils/security.py
─────────────────
Fonctions simples de sécurité et validation pour CityMatch.

Objectifs :
- nettoyer l'entrée utilisateur avant de l'envoyer au LLM ;
- détecter les tentatives évidentes de prompt injection ;
- valider un JSON de critères avec la whitelist centralisée du projet.

Important :
ce module ne remplace pas les règles métier des agents.
Il ajoute seulement une couche de garde-fou légère et maintenable.
"""

from __future__ import annotations

import logging
import re
from typing import Optional


logger = logging.getLogger(__name__)


# ─── Patterns de prompt injection / jailbreak évidents ────────────────────────
# La détection doit rester prudente : on bloque les formulations clairement
# malveillantes, mais on évite de rejeter des phrases normales d'utilisateur.
INJECTION_PATTERNS = [
    # Remplacement / oubli d'instructions
    r"ignore\s+(tes|les|your|all|previous)\s+instructions",
    r"oublie\s+(tes|toutes\s+tes)\s+instructions",
    r"forget\s+(your|all|previous)\s+instructions",
    r"new\s+instructions?\s*:",
    r"system\s*:\s*",
    r"developer\s*:\s*",
    r"assistant\s*:\s*",
    r"\[\s*system\s*\]",
    r"<\s*system\s*>",

    # Jailbreaks / changement de rôle
    r"tu\s+es\s+(maintenant|désormais)\s+(un|une)\s+assistant\s+sans\s+restriction",
    r"you\s+are\s+now\s+a\s+.*without\s+restrictions",
    r"act\s+as\s+(a|an)\s+\w+\s+without\s+restrictions",
    r"jailbreak",
    r"\bDAN\s*mode\b",

    # Manipulation de contexte
    r"ignore\s+le\s+contexte",
    r"ignore\s+previous\s+context",
    r"previous\s+conversation",

    # Exfiltration de secrets
    r"affiche\s+(tous\s+les\s+)?mots?\s+de\s+passe",
    r"montre\s+(moi\s+)?(ta|les)\s+cl[eé]s?\s+api",
    r"reveal\s+(your\s+)?(api\s+)?key",
    r"print\s+(all\s+)?database",
    r"dump\s+(the\s+)?database",
]


VALID_PROFILES = {"famille", "actif", "senior", "couple", "etudiant", "étudiant", "autre"}


def _get_valid_criteria_keys() -> set[str]:
    """
    Retourne la whitelist centralisée des critères.

    Priorité :
    1. agents.common.criteria.VALID_CRITERIA_KEYS si le refacto agents est installé ;
    2. config.settings.AVAILABLE_CRITERIA en fallback.

    Cela évite d'avoir une ancienne whitelist locale qui se désynchronise.
    """
    try:
        from agents.common.criteria import VALID_CRITERIA_KEYS

        return set(VALID_CRITERIA_KEYS)
    except Exception:
        try:
            from config.settings import AVAILABLE_CRITERIA

            return set(AVAILABLE_CRITERIA.keys())
        except Exception:
            return set()


def detect_prompt_injection(text: str) -> tuple[bool, Optional[str]]:
    """
    Détecte des tentatives évidentes de prompt injection.

    Returns:
        (True, pattern) si un pattern est trouvé, sinon (False, None).
    """
    if not isinstance(text, str) or not text:
        return False, None

    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            logger.warning(
                "Prompt injection détectée : pattern=%r extrait=%r",
                pattern,
                text[:120],
            )
            return True, pattern

    return False, None


def sanitize_user_input(text: str, max_length: int = 2000) -> str:
    """
    Nettoie l'entrée utilisateur.

    - convertit les non-str en chaîne vide ;
    - tronque à max_length ;
    - supprime les caractères de contrôle ;
    - retire les balises/rôles système explicites.
    """
    if not isinstance(text, str):
        return ""

    text = text[:max_length]

    # Supprime caractères de contrôle, sauf tabulation et retours ligne.
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

    # Retire les balises de rôles qui peuvent polluer le prompt.
    text = re.sub(
        r"<\s*(system|developer|assistant|human|user)\s*>",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\[\s*(SYSTEM|DEVELOPER|ASSISTANT|HUMAN|USER|INST)\s*\]",
        "",
        text,
        flags=re.IGNORECASE,
    )

    return text.strip()


def validate_criteria_json(profile: dict) -> tuple[bool, list[str]]:
    """
    Valide un profil JSON extrait par le LLM.

    Vérifie :
    - structure dict ;
    - critères uniquement dans la whitelist centralisée ;
    - poids numériques entre 1 et 5 ;
    - profil connu si fourni ;
    - bornes de population cohérentes ;
    - budget et surface cohérents si fournis.

    Ne modifie pas le profil.
    """
    errors: list[str] = []

    if not isinstance(profile, dict):
        return False, ["Le profil doit être un dictionnaire"]

    criteria = profile.get("criteres", {})
    if not isinstance(criteria, dict):
        return False, ["Le champ 'criteres' doit être un dictionnaire"]

    valid_keys = _get_valid_criteria_keys()

    for key, value in criteria.items():
        if valid_keys and key not in valid_keys:
            errors.append(f"Critère invalide : '{key}'")
            continue

        if not isinstance(value, (int, float)) or not (1 <= float(value) <= 5):
            errors.append(f"Poids invalide pour '{key}' : {value} (attendu : 1 à 5)")

    profil = profile.get("profil")
    if profil and str(profil).lower() not in VALID_PROFILES:
        errors.append(f"Profil invalide : '{profil}'")

    _validate_population_bounds(profile, errors)
    _validate_budget_fields(profile, errors)

    return len(errors) == 0, errors


def filter_valid_criteria(profile: dict) -> dict:
    """
    Retourne une copie du profil avec uniquement les critères valides.

    Utile si tu préfères ne pas bloquer tout le profil quand le LLM invente
    un seul critère.
    """
    if not isinstance(profile, dict):
        return {}

    valid_keys = _get_valid_criteria_keys()
    cleaned = dict(profile)
    criteria = cleaned.get("criteres", {})

    if isinstance(criteria, dict):
        cleaned["criteres"] = {
            key: value
            for key, value in criteria.items()
            if not valid_keys or key in valid_keys
        }
    else:
        cleaned["criteres"] = {}

    return cleaned


def validate_and_sanitize(user_input: str) -> tuple[str, bool, str]:
    """
    Pipeline complet pour une entrée utilisateur.

    Returns:
        (texte_nettoyé, is_safe, message_warning)
    """
    is_injection, _ = detect_prompt_injection(user_input)
    if is_injection:
        return (
            "",
            False,
            "⚠️ Votre message contient des éléments non autorisés. "
            "Veuillez reformuler votre demande de manière normale.",
        )

    sanitized = sanitize_user_input(user_input)

    if len(sanitized.strip()) < 3:
        return sanitized, False, "Message trop court, veuillez préciser votre demande."

    return sanitized, True, ""


def safe_filename(filename: str, default: str = "file") -> str:
    """
    Nettoie un nom de fichier pour éviter path traversal et caractères dangereux.

    Exemple :
        '../../secret.txt' → 'secret.txt'
    """
    if not isinstance(filename, str) or not filename.strip():
        return default

    name = filename.replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^a-zA-Z0-9._-]+", "_", name).strip("._-")

    return name or default


def _validate_population_bounds(profile: dict, errors: list[str]) -> None:
    """Valide population_min / population_max."""
    pop_min = profile.get("population_min")
    pop_max = profile.get("population_max")

    try:
        if pop_min is not None:
            pop_min = int(pop_min)
        if pop_max is not None:
            pop_max = int(pop_max)
    except Exception:
        errors.append("Contraintes de population invalides")
        return

    if pop_min is not None and pop_min < 0:
        errors.append("population_min ne peut pas être négative")

    if pop_max is not None and pop_max < 0:
        errors.append("population_max ne peut pas être négative")

    if pop_min is not None and pop_max is not None and pop_max < pop_min:
        errors.append("population_max doit être supérieure ou égale à population_min")


def _validate_budget_fields(profile: dict, errors: list[str]) -> None:
    """Valide budget_immobilier / surface_min_m2 si présents."""
    budget = profile.get("budget_immobilier")
    surface = profile.get("surface_min_m2")

    if budget is not None:
        try:
            if float(budget) <= 0:
                errors.append("budget_immobilier doit être positif")
        except Exception:
            errors.append("budget_immobilier invalide")

    if surface is not None:
        try:
            if float(surface) <= 0:
                errors.append("surface_min_m2 doit être positive")
        except Exception:
            errors.append("surface_min_m2 invalide")
