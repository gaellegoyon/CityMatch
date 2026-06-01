# syntax=docker/dockerfile:1

# ════════════════════════════════════════════════════════════════════════════
# CityMatch — Dockerfile
# Build multi-stage : builder → runtime
#
# Commandes :
#   docker compose up --build
#   docker compose run --rm citymatch python data/ingest_real_data.py --workers 8
# ════════════════════════════════════════════════════════════════════════════

FROM python:3.11-slim AS builder

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
ENV PIP_NO_CACHE_DIR=1

# Dépendances de build nécessaires à certaines libs Python.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    libffi-dev \
    libssl-dev \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv "$VIRTUAL_ENV"
RUN pip install --upgrade pip wheel setuptools

WORKDIR /build

COPY requirements.txt .

# Torch CPU installé explicitement pour éviter une image GPU inutile.
RUN pip install \
    torch==2.3.1 \
    --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r requirements.txt


FROM python:3.11-slim AS runtime

LABEL description="CityMatch — Assistant IA pour trouver sa ville idéale"
LABEL version="1.0.0"

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Dépendances runtime uniquement.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    libgl1 \
    libglib2.0-0 \
    fontconfig \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Variables par défaut. Les secrets doivent venir de .env via docker compose.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    MPLBACKEND=Agg \
    CUDA_VISIBLE_DEVICES="" \
    DATABASE_URL=sqlite:///./db/cities.db \
    CHROMA_PERSIST_DIR=./vectorstore \
    APP_NAME=CityMatch \
    APP_VERSION=1.0.0 \
    LOG_LEVEL=INFO \
    MAX_CITIES_IN_REPORT=10

# Copier le code après les dépendances pour maximiser le cache Docker.
COPY . .

# Dossiers persistables.
RUN mkdir -p \
    db \
    data/cache \
    data/docs \
    reports/output \
    vectorstore \
    logs \
    && chmod +x /app/docker-entrypoint.sh

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -fsS http://localhost:8501/_stcore/health || exit 1

ENTRYPOINT ["/app/docker-entrypoint.sh"]
