"""
data/ingest/cli.py
──────────────────
Parsing des arguments CLI de l'ingestion CityMatch.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence


SOURCE_CHOICES = ("all", "bpe", "crime")
DEFAULT_WORKERS = 4


def _positive_int(value: str) -> int:
    """Valide un entier strictement positif pour les options CLI."""
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Valeur entière invalide : {value!r}"
        ) from exc

    if number < 1:
        raise argparse.ArgumentTypeError(
            "Le nombre de workers doit être supérieur ou égal à 1."
        )

    return number


def build_parser() -> argparse.ArgumentParser:
    """Construit le parser CLI de l'ingestion."""
    parser = argparse.ArgumentParser(
        description="Ingestion des données réelles CityMatch."
    )

    parser.add_argument(
        "--test",
        action="store_true",
        help="Traite seulement un petit échantillon de villes.",
    )

    parser.add_argument(
        "--source",
        default="all",
        choices=SOURCE_CHOICES,
        help=(
            "Sous-ensemble de sources à charger. "
            "Valeurs possibles : all, bpe, crime."
        ),
    )

    parser.add_argument(
        "--workers",
        type=_positive_int,
        default=DEFAULT_WORKERS,
        help="Nombre de threads de traitement. Doit être supérieur ou égal à 1.",
    )

    parser.add_argument(
        "--no-api",
        action="store_true",
        help="Désactive les appels API externes expérimentaux.",
    )

    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """
    Parse les arguments CLI.

    Le paramètre argv permet de tester le parsing sans dépendre de sys.argv.
    """
    parser = build_parser()
    return parser.parse_args(argv)