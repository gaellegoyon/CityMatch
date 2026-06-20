"""
ui/components/results.py
────────────────────────
Affichage des résultats : carte, graphiques, classement, rapport.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Final

import folium
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from streamlit_folium import st_folium

from config.settings import AVAILABLE_CRITERIA, REPORTS_DIR


FRANCE_CENTER: Final[list[float]] = [46.8, 2.3]
INITIAL_MAP_ZOOM: Final[int] = 5
RESULT_MAP_ZOOM: Final[int] = 6
MAP_HEIGHT_INITIAL: Final[int] = 400
MAP_HEIGHT_RESULTS: Final[int] = 450
MAX_MAP_CITIES: Final[int] = 10
MAX_CHART_CITIES: Final[int] = 10
MAX_RADAR_CITIES: Final[int] = 5

MAP_COLORS: Final[list[str]] = [
    "#22c55e",
    "#3b82f6",
    "#f59e0b",
    "#8b5cf6",
    "#ef4444",
    "#06b6d4",
    "#84cc16",
    "#f97316",
    "#ec4899",
    "#6b7280",
]


def render_results_panel() -> None:
    """Affiche le panneau de droite : résultats ou état initial."""
    top_cities = st.session_state.get("top_cities") or []

    if not top_cities:
        render_initial_state()
        return

    st.markdown("### 🏆 Villes recommandées")

    tab_map, tab_charts, tab_ranking, tab_report = st.tabs(
        ["🗺️ Carte", "📊 Graphiques", "🏙️ Classement", "📄 Rapport"]
    )

    with tab_map:
        render_map(top_cities)

    with tab_charts:
        render_bar_chart(top_cities)
        st.divider()
        render_radar_chart(top_cities)

    with tab_ranking:
        for index, city in enumerate(top_cities):
            render_city_card(city, _to_int(city.get("rank"), index + 1))

    with tab_report:
        render_report_tab()


def render_initial_state() -> None:
    """Affiche la carte vide et l'aide de départ."""
    st.markdown("### 🗺️ Carte de France")

    map_object = folium.Map(
        location=FRANCE_CENTER,
        zoom_start=INITIAL_MAP_ZOOM,
        tiles="CartoDB positron",
    )
    st_folium(
        map_object,
        width=None,
        height=MAP_HEIGHT_INITIAL,
        returned_objects=[],
    )

    st.info(
        "💡 **Comment ça marche ?**\n\n"
        "1. 🗣️ Décrivez votre situation et vos préférences\n"
        "2. 🤖 L'IA structure vos critères et les pondère\n"
        "3. 🗄️ La base filtre les villes compatibles\n"
        "4. 🔍 Les résultats peuvent être enrichis par recherche web\n"
        "5. 🏆 Un score personnalisé classe les villes\n"
        "6. 📄 Un rapport est généré"
    )


def render_city_card(city: dict[str, Any], rank: int) -> None:
    """Affiche une carte synthétique pour une ville recommandée."""
    score = _to_float(city.get("total_score"), 0.0)

    col1, col2, col3 = st.columns([3, 2, 2])

    with col1:
        city_name = str(city.get("nom") or "?")
        st.markdown(f"### #{rank} — {city_name}")

        departement = city.get("departement") or "?"
        region = city.get("region") or "?"
        st.caption(f"📍 {departement} | {region}")

        population = _to_int(city.get("population"), 0)
        st.caption(f"👥 {_format_int(population)} habitants")

        city_size = city.get("taille_ville")
        if city_size:
            st.caption(f"🏙️ {city_size}")

        surface = _to_float(city.get("surface_estimable_m2"))
        if surface is not None:
            st.caption(f"🏠 Surface estimable avec budget : ~{surface:.0f} m²")

        distance_ref = _to_float(city.get("distance_reference_km"))
        if distance_ref is not None:
            st.caption(f"📍 Distance référence : {distance_ref:.0f} km")

    with col2:
        st.metric("Score global", f"{score:.1f}/100")

        unemployment = _to_float(city.get("taux_chomage"))
        st.metric("Chômage", f"{unemployment:.1f}%" if unemployment is not None else "N/A")

        security = _to_float(city.get("score_securite"))
        st.metric("Sécurité", f"{security:.1f}/10" if security is not None else "N/A")

    with col3:
        price = _to_float(city.get("prix_immo_m2"))
        st.metric("Prix m²", f"{_format_float(price, 0)} €" if price is not None else "N/A")

        fibre = _to_float(city.get("fibre_pct"))
        st.metric("Fibre", f"{fibre:.0f}%" if fibre is not None else "N/A")

        air = _to_float(city.get("qualite_air_score"))
        st.metric("Air", f"{air:.1f}/10" if air is not None else "N/A")

    st.divider()


def render_map(cities: list[dict[str, Any]]) -> None:
    """Affiche une carte Folium des villes recommandées."""
    valid_cities = [
        city for city in cities[:MAX_MAP_CITIES]
        if _to_float(city.get("latitude")) is not None
        and _to_float(city.get("longitude")) is not None
    ]

    if not valid_cities:
        st.info("Aucune coordonnée disponible pour afficher la carte.")
        return

    map_object = folium.Map(
        location=FRANCE_CENTER,
        zoom_start=RESULT_MAP_ZOOM,
        tiles="CartoDB positron",
    )

    for index, city in enumerate(valid_cities):
        lat = _to_float(city.get("latitude"))
        lon = _to_float(city.get("longitude"))

        if lat is None or lon is None:
            continue

        rank = _to_int(city.get("rank"), index + 1)
        color = MAP_COLORS[index % len(MAP_COLORS)]
        popup_html = build_city_popup(city, color, rank)
        city_name = str(city.get("nom") or "?")
        score = _to_float(city.get("total_score"), 0.0)

        folium.CircleMarker(
            location=[lat, lon],
            radius=max(6, 20 - rank),
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.7,
            tooltip=f"#{rank} {city_name} — {score:.1f}/100",
            popup=folium.Popup(popup_html, max_width=280),
        ).add_to(map_object)

        folium.Marker(
            location=[lat, lon],
            icon=folium.DivIcon(
                html=(
                    f'<div style="font-size:10px;font-weight:bold;color:white;'
                    f"background:{color};border-radius:50%;width:22px;height:22px;"
                    f'display:flex;align-items:center;justify-content:center;">'
                    f"{rank}</div>"
                ),
                icon_size=(22, 22),
                icon_anchor=(11, 11),
            ),
        ).add_to(map_object)

    st_folium(
        map_object,
        width=None,
        height=MAP_HEIGHT_RESULTS,
        returned_objects=[],
    )


def build_city_popup(city: dict[str, Any], color: str, rank: int) -> str:
    """Construit le HTML de popup pour une ville recommandée."""
    city_name = _html_escape(city.get("nom") or "?")
    score = _to_float(city.get("total_score"), 0.0)

    fields = [
        ("Région", city.get("region")),
        ("Population", _format_int(_to_int(city.get("population"), 0))),
        ("Chômage", _fmt(city.get("taux_chomage"), "{:.1f}%")),
        ("Prix m²", _fmt(city.get("prix_immo_m2"), "{:,.0f} €")),
        ("Mer", _fmt(city.get("distance_mer_km"), "{:.0f} km")),
        ("Montagne", _fmt(city.get("distance_montagne_km"), "{:.0f} km")),
        ("Fibre", _fmt(city.get("fibre_pct"), "{:.0f}%")),
        ("Air", _fmt(city.get("qualite_air_score"), "{:.1f}/10")),
        ("Temp.", _fmt(city.get("temperature_moyenne"), "{:.1f} °C")),
    ]

    rows = "\n".join(
        f"<b>{_html_escape(label)} :</b> {_html_escape(value)}<br>"
        for label, value in fields
        if value not in (None, "", "N/A")
    )

    return f"""
    <div style="font-family: Arial; min-width: 220px;">
        <h4 style="color: {_html_escape(color)};">#{rank} {city_name}</h4>
        <b>Score :</b> {score:.1f}/100<br>
        {rows}
    </div>
    """


def render_bar_chart(cities: list[dict[str, Any]]) -> None:
    """Affiche un bar chart des scores globaux."""
    rows = [
        {
            "Ville": str(city.get("nom") or "?"),
            "Score": _to_float(city.get("total_score"), 0.0),
            "Rang": _to_int(city.get("rank"), index + 1),
        }
        for index, city in enumerate(cities[:MAX_CHART_CITIES])
    ]

    dataframe = pd.DataFrame(rows)

    if dataframe.empty:
        st.info("Aucun score disponible pour générer le graphique.")
        return

    figure = px.bar(
        dataframe,
        x="Score",
        y="Ville",
        orientation="h",
        color="Score",
        color_continuous_scale="viridis",
        title="Scores globaux des villes recommandées",
        labels={"Score": "Score (/100)", "Ville": ""},
    )
    figure.update_layout(
        height=350,
        yaxis={"categoryorder": "total ascending"},
    )
    st.plotly_chart(figure, use_container_width=True)


def render_radar_chart(cities: list[dict[str, Any]]) -> None:
    """Affiche un radar chart comparant les TOP 5."""
    radar_cities = [
        city for city in cities[:MAX_RADAR_CITIES]
        if isinstance(city.get("score_details"), dict)
    ]

    if not radar_cities:
        st.info("Le détail des scores n'est pas disponible pour le radar.")
        return

    figure = go.Figure()
    has_trace = False

    for city in radar_cities:
        details = city.get("score_details") or {}

        categories: list[str] = []
        scores: list[float] = []

        for criterion_key, detail in details.items():
            if not isinstance(detail, dict):
                continue

            normalized_score = _to_float(detail.get("normalized_score"))

            if normalized_score is None:
                continue

            label = AVAILABLE_CRITERIA.get(criterion_key, {}).get("label", criterion_key)
            categories.append(str(label)[:15])
            scores.append(normalized_score)

        if not categories or not scores:
            continue

        figure.add_trace(
            go.Scatterpolar(
                r=scores + [scores[0]],
                theta=categories + [categories[0]],
                fill="toself",
                opacity=0.6,
                name=str(city.get("nom") or "?"),
            )
        )
        has_trace = True

    if not has_trace:
        st.info("Le détail des scores n'est pas suffisant pour générer le radar.")
        return

    figure.update_layout(
        polar={"radialaxis": {"visible": True, "range": [0, 10]}},
        showlegend=True,
        title="Comparaison des TOP 5 villes",
        height=450,
    )
    st.plotly_chart(figure, use_container_width=True)


def render_report_tab() -> None:
    """Affiche le rapport markdown et les boutons de téléchargement."""
    report_markdown = st.session_state.get("report_markdown") or ""

    if not report_markdown:
        st.info("Le rapport sera disponible après l'analyse complète.")
        return

    st.markdown(report_markdown)

    st.download_button(
        "📥 Télécharger le rapport (Markdown)",
        data=report_markdown,
        file_name="citymatch_rapport.md",
        mime="text/markdown",
        use_container_width=True,
    )

    report_path = _resolve_report_path(
        st.session_state.get("report_path")
        or st.session_state.get("report_pdf_path")
        or ""
    )

    if report_path is None:
        return

    try:
        with report_path.open("rb") as file:
            st.download_button(
                "📥 Télécharger le rapport (PDF)",
                data=file,
                file_name="citymatch_rapport.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
    except OSError:
        st.warning("Le fichier PDF du rapport est introuvable ou illisible.")


def _resolve_report_path(raw_path: str | Path) -> Path | None:
    """Résout et sécurise le chemin du rapport PDF."""
    if not raw_path:
        return None

    candidate = Path(raw_path)

    if not candidate.is_absolute():
        candidate = REPORTS_DIR / candidate.name

    try:
        reports_root = REPORTS_DIR.resolve()
        resolved = candidate.resolve()
    except OSError:
        return None

    if not resolved.exists() or resolved.suffix.lower() != ".pdf":
        return None

    if resolved != reports_root and reports_root not in resolved.parents:
        return None

    return resolved


def _to_float(value: Any, default: float | None = None) -> float | None:
    """Convertit une valeur en float nullable."""
    if value is None:
        return default

    try:
        number = float(value)
    except (TypeError, ValueError):
        return default

    return number


def _to_int(value: Any, default: int = 0) -> int:
    """Convertit une valeur en int avec fallback."""
    if value is None:
        return default

    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _format_int(value: int) -> str:
    """Formate un entier avec séparateurs français simples."""
    return f"{value:,}".replace(",", " ")


def _format_float(value: float | None, decimals: int = 1) -> str:
    """Formate un float avec séparateurs français simples."""
    if value is None:
        return "N/A"

    return f"{value:,.{decimals}f}".replace(",", " ")


def _fmt(value: Any, fmt: str) -> str | None:
    """Formate une valeur nullable."""
    number = _to_float(value)

    if number is None:
        return None

    try:
        return fmt.format(number).replace(",", " ")
    except (TypeError, ValueError):
        return None


def _html_escape(value: Any) -> str:
    """Échappe une valeur pour insertion dans une popup HTML."""
    return html.escape(str(value or ""), quote=True)