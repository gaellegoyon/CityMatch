"""
agents/web_search_agent.py
───────────────────────────
Recherche web temps réel avec cache SQLite.
"""

import hashlib
import time
from datetime import datetime, timedelta, timezone

from config.settings import TAVILY_API_KEY
from db.models import SessionLocal, WebSearchCache, AgentLog
from graph.state import CityMatchState
from rich.console import Console

console = Console()


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def get_search_client():
    """Retourne Tavily si disponible, sinon DuckDuckGo."""
    if TAVILY_API_KEY and TAVILY_API_KEY != "your_tavily_api_key_here":
        try:
            from tavily import TavilyClient
            return "tavily", TavilyClient(api_key=TAVILY_API_KEY)
        except ImportError:
            console.print("[yellow]⚠️  tavily-python non installé, bascule sur DuckDuckGo[/yellow]")

    try:
        from duckduckgo_search import DDGS
        return "duckduckgo", DDGS()
    except ImportError:
        return None, None


def search_web(query: str, max_results: int = 5) -> list[dict]:
    """Effectue une recherche web avec cache 24h."""
    query_hash = hashlib.sha256(query.encode()).hexdigest()
    db = SessionLocal()
    try:
        cached = db.query(WebSearchCache).filter_by(query_hash=query_hash).first()
        if cached and cached.expires_at and cached.expires_at > utc_now():
            return cached.results or []

        engine_name, client = get_search_client()
        if engine_name == "tavily":
            response = client.search(query=query, max_results=max_results, search_depth="basic")
            results = [
                {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")[:500]}
                for r in response.get("results", [])
            ]
        elif engine_name == "duckduckgo":
            raw = list(client.text(query, max_results=max_results))
            results = [
                {"title": r.get("title", ""), "url": r.get("href", ""), "content": r.get("body", "")[:500]}
                for r in raw
            ]
        else:
            console.print("[yellow]⚠️  Aucun moteur de recherche disponible.[/yellow]")
            return []

        expires_at = utc_now() + timedelta(hours=24)
        if cached:
            cached.results = results
            cached.expires_at = expires_at
        else:
            db.add(WebSearchCache(query_hash=query_hash, query=query, results=results, expires_at=expires_at))
        db.commit()
        return results
    except Exception as exc:
        console.print(f"[yellow]⚠️  Erreur recherche web : {exc}[/yellow]")
        return []
    finally:
        db.close()


def build_search_queries(top_cities: list[dict]) -> list[tuple[str, str]]:
    """Associe chaque requête au nom exact de ville pour éviter le bug 'La Rochelle' → 'La'."""
    queries: list[tuple[str, str]] = []
    for city in top_cities[:5]:
        nom = city.get("nom", "")
        if not nom:
            continue
        queries.append((nom, f"{nom} qualité vie avis habitants 2025"))
        queries.append((nom, f"{nom} économie emploi dynamisme 2025"))
    return queries


def run_web_search_agent(state: CityMatchState) -> CityMatchState:
    start_time = time.time()
    console.print("\n[bold cyan]🔍 WebSearchAgent activé[/bold cyan]")

    top_cities = state.get("top_cities", [])
    if not top_cities:
        state["enriched_city_data"] = state.get("raw_city_data", [])
        return state

    all_results: dict[str, list[dict]] = {}
    performed_queries: list[str] = []

    for city_name, query in build_search_queries(top_cities):
        results = search_web(query, max_results=3)
        if results:
            all_results.setdefault(city_name, []).extend(results)
            performed_queries.append(query)
            console.print(f"[dim]  ✓ {len(results)} résultats pour : {query[:60]}[/dim]")

    enriched = []
    for city in state.get("raw_city_data", []):
        city_copy = city.copy()
        city_name = city.get("nom", "")
        web_info = all_results.get(city_name, [])
        city_copy["web_insights"] = " | ".join(r.get("content", "")[:200] for r in web_info[:3] if r.get("content"))
        city_copy["web_sources"] = [r.get("url", "") for r in web_info[:3]]
        enriched.append(city_copy)

    state["enriched_city_data"] = enriched
    state["web_search_results"] = all_results

    duration_ms = int((time.time() - start_time) * 1000)
    db = SessionLocal()
    try:
        db.add(AgentLog(
            session_id=state.get("session_id", "unknown"),
            agent_name="WebSearchAgent",
            action="web_enrichment",
            input_data={"queries": performed_queries},
            output_data={"queries_count": len(performed_queries)},
            duration_ms=duration_ms,
            success=True,
        ))
        db.commit()
    finally:
        db.close()

    trace = state.get("agent_trace", [])
    trace.append(f"WebSearchAgent: {len(performed_queries)} recherches en {duration_ms}ms")
    state["agent_trace"] = trace
    return state
