"""
data/ingest/cli.py

Parsing des arguments CLI de l'ingestion.
"""

from __future__ import annotations

import argparse


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingestion des données réelles CityMatch.")
    parser.add_argument("--test", action="store_true", help="Traite seulement quelques villes.")
    parser.add_argument("--source", default="all", choices=["all", "bpe", "crime"], help="Sous-ensemble de sources à charger.")
    parser.add_argument("--workers", type=int, default=4, help="Nombre de threads de traitement.")
    parser.add_argument("--no-api", action="store_true", help="Mode sans appels API externes expérimentaux.")
    return parser.parse_args()
