"""
data/ingest_real_data.py
────────────────────────
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
from typing import Any

import pandas as pd
from rich.progress import track
from sqlalchemy import func

# Permet d'exécuter ce fichier directement depuis la racine du projet.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data.ingest.cli import parse_args  # noqa: E402
from data.ingest.index import load_communes_index  # noqa: E402
from data.ingest.pipeline import apply_postprocess_fallbacks, build_commune_data  # noqa: E402
from data.ingest.sources.arcep import load_arcep_fibre  # noqa: E402
from data.ingest.sources.atmo import load_atmo_air_quality  # noqa: E402
from data.ingest.sources.bpe import load_bpe_2024  # noqa: E402
from data.ingest.sources.crime import load_criminalite  # noqa: E402
from data.ingest.sources.insee import load_dossier_complet  # noqa: E402
from data.ingest.utils import console  # noqa: E402
from db.models import City, SessionLocal, init_db  # noqa: E402


warnings.filterwarnings(
    "ignore",
    message="Workbook contains no default style, apply openpyxl's default",
    category=UserWarning,
    module="openpyxl",
)
warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")


SourceFrames = dict[str, pd.DataFrame]
CommuneTuple = tuple[str, str, str, str, float, float]
IngestResult = tuple[str, str, dict[str, Any] | None, str | None]

DEFAULT_TEST_LIMIT = 5
MIN_WORKERS = 1
MAX_WORKERS = 32


def _empty_dataframe() -> pd.DataFrame:
    """Retourne un DataFrame vide explicite."""
    return pd.DataFrame()


def _should_load(source: str, source_name: str) -> bool:
    """
    Indique si une source optionnelle doit être chargée.

    La source INSEE principale reste chargée dans tous les cas, car elle sert
    de socle à l'enrichissement communal.
    """
    return source == "all" or source == source_name


def load_sources(source: str) -> SourceFrames:
    """
    Charge les sources partagées avant le traitement des communes.

    INSEE est toujours chargé, car build_commune_data dépend des données
    communales de base. Les autres sources sont chargées selon l'option CLI.
    """
    sources: SourceFrames = {
        "bpe_df": _empty_dataframe(),
        "crime_df": _empty_dataframe(),
        "communes_df": _empty_dataframe(),
        "arcep_df": _empty_dataframe(),
        "atmo_df": _empty_dataframe(),
    }

    sources["communes_df"] = load_dossier_complet()

    if _should_load(source, "bpe"):
        sources["bpe_df"] = load_bpe_2024()

    if _should_load(source, "crime"):
        sources["crime_df"] = load_criminalite()

    if _should_load(source, "arcep"):
        sources["arcep_df"] = load_arcep_fibre()

    if _should_load(source, "air") or _should_load(source, "atmo"):
        sources["atmo_df"] = load_atmo_air_quality()

    # En mode "all", toutes les sources ci-dessus sont chargées.
    if source == "all":
        if sources["arcep_df"].empty:
            sources["arcep_df"] = load_arcep_fibre()
        if sources["atmo_df"].empty:
            sources["atmo_df"] = load_atmo_air_quality()

    return sources


def process_commune(
    commune: CommuneTuple,
    sources: SourceFrames,
    no_api: bool = False,
) -> IngestResult:
    """Construit les données enrichies d'une commune."""
    code, nom, departement, region, latitude, longitude = commune

    try:
        data = build_commune_data(
            code,
            nom,
            departement,
            region,
            latitude,
            longitude,
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


def _city_payload_for_model(data: dict[str, Any]) -> dict[str, Any]:
    """Filtre les données pour ne garder que les colonnes connues du modèle City."""
    return {
        key: value
        for key, value in data.items()
        if hasattr(City, key)
    }


def _update_city(existing: City, data: dict[str, Any]) -> None:
    """Met à jour une ville existante sans écraser une valeur par None."""
    for key, value in data.items():
        if hasattr(City, key) and value is not None:
            setattr(existing, key, value)

    existing.last_updated = datetime.now(timezone.utc)


def save_results(results: list[IngestResult]) -> None:
    """
    Écrit les villes en base SQLite.

    L'écriture est volontairement séquentielle pour éviter les conflits SQLite.
    """
    db = SessionLocal()
    inserted = 0
    updated = 0
    skipped = 0
    failed = 0

    try:
        for code, nom, data, error in results:
            if error:
                skipped += 1
                console.print(f"[yellow]⚠️  {nom} : {error}[/yellow]")
                continue

            if not data:
                skipped += 1
                console.print(f"[yellow]⚠️  {nom} : aucune donnée à sauvegarder[/yellow]")
                continue

            try:
                existing = db.query(City).filter_by(code_insee=code).first()

                if existing:
                    _update_city(existing, data)
                    updated += 1
                else:
                    valid_payload = _city_payload_for_model(data)

                    if not valid_payload.get("code_insee"):
                        valid_payload["code_insee"] = code

                    if not valid_payload.get("nom"):
                        valid_payload["nom"] = nom

                    db.add(City(**valid_payload))
                    inserted += 1

            except Exception as exc:
                failed += 1
                db.rollback()
                console.print(f"[yellow]⚠️  DB {nom} : {exc}[/yellow]")

        db.commit()
        print_summary(
            db=db,
            inserted=inserted,
            updated=updated,
            skipped=skipped,
            failed=failed,
        )

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def _count_not_null(db, attr: str) -> int:
    """Compte les villes dont une colonne est renseignée."""
    column = getattr(City, attr, None)

    if column is None:
        return 0

    return db.query(func.count(City.id)).filter(column.isnot(None)).scalar() or 0


def print_summary(
    db,
    inserted: int,
    updated: int,
    skipped: int = 0,
    failed: int = 0,
) -> None:
    """Affiche un résumé court de la qualité des données ingérées."""
    total = db.query(func.count(City.id)).scalar() or 0

    console.print("\n[bold green]🎉 Ingestion terminée ![/bold green]")
    console.print(f"  Nouvelles villes : {inserted}")
    console.print(f"  Mises à jour     : {updated}")
    console.print(f"  Ignorées         : {skipped}")
    console.print(f"  Erreurs DB       : {failed}")
    console.print(f"  Total en base    : {total}")

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
        count = _count_not_null(db, attr)
        console.print(f"[dim]  {label:<16}: {count}/{total}[/dim]")


def _select_communes(
    all_communes: list[CommuneTuple],
    test_mode: bool,
) -> list[CommuneTuple]:
    """Sélectionne les communes à traiter selon le mode CLI."""
    if test_mode:
        return all_communes[:DEFAULT_TEST_LIMIT]

    return all_communes


def _normalize_workers(workers: int) -> int:
    """Borne le nombre de workers pour éviter les valeurs dangereuses."""
    try:
        workers = int(workers)
    except (TypeError, ValueError):
        return 4

    return max(MIN_WORKERS, min(MAX_WORKERS, workers))


def _estimate_duration_minutes(communes_count: int, workers: int, no_api: bool) -> int:
    """Estime grossièrement la durée d'ingestion."""
    seconds_per_city = 2 if no_api else 5
    estimated_seconds = max(1, communes_count * seconds_per_city // max(1, workers))
    return max(1, estimated_seconds // 60)


def main(
    test_mode: bool = False,
    source: str = "all",
    workers: int = 4,
    no_api: bool = False,
) -> None:
    """Point d'entrée principal de l'ingestion."""
    workers = _normalize_workers(workers)

    console.print(
        "[bold magenta]═══ CityMatch — Ingestion Données Réelles ═══[/bold magenta]\n"
    )

    init_db()

    console.print("[bold]📦 Chargement des sources de données...[/bold]")
    sources = load_sources(source)

    all_communes = load_communes_index()
    communes = _select_communes(
        all_communes=all_communes,
        test_mode=test_mode,
    )

    console.print(f"\n[bold]🏙️  Traitement de {len(communes)} communes...[/bold]\n")
    console.print(
        "[dim]Sources possibles : INSEE, BPE, SSMSI, DVF, ARCEP, ATMO, climat, géographie.[/dim]"
    )
    console.print(f"[dim]Source demandée : {source}[/dim]")

    estimated_minutes = _estimate_duration_minutes(
        communes_count=len(communes),
        workers=workers,
        no_api=no_api,
    )

    console.print(f"[dim]⏱  Estimation : ~{estimated_minutes} min ({workers} threads)[/dim]\n")

    results: list[IngestResult] = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                process_commune,
                commune,
                sources,
                no_api,
            ): commune
            for commune in communes
        }

        for future in track(
            as_completed(futures),
            total=len(communes),
            description="Traitement communes...",
        ):
            try:
                results.append(future.result())
            except Exception as exc:
                commune = futures[future]
                code, nom, *_ = commune
                results.append((code, nom, None, str(exc)))

    results = apply_postprocess_fallbacks(results)
    save_results(results)


if __name__ == "__main__":
    args = parse_args()
    main(
        test_mode=args.test,
        source=args.source,
        workers=args.workers,
        no_api=args.no_api,
    )