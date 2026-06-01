"""
ui/services/city_loader.py
──────────────────────────
Chargement des villes depuis la base pour l'onglet Explorer.
"""

from __future__ import annotations

import streamlit as st


@st.cache_data(ttl=300)
def load_all_cities() -> list[dict]:
    """
    Charge toutes les villes utiles à l'exploration.

    Le cache est court pour refléter une nouvelle ingestion sans redémarrer
    Streamlit trop souvent.
    """
    from db.models import City, SessionLocal

    db = SessionLocal()
    try:
        cities = db.query(City).all()
        return [
            {
                "code_insee": c.code_insee,
                "nom": c.nom,
                "region": c.region or "",
                "departement": c.departement or "",
                "latitude": c.latitude,
                "longitude": c.longitude,
                "population": c.population or 0,
                "distance_mer_km": c.distance_mer_km,
                "distance_montagne_km": c.distance_montagne_km,
                "prix_immo_m2": c.prix_immo_m2,
                "taux_chomage": c.taux_chomage,
                "score_securite": c.score_securite,
                "score_climat": c.score_climat,
                "ensoleillement_h_an": c.ensoleillement_h_an,
                "temperature_moyenne": c.temperature_moyenne,
                "precipitations_mm": c.precipitations_mm,
                "qualite_air_score": c.qualite_air_score,
                "fibre_pct": c.fibre_pct,
            }
            for c in cities
            if c.latitude and c.longitude
        ]
    finally:
        db.close()
