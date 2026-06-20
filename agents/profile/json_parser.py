"""
agents/profile/json_parser.py
────────────────────────────
Extraction robuste d'un objet JSON depuis une réponse LLM.
"""

from __future__ import annotations

import json
import re
from typing import Any


_CODE_BLOCK_RE = re.compile(
    r"```(?:json)?\s*(.*?)\s*```",
    flags=re.DOTALL | re.IGNORECASE,
)


def _try_load_json(candidate: str) -> dict[str, Any] | None:
    """Parse un candidat JSON et retourne un dict contenant 'criteres' si valide."""
    try:
        data = json.loads(candidate.strip())
    except json.JSONDecodeError:
        return None

    if isinstance(data, dict) and isinstance(data.get("criteres"), dict):
        return data

    return None


def _extract_balanced_json_objects(text: str) -> list[str]:
    """
    Extrait les objets JSON équilibrés présents dans un texte.

    Cette approche est plus robuste qu'une regex pour les objets imbriqués.
    """
    candidates: list[str] = []
    start_index: int | None = None
    depth = 0
    in_string = False
    escaped = False

    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue

        if char == "\\" and in_string:
            escaped = True
            continue

        if char == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if char == "{":
            if depth == 0:
                start_index = index
            depth += 1

        elif char == "}":
            if depth == 0:
                continue

            depth -= 1

            if depth == 0 and start_index is not None:
                candidates.append(text[start_index : index + 1])
                start_index = None

    return candidates


def extract_criteria_from_response(response_text: str | None) -> dict[str, Any] | None:
    """
    Extrait le JSON de profil depuis une réponse LLM.

    Retourne None si aucun objet JSON valide contenant une clé ``criteres``
    n'est trouvé.
    """
    if not response_text:
        return None

    text = str(response_text).strip()

    direct_json = _try_load_json(text)
    if direct_json is not None:
        return direct_json

    for match in _CODE_BLOCK_RE.findall(text):
        parsed = _try_load_json(match)
        if parsed is not None:
            return parsed

    for candidate in _extract_balanced_json_objects(text):
        parsed = _try_load_json(candidate)
        if parsed is not None:
            return parsed

    return None