"""
data/ingest_real_data.py
─────────────────────────
Point d'entrée de l'ingestion CityMatch.

Ce fichier orchestre les sources, mais la logique métier est rangée dans :
    data/ingest/sources/*.py
    data/ingest/pipeline.py
    data/ingest/index.py

Usage :
    python data/ingest_real_data.py
    python data/ingest_real_data.py --workers 8
    python data/ingest_real_data.py --test
"""

from __future__ import annotations

import sys
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from rich.progress import track

# Permet d'exécuter ce fichier directement depuis la racine du projet.
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.models import City, SessionLocal, init_db
from data.ingest.cli import parse_args
from data.ingest.index import load_communes_index
from data.ingest.pipeline import apply_postprocess_fallbacks, build_commune_data
from data.ingest.sources.arcep import load_arcep_fibre
from data.ingest.sources.atmo import load_atmo_air_quality
from data.ingest.sources.bpe import load_bpe_2024
from data.ingest.sources.crime import load_criminalite
from data.ingest.sources.insee import load_dossier_complet
from data.ingest.utils import console

warnings.filterwarnings(
    "ignore",
    message="Workbook contains no default style, apply openpyxl's default",
    category=UserWarning,
    module="openpyxl",
)
warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")


def load_sources(source: str) -> dict:
    """Charge toutes les sources partagées avant le traitement des communes."""
    return {
        "bpe_df": load_bpe_2024() if source in ("all", "bpe") else pd.DataFrame(),
        "crime_df": load_criminalite() if source in ("all", "crime") else pd.DataFrame(),
        "communes_df": load_dossier_complet(),
        "arcep_df": load_arcep_fibre(),
        "atmo_df": load_atmo_air_quality(),
    }


def save_results(results: list[tuple]) -> None:
    """Écrit les villes en base SQLite, séquentiellement pour éviter les conflits."""
    db = SessionLocal()
    inserted, updated = 0, 0

    try:
        for code, nom, data, err in results:
            if err:
                console.print(f"[yellow]⚠️  {nom} : {err}[/yellow]")
                continue

            try:
                existing = db.query(City).filter_by(code_insee=code).first()
                if existing:
                    for key, value in data.items():
                        if hasattr(City, key) and value is not None:
                            setattr(existing, key, value)
                    existing.last_updated = datetime.now(timezone.utc)
                    updated += 1
                else:
                    valid = {key: value for key, value in data.items() if hasattr(City, key)}
                    db.add(City(**valid))
                    inserted += 1
            except Exception as exc:
                console.print(f"[yellow]⚠️  DB {nom} : {exc}[/yellow]")

        db.commit()
        print_summary(db, inserted, updated)

    finally:
        db.close()


def print_summary(db, inserted: int, updated: int) -> None:
    """Affiche un résumé court de la qualité des données ingérées."""
    console.print("\n[bold green]🎉 Ingestion terminée ![/bold green]")
    console.print(f"  Nouvelles villes : {inserted}")
    console.print(f"  Mises à jour     : {updated}")
    console.print(f"  Total en base    : {db.query(City).count()}")

    cities = db.query(City).all()
    total = len(cities)
    if total == 0:
        return

    checks = [
        ("Revenu médian", "revenu_median"),
        ("Âge médian", "age_median"),
        ("+65 ans", "pct_plus_65_ans"),
        ("Entreprises", "nb_entreprises"),
        ("BPE équipements", "nb_creches"),
        ("Criminalité", "criminalite_pour_1000"),
        ("Prix immobilier", "prix_immo_m2"),
        ("Distance mer", "distance_mer_km"),
        ("Fibre ARCEP", "fibre_pct"),
        ("Qualité air", "qualite_air_score"),
    ]

    console.print(f"\n[dim]Qualité des données ({total} villes) :[/dim]")
    for label, attr in checks:
        count = sum(1 for city in cities if getattr(city, attr, None) is not None)
        console.print(f"[dim]  {label:<16}: {count}/{total}[/dim]")


def main(test_mode: bool = False, source: str = "all", workers: int = 4, no_api: bool = False) -> None:
    console.print("[bold magenta]═══ CityMatch — Ingestion Données Réelles, version 01/06/2026 ═══[/bold magenta]\n")
    init_db()

    console.print("[bold]📦 Chargement des sources de données...[/bold]")
    sources = load_sources(source)

    all_communes = load_communes_index()
    communes = all_communes[:5] if test_mode else all_communes

    console.print(f"\n[bold]🏙️  Traitement de {len(communes)} communes...[/bold]\n")
    console.print("[dim]Sources actives : INSEE, BPE, SSMSI, DVF, ARCEP, ATMO, climat, géographie.[/dim]")

    est_sec = len(communes) * (2 if no_api else 5)
    est_min = max(1, est_sec // 60)
    console.print(f"[dim]⏱  Estimation : ~{est_min} min ({workers} threads)[/dim]\n")

    def process_commune(commune: tuple) -> tuple:
        code, nom, dept, region, lat, lon = commune
        try:
            data = build_commune_data(
                code,
                nom,
                dept,
                region,
                lat,
                lon,
                sources["bpe_df"],
                sources["crime_df"],
                sources["arcep_df"],
                sources["communes_df"],
                atmo_df=sources["atmo_df"],
                no_api=no_api,
            )
            return code, nom, data, None
        except Exception as exc:
            return code, nom, None, str(exc)

    results = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(process_commune, commune): commune for commune in communes}
        for future in track(as_completed(futures), total=len(communes), description="Traitement communes..."):
            results.append(future.result())

    results = apply_postprocess_fallbacks(results)
    save_results(results)


if __name__ == "__main__":
    args = parse_args()
    main(test_mode=args.test, source=args.source, workers=args.workers, no_api=args.no_api)
