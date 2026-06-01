# 🏙️ CityMatch

CityMatch est une application d’aide au choix d’une ville où s’installer en France.

L’utilisateur décrit son projet en langage naturel, par exemple :

> “On est un couple de 25 ans, on cherche une ville proche de la mer, sécurisée, avec fibre, et un budget de 200 000 € pour une maison.”

L’application transforme cette demande en critères exploitables, interroge une base de données de villes françaises, calcule un score personnalisé, puis affiche les meilleures villes avec une carte, des graphiques et un rapport.

---

## Ce que fait le projet

CityMatch permet de comparer des villes françaises selon des critères concrets :

- budget immobilier ;
- prix au m² ;
- sécurité ;
- fibre ;
- qualité de l’air quand disponible ;
- proximité de la mer ;
- proximité de la montagne ;
- proximité d’une ville de référence comme Lyon, Bordeaux ou Paris ;
- taille de ville ;
- climat ;
- santé ;
- écoles ;
- commerces et services ;
- dynamisme économique.

Le projet privilégie les données réelles et exploitables. Les critères trop incomplets, trop subjectifs ou non fiables ne sont pas utilisés dans le scoring.

---

## Fonctionnement général

Le projet repose sur plusieurs étapes :

1. **Ingestion des données**
   - Récupération et nettoyage des données publiques.
   - Construction de la base SQLite `db/cities.db`.

2. **Analyse de la demande utilisateur**
   - L’agent IA comprend la demande en langage naturel.
   - Il extrait les critères importants et leur poids.

3. **Recherche en base**
   - Les villes incompatibles sont filtrées.
   - Exemple : budget trop faible, ville trop loin de la mer, population trop élevée.

4. **Scoring**
   - Chaque ville reçoit un score personnalisé.
   - Les critères sont normalisés et pondérés.

5. **Interface**
   - Résultats dans Streamlit.
   - Carte interactive.
   - Classement.
   - Graphiques.
   - Rapport.

---

## Architecture simplifiée

```text
Utilisateur
   │
   ▼
Interface Streamlit
   │
   ▼
LangGraph Orchestrator
   │
   ├── UserProfileAgent   → comprend la demande
   ├── DatabaseAgent      → récupère les villes candidates
   ├── ScoringAgent       → calcule les scores
   ├── RAGAgent           → répond aux questions méthodologiques
   ├── WebSearchAgent     → enrichissement web éventuel
   └── ReportAgent        → génère le rapport
```

---

## Sources de données

CityMatch utilise principalement :

| Domaine | Source |
|---|---|
| Population, revenus, chômage, logements | INSEE |
| Équipements, santé, écoles, commerces | INSEE BPE |
| Immobilier | DVF |
| Sécurité | SSMSI |
| Fibre | ARCEP |
| Qualité de l’air | ATMO + sources régionales officielles |
| Distances mer / montagne | Calcul géographique |
| Climat | Données climatiques agrégées |

---

## Lancer le projet avec Docker

Docker est le mode recommandé.

### 1. Préparer le fichier `.env`

Créer un fichier `.env` à la racine du projet :

```bash
cp .env.example .env
```

Renseigner au moins une clé LLM :

```env
GROQ_API_KEY=
GOOGLE_API_KEY=
TAVILY_API_KEY=
```

`GROQ_API_KEY` ou `GOOGLE_API_KEY` est nécessaire pour le dialogue IA.

---

### 2. Construire et lancer l’application

```bash
docker compose up --build
```

Puis ouvrir :

```text
http://localhost:8501
```

---

### 3. Lancer en arrière-plan

```bash
docker compose up -d --build
```

---

### 4. Arrêter l’application

```bash
docker compose down
```

---

### 5. Voir les logs

```bash
docker compose logs -f
```

---

## Ingestion des données avec Docker

La base de données réelle peut être longue à construire.

### Générer l’index des communes

```bash
docker compose run --rm citymatch python data/build_communes_index.py --seuil 20000
```

### Lancer l’ingestion complète

```bash
docker compose run --rm citymatch python data/ingest_real_data.py --workers 8
```

### Lancer une ingestion de test

```bash
docker compose run --rm citymatch python data/ingest_real_data.py --test --workers 2
```

### Relancer l’application après ingestion

```bash
docker compose up
```

---

## Commandes Docker utiles

### Ouvrir un shell dans le container

```bash
docker compose run --rm citymatch bash
```

### Exécuter une commande Python

```bash
docker compose run --rm citymatch python --version
```

### Vérifier que l’application répond

```bash
curl http://localhost:8501/_stcore/health
```

### Reconstruire l’image sans cache

```bash
docker compose build --no-cache
docker compose up
```

### Supprimer les containers

```bash
docker compose down
```

### Supprimer aussi les volumes Docker

Attention : cette commande supprime la base Docker, le cache, les rapports et le vectorstore.

```bash
docker compose down -v
```

---

## Développement local sans Docker

Le projet peut aussi être lancé localement avec un environnement virtuel Python.

### Créer le venv

```bash
python -m venv venv
```

Sur Git Bash / Linux / macOS :

```bash
source venv/Scripts/activate
```

Sur PowerShell :

```powershell
.\venv\Scripts\Activate.ps1
```

### Installer les dépendances

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### Lancer l’application

```bash
streamlit run ui/app.py
```

Si tu utilises uniquement Docker, le dossier local `venv/` n’est pas nécessaire et peut être supprimé :

```bash
rm -rf venv
```

---

## Commandes utiles hors Docker

### Vérifier la syntaxe Python

```bash
python -m py_compile \
  data/ingest_real_data.py \
  data/ingest/*.py \
  data/ingest/sources/*.py \
  agents/*.py \
  agents/common/*.py \
  agents/database/*.py \
  agents/profile/*.py \
  agents/reporting/*.py \
  ui/app.py \
  ui/components/*.py \
  ui/services/*.py \
  db/models.py \
  config/settings.py
```

### Lancer l’ingestion localement

```bash
python data/ingest_real_data.py --workers 8
```

### Lancer l’ingestion de test

```bash
python data/ingest_real_data.py --test --workers 2
```

### Lancer Streamlit localement

```bash
streamlit run ui/app.py
```

---

## Auditer la base de données

### Nombre de villes

```bash
python - <<'PY'
import sqlite3
conn = sqlite3.connect("db/cities.db")
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM cities")
print("Villes:", cur.fetchone()[0])
conn.close()
PY
```

### Vérifier les valeurs manquantes principales

```bash
python - <<'PY'
import sqlite3

cols = [
    "prix_immo_m2",
    "fibre_pct",
    "qualite_air_score",
    "score_securite",
    "distance_mer_km",
    "distance_montagne_km",
]

conn = sqlite3.connect("db/cities.db")
cur = conn.cursor()

for col in cols:
    cur.execute(f"""
        SELECT COUNT(*), SUM({col} IS NULL), MIN({col}), AVG({col}), MAX({col})
        FROM cities
    """)
    print(col, cur.fetchone())

conn.close()
PY
```

---

## Structure du projet

```text
citymatch/
  agents/                  # Agents IA
  config/                  # Configuration
  data/                    # Ingestion, cache, docs
    ingest/
      sources/
  db/                      # Modèles SQLAlchemy + base SQLite
  graph/                   # LangGraph state + orchestrator
  reports/output/          # Rapports générés
  ui/                      # Interface Streamlit
    components/
    services/
  utils/                   # Sécurité / validation
  vectorstore/             # ChromaDB pour le RAG

  Dockerfile
  docker-compose.yml
  docker-entrypoint.sh
  requirements.txt
  README.md
```

---

## Dossiers générés

Ces dossiers peuvent être créés automatiquement :

```text
db/cities.db
data/cache/
reports/output/
vectorstore/
```

Ils ne doivent pas forcément être versionnés dans Git.

---

## Variables d’environnement principales

```env
GROQ_API_KEY=
GOOGLE_API_KEY=
TAVILY_API_KEY=

DATABASE_URL=sqlite:///./db/cities.db

STREAMLIT_SERVER_PORT=8501
STREAMLIT_SERVER_ADDRESS=0.0.0.0

CITYMATCH_COMMUNE_SEUIL=20000
LOAD_DEMO_DATA=0
```

---

## Exemples de demandes utilisateur

```text
On est un couple de 25 ans sans enfants, on cherche une ville bord de mer à moins de 30 km, air pur, sécurisée. Budget 200 000€ pour une maison.
```

```text
Famille avec 2 enfants en primaire, on cherche une ville avec de bonnes écoles, proche de Lyon, budget 350 000€.
```

```text
J'ai 67 ans, je cherche une ville avec beaucoup de médecins et spécialistes, climat doux, pas trop chère. Bretagne ou Sud.
```

```text
Je télétravaille 100%, je veux fibre obligatoire, ville calme moins de 50 000 habitants, prix immobilier bas, nature proche.
```

---

## Résumé des commandes principales

```bash
# Lancer l’app avec Docker
docker compose up --build

# Ingestion complète avec Docker
docker compose run --rm citymatch python data/ingest_real_data.py --workers 8

# Ingestion test avec Docker
docker compose run --rm citymatch python data/ingest_real_data.py --test --workers 2

# Générer l’index communes
docker compose run --rm citymatch python data/build_communes_index.py --seuil 20000

# Arrêter
docker compose down

# Logs
docker compose logs -f

# Lancer localement sans Docker
streamlit run ui/app.py
```