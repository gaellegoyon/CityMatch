"""
agents/database_agent.py
─────────────────────────
Agent d'accès à la base SQLite.

Ce fichier orchestre uniquement la requête. La construction des filtres SQL
et les enrichissements de résultats sont dans agents/database/.
"""

import time
from sqlalchemy import text
from db.models import SessionLocal, City, AgentLog
from graph.state import CityMatchState
from rich.console import Console
from agents.common.criteria import VALID_CRITERIA_KEYS, filter_valid_criteria
from agents.database.filters import build_sql_filter
from agents.database.enrichers import (
    apply_reference_city_filter,
    apply_budget_surface_estimate,
    add_population_category,
)

console = Console()


def run_database_agent(state: CityMatchState) -> CityMatchState:
    """
    Nœud LangGraph : Agent de requête base de données.

    Flux :
    1. Lit les critères validés depuis l'état
    2. Construit une requête SQL dynamique de pré-filtrage
    3. Charge toutes les villes candidates avec leurs indicateurs
    4. Met à jour l'état avec les données brutes des villes
    """
    start_time = time.time()
    console.print("\n[bold cyan]🗄️  DatabaseAgent activé[/bold cyan]")

    user_criteria = state.get("user_criteria", {})
    if not user_criteria:
        console.print("[yellow]⚠️  Aucun critère utilisateur. Chargement de toutes les villes.[/yellow]")
        user_criteria = {}

    db = SessionLocal()
    log_entry = AgentLog(
        session_id=state.get("session_id", "unknown"),
        agent_name="DatabaseAgent",
        action="query_cities",
        input_data=user_criteria,
    )

    try:
        # ── Nettoyage des critères invalides avant filtrage ───────────────────
        criteres_raw = user_criteria.get("criteres", {})
        criteres_clean = {k: v for k, v in criteres_raw.items() if k in VALID_CRITERIA_KEYS}
        if len(criteres_clean) < len(criteres_raw):
            ignored = set(criteres_raw.keys()) - VALID_CRITERIA_KEYS
            console.print(f"[yellow]⚠️  Critères LLM ignorés (hors whitelist) : {ignored}[/yellow]")
        user_criteria_clean = {**user_criteria, "criteres": criteres_clean}

        # ── Construction du filtre SQL ─────────────────────────────────────────
        where_clause, params = build_sql_filter(user_criteria_clean, user_criteria_clean)

        # ── Requête principale ─────────────────────────────────────────────────
        query = db.query(City).filter(text(where_clause).bindparams(**params))
        cities = query.all()

        console.print(f"[green]✅ {len(cities)} villes récupérées depuis la BDD[/green]")

        # ── Fallback progressif si trop peu de résultats ─────────────────────
        TARGET_MIN = 10  # minimum de villes pour un scoring pertinent
        if len(cities) < TARGET_MIN:
            console.print(f"[yellow]⚠️  {len(cities)} villes — relâchement progressif des filtres...[/yellow]")

            criteres_fb = user_criteria_clean.get("criteres", {})
            regions = user_criteria_clean.get("regions_preferees", [])
            pop_min_orig = user_criteria_clean.get("population_min", 10000)
            pop_max_orig = user_criteria_clean.get("population_max", 500000)

            # Niveaux de relâchement successifs
            fallback_levels = [
                # Niveau 1 : élargir distance mer à 50km, garder régions + pop
                {"max_mer": 50.0, "pop_min": pop_min_orig, "keep_regions": True},
                # Niveau 2 : élargir distance mer à 80km, pop_min réduit à 10k
                {"max_mer": 80.0, "pop_min": 10000, "keep_regions": True},
                # Niveau 3 : supprimer filtre régional, distance mer à 80km
                {"max_mer": 80.0, "pop_min": 10000, "keep_regions": False},
            ]

            for level_idx, level in enumerate(fallback_levels):
                fb_conditions = []
                fb_params = {}

                fb_conditions.append("population >= :pop_min AND population <= :pop_max")
                fb_params["pop_min"] = level["pop_min"]
                fb_params["pop_max"] = pop_max_orig

                if criteres_fb.get("distance_mer_km", 0) >= 3:
                    fb_conditions.append("distance_mer_km IS NOT NULL AND distance_mer_km <= :max_mer")
                    fb_params["max_mer"] = level["max_mer"]

                if level["keep_regions"] and regions:
                    placeholders = ", ".join(f":fb_region_{i}" for i in range(len(regions)))
                    fb_conditions.append(f"region IN ({placeholders})")
                    for i, r in enumerate(regions): fb_params[f"fb_region_{i}"] = r

                fb_where = " AND ".join(fb_conditions) if fb_conditions else "1=1"
                fb_cities = db.query(City).filter(text(fb_where).bindparams(**fb_params)).all()
                console.print(f"[yellow]→ Niveau {level_idx+1} : {len(fb_cities)} villes[/yellow]")

                if len(fb_cities) >= TARGET_MIN:
                    cities = fb_cities
                    break
            else:
                # Dernier recours : toutes les villes sans filtre géo
                if len(cities) < 5:
                    cities = db.query(City).filter(
                        text("population >= :p").bindparams(p=10000)
                    ).limit(50).all()
                    console.print(f"[yellow]→ Dernier recours : {len(cities)} villes sans filtre géo[/yellow]")

        # ── Sérialisation — conserver les NULL, ne pas les remplacer par 0 ────
        city_dicts = []
        for city in cities:
            d = city.to_dict()
            # NE PAS remplacer None par 0.0 — le scoring utilise la médiane pour les NULL
            city_dicts.append(d)

        # Filtre exact de proximité à une ville de référence.
        # Ajoute distance_reference_km aux résultats sans créer de colonne SQL.
        city_dicts = apply_reference_city_filter(city_dicts, user_criteria_clean)

        # Budget immobilier : ajoute surface_estimable_m2 aux résultats.
        city_dicts = apply_budget_surface_estimate(city_dicts, user_criteria_clean)

        # Taille de ville lisible côté UI/rapport.
        city_dicts = add_population_category(city_dicts)

        state["raw_city_data"] = city_dicts

        # ── Log ────────────────────────────────────────────────────────────────
        duration_ms = int((time.time() - start_time) * 1000)
        log_entry.output_data = {"cities_count": len(city_dicts)}
        log_entry.duration_ms = duration_ms
        log_entry.success = True

        trace = state.get("agent_trace", [])
        trace.append(f"DatabaseAgent: {len(city_dicts)} villes chargées en {duration_ms}ms")
        state["agent_trace"] = trace

    except Exception as e:
        console.print(f"[red]❌ Erreur DatabaseAgent : {e}[/red]")
        state["error"] = str(e)
        state["raw_city_data"] = []
        log_entry.success = False
        log_entry.error_message = str(e)

    finally:
        db.add(log_entry)
        db.commit()
        db.close()

    return state



def get_city_details_by_name(city_name: str) -> dict | None:
    """
    Utilitaire : retourne les détails complets d'une ville par son nom.
    Utilisé par l'interface Streamlit pour l'affichage de détails.
    """
    db = SessionLocal()
    try:
        city = db.query(City).filter(
            City.nom.ilike(f"%{city_name}%")
        ).first()
        return city.to_dict() if city else None
    finally:
        db.close()


def get_all_regions() -> list[str]:
    """Retourne la liste unique de toutes les régions disponibles."""
    db = SessionLocal()
    try:
        regions = db.query(City.region).distinct().all()
        return sorted([r[0] for r in regions if r[0]])
    finally:
        db.close()


def get_stats_summary() -> dict:
    """Retourne des statistiques globales sur la base de données."""
    db = SessionLocal()
    try:
        total = db.query(City).count()
        regions = db.query(City.region).distinct().count()
        return {
            "total_cities": total,
            "total_regions": regions,
            "last_updated": "2026",
        }
    finally:
        db.close()
