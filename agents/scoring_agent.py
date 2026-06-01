"""
agents/scoring_agent.py
────────────────────────
Agent de scoring multi-critères pondéré.
Calcule un score global (0-100) pour chaque ville candidate
en fonction des critères et pondérations définis par l'utilisateur.

Algorithme :
1. Normalisation min-max de chaque indicateur → [0, 10]
2. Inversion si "plus bas = mieux" (ex: taux de chômage)
3. Pondération par les poids utilisateur (1-5)
4. Calcul du score global (moyenne pondérée × 20 → [0-100])
5. Bonus/malus pour les critères éliminatoires
"""

import time
import numpy as np
import pandas as pd
from db.models import SessionLocal, CityScore, AgentLog
from graph.state import CityMatchState
from config.settings import AVAILABLE_CRITERIA, MAX_CITIES_IN_REPORT
from rich.console import Console
from rich.table import Table

console = Console()


from agents.common.serialization import to_python as _to_python

def normalize_series(series: pd.Series, lower_is_better: bool = False) -> pd.Series:
    """
    Normalisation min-max d'une série → [0, 10].
    Si lower_is_better=True, on inverse (10 - valeur normalisée)
    pour que le score soit toujours "plus grand = mieux".
    """
    min_val, max_val = series.min(), series.max()
    if max_val == min_val:
        # Toutes les villes ont la même valeur → score neutre de 5
        return pd.Series([5.0] * len(series), index=series.index)

    normalized = (series - min_val) / (max_val - min_val) * 10
    if lower_is_better:
        normalized = 10 - normalized
    return normalized


def compute_weighted_score(row: pd.Series, weights: dict, normalized_df: pd.DataFrame) -> dict:
    """
    Calcule le score pondéré d'une ville et le détail par critère.

    Args:
        row: ligne du DataFrame pour une ville
        weights: {critere: poids} (1-5)
        normalized_df: DataFrame des scores normalisés

    Returns:
        dict avec total_score et score_details
    """
    total_weight = sum(weights.values())
    if total_weight == 0:
        return {"total_score": 0.0, "score_details": {}}

    weighted_sum = 0.0
    details = {}

    for critere, poids in weights.items():
        if critere in normalized_df.columns:
            score_10 = normalized_df.loc[row.name, critere]
            has_data = normalized_df.get(f"{critere}__has_data", pd.Series([True]*len(normalized_df))).loc[row.name]
            contribution = score_10 * poids
            weighted_sum += contribution
            raw_val = row.get(critere)
            details[critere] = {
                "raw_value": raw_val,
                "normalized_score": round(score_10, 2) if has_data else None,
                "weight": poids,
                "contribution": round(contribution, 2),
                "unit": AVAILABLE_CRITERIA.get(critere, {}).get("unit", ""),
                "label": AVAILABLE_CRITERIA.get(critere, {}).get("label", critere),
                "has_data": bool(has_data),
            }

    # Score global sur 100
    total_score = (weighted_sum / (total_weight * 10)) * 100
    return {
        "total_score": round(total_score, 2),
        "score_details": details,
    }


def run_scoring_agent(state: CityMatchState) -> CityMatchState:
    """
    Nœud LangGraph : Agent de scoring multi-critères.

    Flux :
    1. Charge les données brutes des villes depuis l'état
    2. Normalise chaque indicateur (min-max, 0-10)
    3. Applique les pondérations utilisateur
    4. Trie les villes par score décroissant
    5. Sauvegarde les scores en base
    """
    start_time = time.time()
    console.print("\n[bold cyan]🏆 ScoringAgent activé[/bold cyan]")

    # ── Données d'entrée ──────────────────────────────────────────────────────
    city_data = state.get("enriched_city_data") or state.get("raw_city_data", [])
    user_criteria = state.get("user_criteria", {})
    weights = user_criteria.get("criteres", {})

    if not city_data:
        console.print("[yellow]⚠️  Aucune donnée de ville à scorer.[/yellow]")
        state["scored_cities"] = []
        state["top_cities"] = []
        return state

    if not weights:
        console.print("[yellow]⚠️  Aucun poids défini, utilisation de poids égaux.[/yellow]")
        weights = {k: 3 for k in AVAILABLE_CRITERIA.keys()}

    # ── Conversion en DataFrame ────────────────────────────────────────────────
    df = pd.DataFrame(city_data)
    console.print(f"[dim]Scoring de {len(df)} villes avec {len(weights)} critères...[/dim]")

    # ── Nettoyage : ignorer critères invalides inventés par le LLM ───────────
    # Whitelist exhaustive — tous les critères disponibles dans AVAILABLE_CRITERIA
    from agents.common.criteria import VALID_CRITERIA_KEYS as VALID_KEYS
    weights = {k: v for k, v in weights.items() if k in VALID_KEYS}

    # ── Normalisation par critère ──────────────────────────────────────────────
    normalized_df = pd.DataFrame(index=df.index)

    for critere in weights.keys():
        if critere not in df.columns:
            console.print(f"[yellow]⚠️  Critère '{critere}' absent des données, ignoré.[/yellow]")
            continue

        col_data = pd.to_numeric(df[critere], errors='coerce')
        # Ne pas remplacer les NULL par 0 — une ville sans données ne doit pas
        # être pénalisée. On normalise uniquement sur les villes qui ont la donnée,
        # les autres reçoivent la médiane (score neutre) plutôt que 0.
        median_val = col_data.median()
        col_data_filled = col_data.fillna(median_val if not pd.isna(median_val) else 5.0)
        lower_is_better = AVAILABLE_CRITERIA.get(critere, {}).get("lower_is_better", False)
        normalized_df[critere] = normalize_series(col_data_filled, lower_is_better)
        # Marquer les villes sans donnée pour les exclure des points forts/faibles
        normalized_df[f"{critere}__has_data"] = col_data.notna()

    # ── Calcul des scores ──────────────────────────────────────────────────────
    scored_rows = []
    valid_weights = {k: v for k, v in weights.items() if k in normalized_df.columns}

    for idx, row in df.iterrows():
        score_data = compute_weighted_score(row, valid_weights, normalized_df)
        city_scored = _to_python(row.to_dict())
        city_scored["total_score"] = float(score_data["total_score"])
        city_scored["score_details"] = _to_python(score_data["score_details"])
        scored_rows.append(city_scored)

    # ── Tri par score décroissant ─────────────────────────────────────────────
    scored_rows.sort(key=lambda x: x["total_score"], reverse=True)

    # Ajout du rang
    for i, city in enumerate(scored_rows):
        city["rank"] = i + 1

    state["scored_cities"] = scored_rows
    state["top_cities"] = scored_rows[:MAX_CITIES_IN_REPORT]

    # ── Affichage console du TOP 5 ────────────────────────────────────────────
    table = Table(title=f"🏆 TOP {min(5, len(scored_rows))} Villes", show_header=True)
    table.add_column("Rang", style="bold yellow", width=6)
    table.add_column("Ville", style="bold white", width=25)
    table.add_column("Région", style="cyan", width=30)
    table.add_column("Score", style="bold green", width=10)
    table.add_column("Population", style="dim", width=12)

    for city in scored_rows[:5]:
        table.add_row(
            f"#{city['rank']}",
            city.get("nom", "?"),
            city.get("region", "?"),
            f"{city['total_score']:.1f}/100",
            f"{city.get('population', 0):,}",
        )
    console.print(table)

    # ── Persistance en base ────────────────────────────────────────────────────
    _save_scores_to_db(state, scored_rows)

    duration_ms = int((time.time() - start_time) * 1000)
    console.print(f"[green]✅ Scoring terminé en {duration_ms}ms[/green]")

    trace = state.get("agent_trace", [])
    trace.append(f"ScoringAgent: {len(scored_rows)} villes scorées en {duration_ms}ms")
    state["agent_trace"] = trace

    return state


def _save_scores_to_db(state: CityMatchState, scored_rows: list):
    """Sauvegarde les scores calculés dans la table city_scores."""
    db = SessionLocal()
    try:
        session_id = state.get("session_id", "unknown")
        # Supprimer les anciens scores de cette session
        db.query(CityScore).filter_by(session_id=session_id).delete()

        for city in scored_rows[:MAX_CITIES_IN_REPORT]:
            score_entry = CityScore(
                city_id=city.get("id"),
                session_id=session_id,
                total_score=city["total_score"],
                rank=city["rank"],
                score_details=city.get("score_details", {}),
            )
            db.add(score_entry)

        db.commit()
        console.print(f"[dim]Scores sauvegardés en base (session {session_id[:8]}...)[/dim]")
    except Exception as e:
        console.print(f"[yellow]⚠️  Erreur sauvegarde scores : {e}[/yellow]")
    finally:
        db.close()


def get_score_radar_data(city: dict) -> dict:
    """
    Formate les données de score pour affichage en graphique radar.
    Utilisé par l'interface Streamlit et le ReportAgent.
    """
    details = city.get("score_details", {})
    categories = []
    scores = []

    for critere, data in details.items():
        label = AVAILABLE_CRITERIA.get(critere, {}).get("label", critere)
        categories.append(label)
        scores.append(data.get("normalized_score", 0))

    return {"categories": categories, "scores": scores, "city_name": city.get("nom", "?")}