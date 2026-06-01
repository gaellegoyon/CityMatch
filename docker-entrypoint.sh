#!/bin/bash
# ════════════════════════════════════════════════════════════
# docker-entrypoint.sh — Démarrage CityMatch
# Initialise l'environnement puis lance Streamlit.
# ════════════════════════════════════════════════════════════

set -euo pipefail

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   CityMatch v1.0 — Démarrage Docker      ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# Toujours travailler depuis la racine app.
cd /app

# ── Vérification des clés API ────────────────────────────────────────────────
if [ -z "${GROQ_API_KEY:-}" ] && [ -z "${GOOGLE_API_KEY:-}" ]; then
    echo "⚠️  Aucune clé API LLM configurée."
    echo "   Définissez GROQ_API_KEY ou GOOGLE_API_KEY dans .env"
    echo "   Le dialogue LLM ne fonctionnera pas tant qu'une clé n'est pas disponible."
    echo ""
fi

# ── Création des dossiers persistants ────────────────────────────────────────
mkdir -p db data/cache data/docs reports/output vectorstore logs

# ── Initialisation DB ────────────────────────────────────────────────────────
echo "🗄️  Initialisation de la base SQLite..."
python - <<'PY'
from db.models import init_db
init_db()
print("   ✅ Base de données prête.")
PY

# ── Index communes ───────────────────────────────────────────────────────────
if [ ! -f "data/cache/communes_index.json" ]; then
    echo ""
    echo "📍 Index communes absent. Génération automatique..."
    echo "   Seuil par défaut : ${CITYMATCH_COMMUNE_SEUIL:-20000} habitants"
    python data/build_communes_index.py --seuil "${CITYMATCH_COMMUNE_SEUIL:-20000}" || {
        echo "   ⚠️  Impossible de générer l'index. L'ingest utilisera sa liste de secours."
    }
else
    echo "✅ Index communes présent."
fi

# ── Vérification du nombre de villes ─────────────────────────────────────────
CITY_COUNT=$(python - <<'PY' 2>/dev/null || echo "0"
from db.models import SessionLocal, City
db = SessionLocal()
try:
    print(db.query(City).count())
finally:
    db.close()
PY
)

echo ""
if [ "${CITY_COUNT:-0}" -gt "0" ]; then
    echo "✅ Base de données : ${CITY_COUNT} ville(s) disponible(s)."
else
    echo "⚠️  Base de données vide."
    echo "   Pour ingérer les données réelles :"
    echo "   docker compose run --rm citymatch python data/ingest_real_data.py --workers 8"
    echo ""

    if [ "${LOAD_DEMO_DATA:-0}" = "1" ]; then
        echo "📦 Chargement des données de démonstration demandé..."
        python data/ingest_data.py && echo "   ✅ Données démo chargées." || echo "   ⚠️  Échec données démo."
    else
        echo "   LOAD_DEMO_DATA=1 permet de charger les données démo au démarrage."
    fi
fi

# ── Mode commande custom ─────────────────────────────────────────────────────
# Permet par exemple :
# docker compose run --rm citymatch python data/ingest_real_data.py --workers 8
if [ "$#" -gt 0 ]; then
    echo ""
    echo "▶️  Commande custom : $*"
    exec "$@"
fi

# ── Lancement Streamlit ──────────────────────────────────────────────────────
echo ""
echo "🚀 Lancement de CityMatch Streamlit..."
echo "   URL locale : http://localhost:${STREAMLIT_SERVER_PORT:-8501}"
echo ""

exec streamlit run ui/app.py \
    --server.port="${STREAMLIT_SERVER_PORT:-8501}" \
    --server.address="${STREAMLIT_SERVER_ADDRESS:-0.0.0.0}" \
    --server.headless=true \
    --browser.gatherUsageStats=false \
    --logger.level="${LOG_LEVEL:-info}"
