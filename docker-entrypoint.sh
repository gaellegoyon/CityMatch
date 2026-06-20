#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════
# docker-entrypoint.sh — Démarrage CityMatch
# Initialise l'environnement puis lance Streamlit.
# ════════════════════════════════════════════════════════════

set -Eeuo pipefail

APP_DIR="${APP_DIR:-/app}"
STREAMLIT_PORT="${STREAMLIT_SERVER_PORT:-8501}"
STREAMLIT_ADDRESS="${STREAMLIT_SERVER_ADDRESS:-0.0.0.0}"
COMMUNE_SEUIL="${CITYMATCH_COMMUNE_SEUIL:-20000}"
LOAD_DEMO="${LOAD_DEMO_DATA:-0}"
LOG_LEVEL="${LOG_LEVEL:-info}"

log() {
    printf '%s\n' "$*"
}

warn() {
    printf '⚠️  %s\n' "$*" >&2
}

is_positive_integer() {
    case "${1:-}" in
        ''|*[!0-9]*)
            return 1
            ;;
        *)
            [ "$1" -gt 0 ]
            ;;
    esac
}

python_count_cities() {
    python - <<'PY' 2>/dev/null || true
from db.models import City, SessionLocal

db = SessionLocal()
try:
    print(db.query(City).count())
finally:
    db.close()
PY
}

run_python_if_exists() {
    local script_path="$1"
    shift

    if [ -f "$script_path" ]; then
        python "$script_path" "$@"
    else
        warn "Script introuvable : $script_path"
        return 1
    fi
}

log ""
log "╔══════════════════════════════════════════╗"
log "║   CityMatch v1.0 — Démarrage Docker     ║"
log "╚══════════════════════════════════════════╝"
log ""

cd "$APP_DIR"

# ── Vérification des clés API ────────────────────────────────────────────────
if [ -z "${GROQ_API_KEY:-}" ] && [ -z "${GOOGLE_API_KEY:-}" ] && [ -z "${GEMINI_API_KEY:-}" ]; then
    warn "Aucune clé API LLM configurée."
    log "   Définissez GROQ_API_KEY, GOOGLE_API_KEY ou GEMINI_API_KEY dans .env"
    log "   Le dialogue LLM ne fonctionnera pas tant qu'une clé n'est pas disponible."
    log ""
fi

# ── Validation légère des variables numériques ───────────────────────────────
if ! is_positive_integer "$STREAMLIT_PORT"; then
    warn "STREAMLIT_SERVER_PORT invalide (${STREAMLIT_PORT}), fallback sur 8501."
    STREAMLIT_PORT="8501"
fi

if ! is_positive_integer "$COMMUNE_SEUIL"; then
    warn "CITYMATCH_COMMUNE_SEUIL invalide (${COMMUNE_SEUIL}), fallback sur 20000."
    COMMUNE_SEUIL="20000"
fi

# ── Création des dossiers persistants ────────────────────────────────────────
mkdir -p \
    db \
    data/cache \
    data/docs \
    data/pdfs \
    reports/output \
    vectorstore \
    logs

# ── Initialisation DB ────────────────────────────────────────────────────────
log "🗄️  Initialisation de la base SQLite..."
python - <<'PY'
from db.models import init_db

init_db()
print("   ✅ Base de données prête.")
PY

# ── Index communes ───────────────────────────────────────────────────────────
if [ ! -f "data/cache/communes_index.json" ]; then
    log ""
    log "📍 Index communes absent. Génération automatique..."
    log "   Seuil par défaut : ${COMMUNE_SEUIL} habitants"

    if run_python_if_exists "data/build_communes_index.py" --seuil "$COMMUNE_SEUIL"; then
        log "   ✅ Index communes généré."
    else
        warn "Impossible de générer l'index. L'ingestion utilisera sa liste de secours."
    fi
else
    log "✅ Index communes présent."
fi

# ── Vérification du nombre de villes ─────────────────────────────────────────
CITY_COUNT="$(python_count_cities | tail -n 1 | tr -dc '0-9')"

if [ -z "$CITY_COUNT" ]; then
    CITY_COUNT="0"
fi

log ""

if [ "$CITY_COUNT" -gt 0 ]; then
    log "✅ Base de données : ${CITY_COUNT} ville(s) disponible(s)."
else
    warn "Base de données vide."
    log "   Pour ingérer les données réelles :"
    log "   docker compose run --rm citymatch python data/ingest_real_data.py --workers 8"
    log ""

    if [ "$LOAD_DEMO" = "1" ]; then
        log "📦 Chargement des données de démonstration demandé..."

        if run_python_if_exists "data/ingest_data.py"; then
            log "   ✅ Données démo chargées."
        else
            warn "Échec du chargement des données démo."
        fi
    else
        log "   LOAD_DEMO_DATA=1 permet de charger les données démo au démarrage."
    fi
fi

# ── Mode commande custom ─────────────────────────────────────────────────────
# Exemples :
# docker compose run --rm citymatch python data/ingest_real_data.py --workers 8
# docker compose run --rm citymatch pytest
if [ "$#" -gt 0 ]; then
    log ""
    log "▶️  Commande custom : $*"
    exec "$@"
fi

# ── Lancement Streamlit ──────────────────────────────────────────────────────
log ""
log "🚀 Lancement de CityMatch Streamlit..."
log "   URL locale : http://localhost:${STREAMLIT_PORT}"
log ""

exec streamlit run ui/app.py \
    --server.port="${STREAMLIT_PORT}" \
    --server.address="${STREAMLIT_ADDRESS}" \
    --server.headless=true \
    --browser.gatherUsageStats=false \
    --logger.level="${LOG_LEVEL}"
