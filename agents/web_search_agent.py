"""
agents/web_search_agent.py
──────────────────────────
Recherche web temps réel avec cache SQLite.

Rôle :
- enrichir les villes candidates avec des informations web récentes ;
- utiliser Tavily si disponible ;
- basculer sur DuckDuckGo si Tavily n'est pas configuré ;
- mettre en cache les résultats 24h pour éviter les requêtes répétées ;
- nettoyer les contenus web avant de les injecter dans l'état LangGraph.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from config.settings import TAVILY_API_KEY
from db.models import AgentLog, SessionLocal, WebSearchCache
from graph.state import CityMatchState
from utils.security import sanitize_untrusted_context
from utils.serialization import to_python


logger = logging.getLogger(__name__)

WEB_SEARCH_AGENT_NAME = "WebSearchAgent"
WEB_SEARCH_AGENT_ACTION = "web_enrichment"
CACHE_TTL_HOURS = 24
DEFAULT_MAX_RESULTS = 5
DEFAULT_CITY_SEARCH_LIMIT = 5
MAX_CONTENT_CHARS = 500
MAX_WEB_EXCERPT_CHARS = 200
MAX_AGENT_TRACE_ENTRIES = 200


def utc_now() -> datetime:
    """Retourne l'heure UTC naïve pour compatibilité SQLite."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _append_agent_trace(state: CityMatchState, message: str) -> None:
    """Ajoute une trace courte dans l'état LangGraph."""
    trace = list(state.get("agent_trace") or [])
    trace.append(message)
    state["agent_trace"] = trace[-MAX_AGENT_TRACE_ENTRIES:]


def _normalize_query(query: str) -> str:
    """Normalise une requête avant hash/cache."""
    return " ".join(str(query or "").strip().split())


def _hash_query(query: str) -> str:
    """Calcule un hash stable pour le cache de recherche."""
    normalized_query = _normalize_query(query).lower()
    return hashlib.sha256(normalized_query.encode("utf-8")).hexdigest()


def _is_placeholder_api_key(api_key: str | None) -> bool:
    """Détecte les valeurs placeholder de clé API."""
    if not api_key:
        return True

    return api_key.strip() in {
        "",
        "your_tavily_api_key_here",
        "changeme",
        "change_me",
        "none",
        "null",
    }


def get_search_client() -> tuple[str | None, Any | None]:
    """
    Retourne le client de recherche disponible.

    Priorité :
    1. Tavily si une clé API réelle est configurée ;
    2. DuckDuckGo sinon.
    """
    if not _is_placeholder_api_key(TAVILY_API_KEY):
        try:
            from tavily import TavilyClient

            return "tavily", TavilyClient(api_key=TAVILY_API_KEY)
        except ImportError:
            logger.warning("tavily-python non installé, bascule sur DuckDuckGo")
        except Exception:
            logger.exception("Impossible d'initialiser Tavily, bascule sur DuckDuckGo")

    try:
        from duckduckgo_search import DDGS

        return "duckduckgo", DDGS()
    except ImportError:
        logger.warning("duckduckgo-search non installé")
        return None, None
    except Exception:
        logger.exception("Impossible d'initialiser DuckDuckGo")
        return None, None


def _clean_url(url: str | None) -> str:
    """Conserve uniquement les URL HTTP/HTTPS valides."""
    if not url:
        return ""

    cleaned_url = str(url).strip()
    parsed = urlparse(cleaned_url)

    if parsed.scheme not in {"http", "https"}:
        return ""

    if not parsed.netloc:
        return ""

    return cleaned_url


def _normalize_search_result(
    title: Any,
    url: Any,
    content: Any,
) -> dict[str, str]:
    """Normalise un résultat web."""
    return {
        "title": str(title or "").strip()[:200],
        "url": _clean_url(str(url or "")),
        "content": str(content or "").strip()[:MAX_CONTENT_CHARS],
    }


def _search_with_tavily(client: Any, query: str, max_results: int) -> list[dict[str, str]]:
    """Effectue une recherche Tavily."""
    response = client.search(
        query=query,
        max_results=max_results,
        search_depth="basic",
    )

    return [
        _normalize_search_result(
            title=result.get("title", ""),
            url=result.get("url", ""),
            content=result.get("content", ""),
        )
        for result in response.get("results", [])
    ]


def _search_with_duckduckgo(client: Any, query: str, max_results: int) -> list[dict[str, str]]:
    """Effectue une recherche DuckDuckGo."""
    raw_results = list(client.text(query, max_results=max_results))

    return [
        _normalize_search_result(
            title=result.get("title", ""),
            url=result.get("href", ""),
            content=result.get("body", ""),
        )
        for result in raw_results
    ]


def _filter_empty_results(results: list[dict[str, str]]) -> list[dict[str, str]]:
    """Supprime les résultats vides ou sans contenu exploitable."""
    filtered: list[dict[str, str]] = []

    for result in results:
        if not result.get("title") and not result.get("content"):
            continue

        filtered.append(result)

    return filtered


def search_web(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> list[dict[str, str]]:
    """Effectue une recherche web avec cache SQLite 24h."""
    normalized_query = _normalize_query(query)

    if not normalized_query:
        return []

    query_hash = _hash_query(normalized_query)
    db = SessionLocal()

    try:
        cached = db.query(WebSearchCache).filter_by(query_hash=query_hash).first()

        if cached and cached.expires_at and cached.expires_at > utc_now():
            return cached.results or []

        engine_name, client = get_search_client()

        if engine_name == "tavily":
            results = _search_with_tavily(
                client=client,
                query=normalized_query,
                max_results=max_results,
            )
        elif engine_name == "duckduckgo":
            results = _search_with_duckduckgo(
                client=client,
                query=normalized_query,
                max_results=max_results,
            )
        else:
            logger.warning("Aucun moteur de recherche disponible")
            return []

        results = _filter_empty_results(results)

        expires_at = utc_now() + timedelta(hours=CACHE_TTL_HOURS)

        if cached:
            cached.query = normalized_query
            cached.results = to_python(results)
            cached.expires_at = expires_at
        else:
            db.add(
                WebSearchCache(
                    query_hash=query_hash,
                    query=normalized_query,
                    results=to_python(results),
                    expires_at=expires_at,
                )
            )

        db.commit()

        return results

    except Exception:
        db.rollback()
        logger.exception("Erreur pendant la recherche web : %s", normalized_query)
        return []

    finally:
        db.close()


def _get_city_name(city: dict[str, Any]) -> str:
    """Récupère le nom exploitable d'une ville."""
    return str(city.get("nom") or city.get("name") or "").strip()


def _select_cities_for_web_search(state: CityMatchState) -> list[dict[str, Any]]:
    """
    Sélectionne les villes à enrichir.

    Compatible avec plusieurs ordres de graphe :
    - après ScoringAgent : top_cities existe ;
    - avant ScoringAgent : raw_city_data existe seulement.
    """
    candidates = (
        state.get("top_cities")
        or state.get("scored_cities")
        or state.get("raw_city_data")
        or []
    )

    selected: list[dict[str, Any]] = []

    for city in candidates:
        if not isinstance(city, dict):
            continue

        if not _get_city_name(city):
            continue

        selected.append(city)

        if len(selected) >= DEFAULT_CITY_SEARCH_LIMIT:
            break

    return selected


def build_search_queries(top_cities: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """
    Associe chaque requête au nom exact de ville.

    Le tuple permet d'éviter le bug :
        "La Rochelle" → "La"
    """
    queries: list[tuple[str, str]] = []
    current_year = utc_now().year

    seen_queries: set[str] = set()

    for city in top_cities[:DEFAULT_CITY_SEARCH_LIMIT]:
        city_name = _get_city_name(city)

        if not city_name:
            continue

        city_queries = [
            f"{city_name} qualité de vie avis habitants {current_year}",
            f"{city_name} économie emploi dynamisme {current_year}",
        ]

        for query in city_queries:
            normalized_query = _normalize_query(query)

            if normalized_query in seen_queries:
                continue

            seen_queries.add(normalized_query)
            queries.append((city_name, normalized_query))

    return queries


def _build_web_excerpt(web_results: list[dict[str, str]], city_name: str) -> str:
    """Construit un extrait web nettoyé pour une ville."""
    web_excerpt = " | ".join(
        result.get("content", "")[:MAX_WEB_EXCERPT_CHARS]
        for result in web_results[:3]
        if result.get("content")
    )

    return sanitize_untrusted_context(
        web_excerpt,
        source_label=city_name or "web",
    )


def _enrich_city_data(
    city_data: list[dict[str, Any]],
    all_results: dict[str, list[dict[str, str]]],
) -> list[dict[str, Any]]:
    """Ajoute web_insights et web_sources aux villes."""
    enriched: list[dict[str, Any]] = []

    for city in city_data:
        if not isinstance(city, dict):
            continue

        city_copy = dict(city)
        city_name = _get_city_name(city)

        web_info = all_results.get(city_name, [])

        city_copy["web_insights"] = _build_web_excerpt(
            web_results=web_info,
            city_name=city_name,
        )
        city_copy["web_sources"] = [
            result.get("url", "")
            for result in web_info[:3]
            if result.get("url")
        ]

        enriched.append(city_copy)

    return enriched


def run_web_search_agent(state: CityMatchState) -> CityMatchState:
    """
    Nœud LangGraph : enrichit les villes avec une recherche web récente.

    Si aucun moteur n'est disponible ou si aucune ville n'est présente,
    l'agent ne bloque pas le pipeline : il recopie les données brutes.
    """
    start_time = time.perf_counter()
    session_id = str(state.get("session_id") or "unknown")

    city_data = state.get("raw_city_data") or []
    cities_to_search = _select_cities_for_web_search(state)

    db = SessionLocal()

    log_entry = AgentLog(
        session_id=session_id,
        agent_name=WEB_SEARCH_AGENT_NAME,
        action=WEB_SEARCH_AGENT_ACTION,
        input_data=to_python(
            {
                "cities_available": len(city_data),
                "cities_selected_for_search": [_get_city_name(city) for city in cities_to_search],
            }
        ),
        success=False,
    )

    try:
        if not city_data:
            state["enriched_city_data"] = []
            state["web_search_results"] = {}

            duration_ms = int((time.perf_counter() - start_time) * 1000)

            log_entry.output_data = {
                "queries_count": 0,
                "reason": "no_city_data",
            }
            log_entry.duration_ms = duration_ms
            log_entry.success = True

            _append_agent_trace(state, f"{WEB_SEARCH_AGENT_NAME}: aucune ville à enrichir")
            return state

        if not cities_to_search:
            state["enriched_city_data"] = [dict(city) for city in city_data if isinstance(city, dict)]
            state["web_search_results"] = {}

            duration_ms = int((time.perf_counter() - start_time) * 1000)

            log_entry.output_data = {
                "queries_count": 0,
                "reason": "no_searchable_city",
            }
            log_entry.duration_ms = duration_ms
            log_entry.success = True

            _append_agent_trace(state, f"{WEB_SEARCH_AGENT_NAME}: aucune ville recherchable")
            return state

        all_results: dict[str, list[dict[str, str]]] = {}
        performed_queries: list[str] = []

        for city_name, query in build_search_queries(cities_to_search):
            results = search_web(query, max_results=3)

            if results:
                all_results.setdefault(city_name, []).extend(results)

            performed_queries.append(query)

        enriched_city_data = _enrich_city_data(
            city_data=city_data,
            all_results=all_results,
        )

        state["enriched_city_data"] = enriched_city_data
        state["web_search_results"] = all_results

        duration_ms = int((time.perf_counter() - start_time) * 1000)

        log_entry.output_data = to_python(
            {
                "queries_count": len(performed_queries),
                "cities_with_results": len(all_results),
                "results_count": sum(len(results) for results in all_results.values()),
                "queries": performed_queries,
            }
        )
        log_entry.duration_ms = duration_ms
        log_entry.success = True

        _append_agent_trace(
            state,
            f"{WEB_SEARCH_AGENT_NAME}: {len(performed_queries)} recherches en {duration_ms} ms",
        )

    except Exception as exc:
        db.rollback()

        duration_ms = int((time.perf_counter() - start_time) * 1000)

        logger.exception("Erreur pendant l'exécution du WebSearchAgent")

        state["error"] = f"{WEB_SEARCH_AGENT_NAME}: {exc}"
        state["enriched_city_data"] = [dict(city) for city in city_data if isinstance(city, dict)]
        state["web_search_results"] = {}

        log_entry.duration_ms = duration_ms
        log_entry.success = False
        log_entry.error_message = str(exc)

        _append_agent_trace(
            state,
            f"{WEB_SEARCH_AGENT_NAME}: erreur après {duration_ms} ms",
        )

    finally:
        try:
            db.add(log_entry)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Impossible d'enregistrer le log du WebSearchAgent")
        finally:
            db.close()

    return state