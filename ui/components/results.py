"""
ui/components/results.py
────────────────────────
Affichage des résultats : carte, graphiques, classement, rapport.
"""

from __future__ import annotations

from pathlib import Path

import folium
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from streamlit_folium import st_folium

from config.settings import AVAILABLE_CRITERIA


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
        for i, city in enumerate(top_cities):
            render_city_card(city, city.get("rank", i + 1))

    with tab_report:
        render_report_tab()


def render_initial_state() -> None:
    """Affiche la carte vide et l'aide de départ."""
    st.markdown("### 🗺️ Carte de France")
    m = folium.Map(location=[46.8, 2.3], zoom_start=5, tiles="CartoDB positron")
    st_folium(m, width=None, height=400, returned_objects=[])

    st.info(
        "💡 **Comment ça marche ?**\n\n"
        "1. 🗣️ Décrivez votre situation et vos préférences\n"
        "2. 🤖 L'IA structure vos critères et les pondère\n"
        "3. 🗄️ La base filtre les villes compatibles\n"
        "4. 🔍 Les résultats peuvent être enrichis par recherche web\n"
        "5. 🏆 Un score personnalisé classe les villes\n"
        "6. 📄 Un rapport est généré"
    )


def render_city_card(city: dict, rank: int) -> None:
    """Affiche une carte synthétique pour une ville recommandée."""
    score = city.get("total_score", 0)

    col1, col2, col3 = st.columns([3, 2, 2])

    with col1:
        st.markdown(f"### #{rank} — {city.get('nom', '?')}")
        st.caption(f"📍 {city.get('departement', '?')} | {city.get('region', '?')}")
        st.caption(f"👥 {city.get('population', 0):,} habitants")

        taille = city.get("taille_ville")
        if taille:
            st.caption(f"🏙️ {taille}")

        surface = city.get("surface_estimable_m2")
        if surface is not None:
            st.caption(f"🏠 Surface estimable avec budget : ~{surface:.0f} m²")

        distance_ref = city.get("distance_reference_km")
        if distance_ref is not None:
            st.caption(f"📍 Distance référence : {distance_ref:.0f} km")

    with col2:
        st.metric("Score global", f"{score:.1f}/100")

        chomage = city.get("taux_chomage")
        st.metric("Chômage", f"{chomage:.1f}%" if chomage is not None else "N/A")

        securite = city.get("score_securite")
        st.metric("Sécurité", f"{securite:.1f}/10" if securite is not None else "N/A")

    with col3:
        prix = city.get("prix_immo_m2")
        st.metric("Prix m²", f"{prix:,.0f} €" if prix is not None else "N/A")

        fibre = city.get("fibre_pct")
        st.metric("Fibre", f"{fibre:.0f}%" if fibre is not None else "N/A")

        air = city.get("qualite_air_score")
        st.metric("Air", f"{air:.1f}/10" if air is not None else "N/A")

    st.divider()


def render_map(cities: list[dict]) -> None:
    """Affiche une carte Folium des villes recommandées."""
    if not cities:
        return

    m = folium.Map(location=[46.8, 2.3], zoom_start=6, tiles="CartoDB positron")
    colors = [
        "#22c55e", "#3b82f6", "#f59e0b", "#8b5cf6", "#ef4444",
        "#06b6d4", "#84cc16", "#f97316", "#ec4899", "#6b7280",
    ]

    for i, city in enumerate(cities[:10]):
        lat = city.get("latitude")
        lon = city.get("longitude")
        if not lat or not lon:
            continue

        rank = city.get("rank", i + 1)
        color = colors[i % len(colors)]
        popup_html = build_city_popup(city, color, rank)

        folium.CircleMarker(
            location=[lat, lon],
            radius=max(6, 20 - rank),
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.7,
            tooltip=f"#{rank} {city.get('nom', '?')} — {city.get('total_score', 0):.1f}/100",
            popup=folium.Popup(popup_html, max_width=280),
        ).add_to(m)

        folium.Marker(
            location=[lat, lon],
            icon=folium.DivIcon(
                html=(
                    f'<div style="font-size:10px;font-weight:bold;color:white;'
                    f'background:{color};border-radius:50%;width:22px;height:22px;'
                    f'display:flex;align-items:center;justify-content:center;">'
                    f"{rank}</div>"
                ),
                icon_size=(22, 22),
                icon_anchor=(11, 11),
            ),
        ).add_to(m)

    st_folium(m, width=None, height=450, returned_objects=[])


def build_city_popup(city: dict, color: str, rank: int) -> str:
    """Construit le HTML de popup pour une ville recommandée."""
    fields = [
        ("Région", city.get("region")),
        ("Population", f"{city.get('population', 0):,}"),
        ("Chômage", _fmt(city.get("taux_chomage"), "{:.1f}%")),
        ("Prix m²", _fmt(city.get("prix_immo_m2"), "{:,.0f} €")),
        ("Mer", _fmt(city.get("distance_mer_km"), "{:.0f} km")),
        ("Montagne", _fmt(city.get("distance_montagne_km"), "{:.0f} km")),
        ("Fibre", _fmt(city.get("fibre_pct"), "{:.0f}%")),
        ("Air", _fmt(city.get("qualite_air_score"), "{:.1f}/10")),
        ("Temp.", _fmt(city.get("temperature_moyenne"), "{:.1f} °C")),
    ]

    rows = "\n".join(
        f"<b>{label} :</b> {value}<br>"
        for label, value in fields
        if value not in (None, "", "N/A")
    )

    return f"""
    <div style="font-family: Arial; min-width: 220px;">
        <h4 style="color: {color};">#{rank} {city.get('nom', '?')}</h4>
        <b>Score :</b> {city.get('total_score', 0):.1f}/100<br>
        {rows}
    </div>
    """


def render_bar_chart(cities: list[dict]) -> None:
    """Affiche un bar chart des scores globaux."""
    df = pd.DataFrame(
        [
            {
                "Ville": c.get("nom", "?"),
                "Score": c.get("total_score", 0),
                "Rang": c.get("rank", 0),
            }
            for c in cities[:10]
        ]
    )

    fig = px.bar(
        df,
        x="Score",
        y="Ville",
        orientation="h",
        color="Score",
        color_continuous_scale="viridis",
        title="Scores globaux des villes recommandées",
        labels={"Score": "Score (/100)", "Ville": ""},
    )
    fig.update_layout(height=350, yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig, use_container_width=True)


def render_radar_chart(cities: list[dict]) -> None:
    """Affiche un radar chart comparant les TOP 5."""
    if not cities or not cities[0].get("score_details"):
        return

    fig = go.Figure()

    for city in cities[:5]:
        details = city.get("score_details", {})
        visible_details = {
            k: v for k, v in details.items()
            if v.get("normalized_score") is not None
        }

        categories = [
            AVAILABLE_CRITERIA.get(k, {}).get("label", k)[:15]
            for k in visible_details.keys()
        ]
        scores = [d.get("normalized_score", 0) for d in visible_details.values()]

        if categories:
            fig.add_trace(
                go.Scatterpolar(
                    r=scores + [scores[0]],
                    theta=categories + [categories[0]],
                    fill="toself",
                    opacity=0.6,
                    name=city.get("nom", "?"),
                )
            )

    fig.update_layout(
        polar={"radialaxis": {"visible": True, "range": [0, 10]}},
        showlegend=True,
        title="Comparaison des TOP 5 villes",
        height=450,
    )
    st.plotly_chart(fig, use_container_width=True)


def render_report_tab() -> None:
    """Affiche le rapport markdown et les boutons de téléchargement."""
    if not st.session_state.report_markdown:
        st.info("Le rapport sera disponible après l'analyse complète.")
        return

    st.markdown(st.session_state.report_markdown)

    st.download_button(
        "📥 Télécharger le rapport (Markdown)",
        data=st.session_state.report_markdown,
        file_name="citymatch_rapport.md",
        mime="text/markdown",
        use_container_width=True,
    )

    report_path = Path(st.session_state.report_path)
    if report_path.exists() and report_path.suffix == ".pdf":
        with open(report_path, "rb") as f:
            st.download_button(
                "📥 Télécharger le rapport (PDF)",
                data=f,
                file_name="citymatch_rapport.pdf",
                mime="application/pdf",
                use_container_width=True,
            )


def _fmt(value, fmt: str) -> str | None:
    """Formate une valeur nullable."""
    if value is None:
        return None
    try:
        return fmt.format(value)
    except Exception:
        return None
