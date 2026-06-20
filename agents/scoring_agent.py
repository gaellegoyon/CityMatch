"""
agents/scoring_agent.py
───────────────────────
Agent de scoring multi-critères pondéré.

Calcule un score global sur 100 pour chaque ville candidate à partir des
critères pondérés de l'utilisateur.

Algorithme :
1. validation des critères issus du LLM ;
2. normalisation min-max de chaque indicateur sur [0, 10] ;
3. inversion des critères où une valeur basse est préférable ;
4. imputation neutre des valeurs manquantes ;
5. moyenne pondérée ramenée sur 100 ;
6. classement et persistance des meilleurs scores en base.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import pandas as pd

from agents.common.criteria import filter_valid_criteria
from config.settings import AVAILABLE_CRITERIA, MAX_CITIES_IN_REPORT
from db.models import AgentLog, CityScore, SessionLocal
from graph.state import CityMatchState
from utils.serialization import to_python


logger = logging.getLogger(__name__)

SCORING_AGENT_NAME = "ScoringAgent"
SCORING_AGENT_ACTION = "score_cities"
DEFAULT_WEIGHT = 3
MIN_WEIGHT = 1
MAX_WEIGHT = 5
MAX_AGENT_TRACE_ENTRIES = 200


def _append_agent_trace(state: CityMatchState, message: str) -> None:
    """Ajoute une trace courte dans l'état LangGraph."""
    trace = list(state.get("agent_trace") or [])
    trace.append(message)
    state["agent_trace"] = trace[-MAX_AGENT_TRACE_ENTRIES:]


def _coerce_weight(value: Any) -> int | None:
    """Convertit un poids LLM en entier borné entre 1 et 5."""
    try:
        weight = int(float(value))
    except (TypeError, ValueError):
        return None

    if weight <= 0:
        return None

    return max(MIN_WEIGHT, min(MAX_WEIGHT, weight))


def _sanitize_weights(raw_weights: dict[str, Any] | None) -> tuple[dict[str, int], set[str]]:
    """Valide les critères et convertit les poids utilisateur."""
    valid_criteria, ignored_criteria = filter_valid_criteria(raw_weights or {})

    weights: dict[str, int] = {}

    for criterion, raw_weight in valid_criteria.items():
        if criterion not in AVAILABLE_CRITERIA:
            ignored_criteria.add(criterion)
            continue

        weight = _coerce_weight(raw_weight)

        if weight is not None:
            weights[criterion] = weight

    return weights, ignored_criteria


def _default_weights_for_dataframe(df: pd.DataFrame) -> dict[str, int]:
    """Construit des poids égaux pour les critères disponibles dans les données."""
    return {
        criterion: DEFAULT_WEIGHT
        for criterion in AVAILABLE_CRITERIA
        if criterion in df.columns
    }


def normalize_series(series: pd.Series, lower_is_better: bool = False) -> pd.Series:
    """
    Normalise une série numérique sur [0, 10].

    Si lower_is_better=True, le score est inversé pour conserver la règle :
    plus le score est élevé, meilleure est la ville.
    """
    numeric_series = pd.to_numeric(series, errors="coerce")

    min_value = numeric_series.min()
    max_value = numeric_series.max()

    if pd.isna(min_value) or pd.isna(max_value) or max_value == min_value:
        return pd.Series([5.0] * len(numeric_series), index=numeric_series.index)

    normalized = (numeric_series - min_value) / (max_value - min_value) * 10

    if lower_is_better:
        normalized = 10 - normalized

    return normalized.clip(lower=0, upper=10)


def _build_normalized_dataframe(
    df: pd.DataFrame,
    weights: dict[str, int],
) -> pd.DataFrame:
    """Construit le DataFrame des scores normalisés et des indicateurs de présence."""
    normalized_df = pd.DataFrame(index=df.index)

    for criterion in weights:
        if criterion not in df.columns:
            logger.warning("Critère absent des données et ignoré : %s", criterion)
            continue

        raw_values = pd.to_numeric(df[criterion], errors="coerce")
        has_data = raw_values.notna()

        median_value = raw_values.median()
        fill_value = median_value if not pd.isna(median_value) else 5.0
        filled_values = raw_values.fillna(fill_value)

        lower_is_better = AVAILABLE_CRITERIA.get(criterion, {}).get(
            "lower_is_better",
            False,
        )

        normalized_df[criterion] = normalize_series(
            filled_values,
            lower_is_better=lower_is_better,
        )
        normalized_df[f"{criterion}__has_data"] = has_data

    return normalized_df


def compute_weighted_score(
    row: pd.Series,
    weights: dict[str, int],
    normalized_df: pd.DataFrame,
) -> dict[str, Any]:
    """Calcule le score pondéré d'une ville et le détail par critère."""
    total_weight = sum(weights.values())

    if total_weight <= 0:
        return {"total_score": 0.0, "score_details": {}}

    weighted_sum = 0.0
    details: dict[str, Any] = {}

    for criterion, weight in weights.items():
        if criterion not in normalized_df.columns:
            continue

        score_10 = float(normalized_df.loc[row.name, criterion])
        has_data = bool(
            normalized_df.get(
                f"{criterion}__has_data",
                pd.Series(True, index=normalized_df.index),
            ).loc[row.name]
        )

        contribution = score_10 * weight
        weighted_sum += contribution

        criterion_config = AVAILABLE_CRITERIA.get(criterion, {})

        details[criterion] = {
            "raw_value": to_python(row.get(criterion)),
            "normalized_score": round(score_10, 2) if has_data else None,
            "weight": weight,
            "contribution": round(contribution, 2),
            "unit": criterion_config.get("unit", ""),
            "label": criterion_config.get("label", criterion),
            "has_data": has_data,
        }

    total_score = (weighted_sum / (total_weight * 10)) * 100

    return {
        "total_score": round(total_score, 2),
        "score_details": details,
    }


def _score_city_rows(
    city_data: list[dict[str, Any]],
    raw_weights: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Score et classe les villes candidates."""
    df = pd.DataFrame(city_data)

    weights, ignored_criteria = _sanitize_weights(raw_weights)

    if not weights:
        weights = _default_weights_for_dataframe(df)

    valid_weights = {
        criterion: weight
        for criterion, weight in weights.items()
        if criterion in df.columns
    }

    if not valid_weights:
        scored_rows = []

        for index, row in df.iterrows():
            city_scored = to_python(row.to_dict())
            city_scored["total_score"] = 0.0
            city_scored["score_details"] = {}
            city_scored["rank"] = index + 1
            scored_rows.append(city_scored)

        return scored_rows, ignored_criteria

    normalized_df = _build_normalized_dataframe(df, valid_weights)

    scored_rows: list[dict[str, Any]] = []

    for _, row in df.iterrows():
        score_data = compute_weighted_score(row, valid_weights, normalized_df)

        city_scored = to_python(row.to_dict())
        city_scored["total_score"] = float(score_data["total_score"])
        city_scored["score_details"] = to_python(score_data["score_details"])

        scored_rows.append(city_scored)

    scored_rows.sort(key=lambda city: city["total_score"], reverse=True)

    for rank, city in enumerate(scored_rows, start=1):
        city["rank"] = rank

    return scored_rows, ignored_criteria


def _save_scores_to_db(
    db,
    session_id: str,
    scored_rows: list[dict[str, Any]],
) -> None:
    """Sauvegarde les meilleurs scores dans la table city_scores."""
    db.query(CityScore).filter_by(session_id=session_id).delete()

    for city in scored_rows[:MAX_CITIES_IN_REPORT]:
        city_id = city.get("id")

        if city_id is None:
            continue

        score_entry = CityScore(
            city_id=city_id,
            session_id=session_id,
            total_score=city["total_score"],
            rank=city["rank"],
            score_details=to_python(city.get("score_details", {})),
        )

        db.add(score_entry)


def run_scoring_agent(state: CityMatchState) -> CityMatchState:
    """
    Nœud LangGraph : agent de scoring multi-critères.

    Lit les villes candidates depuis l'état, calcule les scores, sauvegarde les
    meilleurs résultats et met à jour ``scored_cities`` et ``top_cities``.
    """
    start_time = time.perf_counter()

    city_data = state.get("enriched_city_data") or state.get("raw_city_data") or []
    user_criteria = dict(state.get("user_criteria") or {})
    raw_weights = user_criteria.get("criteres", {})
    session_id = state.get("session_id", "unknown")

    db = SessionLocal()

    log_entry = AgentLog(
        session_id=session_id,
        agent_name=SCORING_AGENT_NAME,
        action=SCORING_AGENT_ACTION,
        input_data=to_python(
            {
                "cities_count": len(city_data),
                "criteria": raw_weights,
            }
        ),
        success=False,
    )

    try:
        if not city_data:
            state["scored_cities"] = []
            state["top_cities"] = []

            duration_ms = int((time.perf_counter() - start_time) * 1000)

            log_entry.output_data = {"scored_count": 0}
            log_entry.duration_ms = duration_ms
            log_entry.success = True

            _append_agent_trace(
                state,
                f"{SCORING_AGENT_NAME}: aucune ville à scorer",
            )

            return state

        scored_rows, ignored_criteria = _score_city_rows(
            city_data=city_data,
            raw_weights=raw_weights,
        )

        top_cities = scored_rows[:MAX_CITIES_IN_REPORT]

        state["scored_cities"] = scored_rows
        state["top_cities"] = top_cities

        _save_scores_to_db(
            db=db,
            session_id=session_id,
            scored_rows=scored_rows,
        )

        duration_ms = int((time.perf_counter() - start_time) * 1000)

        log_entry.output_data = to_python(
            {
                "scored_count": len(scored_rows),
                "top_count": len(top_cities),
                "ignored_criteria": sorted(ignored_criteria),
                "best_city": top_cities[0].get("nom") if top_cities else None,
                "best_score": top_cities[0].get("total_score") if top_cities else None,
            }
        )
        log_entry.duration_ms = duration_ms
        log_entry.success = True

        _append_agent_trace(
            state,
            f"{SCORING_AGENT_NAME}: {len(scored_rows)} villes scorées en {duration_ms} ms",
        )

    except Exception as exc:
        db.rollback()

        duration_ms = int((time.perf_counter() - start_time) * 1000)

        logger.exception("Erreur pendant l'exécution du ScoringAgent")

        state["error"] = f"{SCORING_AGENT_NAME}: {exc}"
        state["scored_cities"] = []
        state["top_cities"] = []

        log_entry.duration_ms = duration_ms
        log_entry.success = False
        log_entry.error_message = str(exc)

        _append_agent_trace(
            state,
            f"{SCORING_AGENT_NAME}: erreur après {duration_ms} ms",
        )

    finally:
        try:
            db.add(log_entry)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Impossible d'enregistrer les résultats du ScoringAgent")
        finally:
            db.close()

    return state


def get_score_radar_data(city: dict[str, Any]) -> dict[str, Any]:
    """
    Formate les scores par critère pour un graphique radar.

    Utilisé par l'interface Streamlit ou le ReportAgent.
    Les critères sans donnée réelle sont exclus pour éviter un affichage trompeur.
    """
    details = city.get("score_details", {})

    categories: list[str] = []
    scores: list[float] = []

    for criterion, data in details.items():
        if not data.get("has_data", True):
            continue

        normalized_score = data.get("normalized_score")

        if normalized_score is None:
            continue

        label = AVAILABLE_CRITERIA.get(criterion, {}).get("label", criterion)

        categories.append(label)
        scores.append(float(normalized_score))

    return {
        "categories": categories,
        "scores": scores,
        "city_name": city.get("nom", "?"),
    }