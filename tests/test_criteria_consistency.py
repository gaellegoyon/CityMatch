"""
tests/test_criteria_consistency.py
──────────────────────────────────
Vérifie que la whitelist des critères CityMatch reste cohérente avec le
modèle SQLAlchemy db.models.City.

Ce test évite qu'un critère autorisé par les agents pointe vers une colonne
inexistante en base.
"""

from sqlalchemy.inspection import inspect

from agents.common.criteria import VALID_CRITERIA_KEYS
from db.models import City


def test_valid_criteria_keys_are_not_empty() -> None:
    """La whitelist ne doit pas être vide."""
    assert VALID_CRITERIA_KEYS, "La whitelist des critères ne doit pas être vide."


def test_valid_criteria_keys_exist_on_city_model() -> None:
    """Tous les critères autorisés doivent correspondre à une colonne de City."""
    city_columns = {column.key for column in inspect(City).columns}

    missing_columns = sorted(VALID_CRITERIA_KEYS - city_columns)

    assert not missing_columns, (
        "Certains critères autorisés n'existent pas dans db.models.City : "
        f"{missing_columns}"
    )