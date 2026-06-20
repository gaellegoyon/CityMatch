"""
db/models.py
────────────
Modèles SQLAlchemy et initialisation de la base SQLite.

Tables :
- cities
- city_scores
- search_sessions
- agent_logs
- web_search_cache
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from config.settings import DATABASE_URL


logger = logging.getLogger(__name__)

Base = declarative_base()


def utc_now() -> datetime:
    """Retourne une date timezone-aware en UTC."""
    return datetime.now(timezone.utc)


def _json_safe_value(value: Any) -> Any:
    """Convertit les valeurs non JSON simples, notamment les dates."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value


# ─────────────────────────────────────────────────────────────────────────────
# Table principale : données communales
# ─────────────────────────────────────────────────────────────────────────────
class City(Base):
    """
    Données communales conservées pour le scoring CityMatch.

    Principes :
    - garder uniquement les champs issus de sources solides ou de calculs simples ;
    - supprimer les scores trop estimés, saturés, non discriminants ou biaisés ;
    - privilégier les ratios par habitant pour comparer correctement les villes ;
    - laisser NULL lorsqu'une source fiable ne fournit pas la donnée.
    """

    __tablename__ = "cities"

    # Identité / localisation
    id = Column(Integer, primary_key=True, autoincrement=True)
    code_insee = Column(String(10), unique=True, nullable=False, index=True)
    nom = Column(String(200), nullable=False)
    departement = Column(String(100))
    region = Column(String(100))
    population = Column(Integer)
    latitude = Column(Float)
    longitude = Column(Float)

    # INSEE — économie / démographie
    taux_chomage = Column(Float)              # % actifs au chômage
    revenu_median = Column(Float)             # €/an — niveau de vie médian INSEE
    age_median = Column(Float)                # années, estimé depuis les classes d'âge INSEE
    pct_moins_15_ans = Column(Float)          # % population < 15 ans
    pct_plus_65_ans = Column(Float)           # % population >= 65 ans
    taux_natalite = Column(Float)             # naissances pour 1000 habitants
    evolution_population_pct = Column(Float)  # évolution population sur environ 5 ans
    nb_entreprises = Column(Integer, default=0)
    entreprises_pour_1000 = Column(Float)

    # INSEE BPE — équipements bruts exploitables
    nb_creches = Column(Integer, default=0)
    nb_ecoles_primaires = Column(Integer, default=0)
    nb_colleges = Column(Integer, default=0)
    nb_lycees = Column(Integer, default=0)
    nb_medecins_generalistes = Column(Integer, default=0)
    nb_pharmacies = Column(Integer, default=0)
    nb_hopitaux = Column(Integer, default=0)
    nb_gares = Column(Integer, default=0)
    nb_piscines = Column(Integer, default=0)
    nb_bibliotheques = Column(Integer, default=0)
    nb_supermarches = Column(Integer, default=0)
    nb_restaurants = Column(Integer, default=0)
    nb_equipements_sportifs = Column(Integer, default=0)
    nb_cinemas = Column(Integer, default=0)
    nb_musees = Column(Integer, default=0)
    nb_dentistes = Column(Integer, default=0)
    nb_ophtalmologues = Column(Integer, default=0)
    nb_pediatres = Column(Integer, default=0)
    nb_urgences = Column(Integer, default=0)

    # INSEE BPE — ratios comparables entre villes
    creches_pour_1000 = Column(Float)
    medecins_pour_1000 = Column(Float)
    medecins_specialistes_pour_1000 = Column(Float)
    nb_pharmacies_pour_1000 = Column(Float)
    ecoles_pour_1000_enfants = Column(Float)
    nb_lycees_pour_1000_ados = Column(Float)
    supermarches_pour_1000 = Column(Float)
    score_restauration = Column(Float)
    transport_score = Column(Float)           # score simple basé sur gares + taille de ville

    # Immobilier
    prix_immo_m2 = Column(Float)              # €/m², DVF nettoyé par mutation
    taux_logements_vacants = Column(Float)

    # Sécurité
    criminalite_pour_1000 = Column(Float)
    cambriolages_pour_1000 = Column(Float)
    violences_physiques_pour_1000 = Column(Float)
    score_securite = Column(Float)

    # Géographie objective
    distance_mer_km = Column(Float)
    distance_montagne_km = Column(Float)

    # Connectivité
    fibre_pct = Column(Float)                 # % fibre ARCEP réel, NULL si indisponible

    # Air / environnement fiable
    qualite_air_score = Column(Float)         # score 0-10 ATMO réel, NULL si indisponible

    # Climat utile au matching utilisateur.
    # Indicateurs orientatifs stables, pas une mesure météo temps réel.
    ensoleillement_h_an = Column(Float)       # heures d'ensoleillement/an
    temperature_moyenne = Column(Float)       # température moyenne annuelle en °C
    precipitations_mm = Column(Float)         # précipitations annuelles en mm
    score_climat = Column(Float)              # score agrégé 0-10, orientatif

    # Métadonnées techniques
    last_updated = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    # Relations
    scores = relationship(
        "CityScore",
        back_populates="city",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("idx_city_region", "region"),
        Index("idx_city_departement", "departement"),
        Index("idx_city_population", "population"),
        Index("idx_city_price", "prix_immo_m2"),
        Index("idx_city_air", "qualite_air_score"),
        Index("idx_city_security", "score_securite"),
        Index("idx_city_fibre", "fibre_pct"),
        Index("idx_city_sea_distance", "distance_mer_km"),
        Index("idx_city_mountain_distance", "distance_montagne_km"),
    )

    def to_dict(self) -> dict[str, Any]:
        """Sérialise la ville en dictionnaire simple et JSON-compatible."""
        return {
            column.name: _json_safe_value(getattr(self, column.name))
            for column in self.__table__.columns
        }

    def __repr__(self) -> str:
        return f"<City {self.nom} ({self.code_insee}) pop={self.population}>"


# ─────────────────────────────────────────────────────────────────────────────
# Scores calculés par session utilisateur
# ─────────────────────────────────────────────────────────────────────────────
class CityScore(Base):
    """
    Score pondéré calculé pour une ville dans le contexte d'une session utilisateur.
    """

    __tablename__ = "city_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    city_id = Column(Integer, ForeignKey("cities.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(String(100), nullable=False, index=True)
    total_score = Column(Float, nullable=False)  # score global pondéré 0-100
    rank = Column(Integer)
    score_details = Column(JSON)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    city = relationship("City", back_populates="scores")

    __table_args__ = (
        Index("idx_city_scores_city_id", "city_id"),
        Index("idx_session_score", "session_id", "total_score"),
        Index("idx_session_rank", "session_id", "rank"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Sessions de recherche
# ─────────────────────────────────────────────────────────────────────────────
class SearchSession(Base):
    """
    Session de recherche conversationnelle.

    Permet la reprise d'une session et le partage de contexte entre agents.
    """

    __tablename__ = "search_sessions"

    id = Column(String(100), primary_key=True)       # UUID
    user_criteria = Column(JSON)                    # critères et pondérations bruts
    normalized_criteria = Column(JSON)              # critères normalisés pour scoring
    conversation_history = Column(JSON)             # historique complet du dialogue
    state = Column(String(50), default="active")    # active | completed | paused
    top_cities = Column(JSON)                       # cache des tops villes
    report_path = Column(String(500))               # chemin vers le rapport généré
    iteration = Column(Integer, default=1)          # nombre de raffinements
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    __table_args__ = (
        Index("idx_search_session_state", "state"),
        Index("idx_search_session_updated", "updated_at"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Logs des agents
# ─────────────────────────────────────────────────────────────────────────────
class AgentLog(Base):
    """
    Trace des actions d'agents pour debug, audit et analyse des performances.
    """

    __tablename__ = "agent_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(100), index=True)
    agent_name = Column(String(100), nullable=False)
    action = Column(String(200))
    input_data = Column(JSON)
    output_data = Column(JSON)
    duration_ms = Column(Integer)
    success = Column(Boolean, default=True)
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        Index("idx_agent_log_session_agent", "session_id", "agent_name"),
        Index("idx_agent_log_created", "created_at"),
        Index("idx_agent_log_success", "success"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Cache des recherches web
# ─────────────────────────────────────────────────────────────────────────────
class WebSearchCache(Base):
    """
    Cache TTL pour les résultats de recherche web.

    Évite les appels redondants à Tavily / DuckDuckGo.
    """

    __tablename__ = "web_search_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    query_hash = Column(String(64), unique=True, nullable=False, index=True)
    query = Column(Text)
    results = Column(JSON)
    created_at = Column(DateTime(timezone=True), default=utc_now)
    expires_at = Column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_web_cache_expires", "expires_at"),
        Index("idx_web_cache_created", "created_at"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Initialisation SQLAlchemy
# ─────────────────────────────────────────────────────────────────────────────
def _engine_connect_args() -> dict[str, Any]:
    """Retourne les connect_args adaptés au moteur utilisé."""
    if DATABASE_URL.startswith("sqlite"):
        return {"check_same_thread": False}

    return {}


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Retourne le moteur SQLAlchemy configuré et mis en cache."""
    return create_engine(
        DATABASE_URL,
        connect_args=_engine_connect_args(),
        echo=False,
        future=True,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker:
    """Retourne une factory de sessions SQLAlchemy."""
    return sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=get_engine(),
        future=True,
    )


def init_db() -> Engine:
    """
    Initialise la base de données.

    Crée les tables si elles n'existent pas encore.
    Attention : create_all ne modifie pas les tables déjà existantes.
    Si le schéma change, il faut recréer la base SQLite ou utiliser une migration.
    """
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    logger.info("Base de données initialisée.")
    return engine


# Singleton de session pour usage direct dans le projet.
SessionLocal = get_session_factory()


__all__ = [
    "AgentLog",
    "Base",
    "City",
    "CityScore",
    "SearchSession",
    "SessionLocal",
    "WebSearchCache",
    "get_engine",
    "get_session_factory",
    "init_db",
    "utc_now",
]
