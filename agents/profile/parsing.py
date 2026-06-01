"""Parsing du JSON renvoyé par le LLM."""

import json
import re
from typing import Optional


def extract_criteria_from_response(response_text: str) -> Optional[dict]:
    """
    Tente d'extraire le JSON de profil de la réponse du LLM.
    Retourne None si aucun JSON valide trouvé.
    """
    import re
    # Chercher un bloc JSON dans la réponse
    patterns = [
        r'```json\s*(.*?)\s*```',   # bloc markdown json
        r'```\s*(.*?)\s*```',        # bloc markdown générique
        r'(\{[^{}]*"criteres"[^{}]*\{.*?\}[^{}]*\})',  # JSON inline
    ]
    for pattern in patterns:
        matches = re.findall(pattern, response_text, re.DOTALL)
        for match in matches:
            try:
                data = json.loads(match)
                if "criteres" in data:
                    return data
            except json.JSONDecodeError:
                continue
    return None


