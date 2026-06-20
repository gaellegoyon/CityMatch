# 🏙️ CityMatch

CityMatch est une application d’aide au choix d’une ville où s’installer en France.

L’utilisateur décrit son projet en langage naturel, par exemple :

> « On est un couple de 25 ans, on cherche une ville proche de la mer, sécurisée, avec fibre, et un budget de 200 000 € pour une maison. »

CityMatch transforme cette demande en critères exploitables, interroge une base SQLite de villes françaises, calcule un score personnalisé, enrichit les résultats lorsque c’est utile, puis affiche les meilleures villes avec une carte, des graphiques et un rapport Markdown/PDF.

---

## 🎯 Objectif du projet

Le projet répond à un cas d’usage concret : aider un utilisateur à comparer des villes françaises à partir de critères hétérogènes, souvent exprimés de façon floue.

CityMatch ne se limite pas à un simple appel LLM. L’application repose sur une architecture agentique avec :

- orchestration multi-agents avec LangGraph ;
- mémoire de session persistante ;
- base de données SQLite interrogée via SQLAlchemy ;
- RAG documentaire sur les fichiers méthodologiques ;
- recherche web active pour enrichir certaines recommandations ;
- génération de rapport structuré Markdown et PDF ;
- interface conversationnelle Streamlit.

---

## ✅ Ce que fait CityMatch

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
- dynamisme économique ;
- population minimale ou maximale ;
- chômage ;
- revenus ;
- logements vacants.

Le projet privilégie les données réelles et exploitables. Les critères trop incomplets, trop subjectifs ou non fiables ne sont pas utilisés directement dans le scoring.

Exemple : le critère « nature proche » est compris et signalé, mais il n’est pas transformé en score artificiel tant qu’aucune donnée fiable n’est disponible. En revanche, des critères mesurables comme « proche de la mer », « proche de la montagne », « petite ville » ou « climat doux » peuvent être utilisés.

---

## 🧠 Fonctionnement général

Le projet repose sur les étapes suivantes :

1. **Ingestion des données**
   - téléchargement et nettoyage de données publiques ;
   - construction de la base SQLite `db/cities.db` ;
   - calcul d’indicateurs géographiques et socio-économiques.

2. **Analyse de la demande utilisateur**
   - compréhension de la demande en langage naturel ;
   - extraction d’un profil structuré ;
   - pondération des critères.

3. **Recherche en base**
   - filtrage des villes incompatibles ;
   - application de contraintes de budget, distance, population ou région ;
   - fallback progressif si les filtres sont trop stricts.

4. **Recherche web**
   - enrichissement éventuel des villes finalistes ;
   - cache pour éviter de refaire les mêmes recherches trop souvent.

5. **Scoring**
   - normalisation des critères ;
   - pondération selon les priorités utilisateur ;
   - classement personnalisé.

6. **RAG documentaire**
   - réponse aux questions sur les sources, la méthode ou les indicateurs ;
   - recherche dans les documents de `data/docs/` et `data/pdfs/`.

7. **Restitution**
   - interface Streamlit ;
   - carte interactive ;
   - classement ;
   - graphiques ;
   - rapport Markdown et PDF.

---

## 🏗️ Architecture simplifiée

```text
Utilisateur
   │
   ▼
Interface Streamlit
   │
   ▼
LangGraph Orchestrator
   │
   ├── UserProfileAgent   → comprend la demande et extrait les critères
   ├── DatabaseAgent      → récupère les villes candidates en SQLite
   ├── WebSearchAgent     → enrichit les villes finalistes si utile
   ├── ScoringAgent       → calcule les scores personnalisés
   ├── RAGAgent           → répond aux questions méthodologiques
   └── ReportAgent        → génère le rapport Markdown/PDF
```

---

## 🤖 Agents

| Agent | Rôle |
|---|---|
| `UserProfileAgent` | Transforme le texte utilisateur en critères structurés. |
| `DatabaseAgent` | Interroge SQLite et applique les filtres principaux. |
| `WebSearchAgent` | Cherche des informations récentes ou complémentaires sur le web. |
| `ScoringAgent` | Normalise et pondère les critères pour classer les villes. |
| `RAGAgent` | Recherche dans les documents méthodologiques du projet. |
| `ReportAgent` | Produit un rapport Markdown et PDF. |

L’orchestration est assurée par LangGraph. L’état partagé circule entre les agents via un `CityMatchState`.

---

## 🗃️ Sources de données

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

Certaines données peuvent être incomplètes selon les communes. Une valeur manquante ne signifie pas nécessairement une mauvaise performance de la ville.

---

## 📦 Prérequis

### Avec Docker

- Docker ;
- Docker Compose ;
- une clé LLM gratuite au choix :
  - `GROQ_API_KEY` ;
  - ou `GOOGLE_API_KEY` / `GEMINI_API_KEY` selon la configuration utilisée.

### Sans Docker

- Python 3.11 recommandé ;
- `pip` ;
- un environnement virtuel Python ;
- les dépendances de `requirements.txt`.

---

## ⚙️ Configuration

Créer un fichier `.env` à la racine :

```bash
cp .env.example .env
```

Exemple minimal :

```env
# LLM, au moins une clé est nécessaire pour le dialogue IA
GROQ_API_KEY=
GOOGLE_API_KEY=
GEMINI_API_KEY=

# Recherche web facultative mais recommandée
TAVILY_API_KEY=

# Base SQLite
DATABASE_URL=sqlite:///./db/cities.db

# Streamlit
STREAMLIT_SERVER_PORT=8501
STREAMLIT_SERVER_ADDRESS=0.0.0.0

# Ingestion
CITYMATCH_COMMUNE_SEUIL=20000
LOAD_DEMO_DATA=0

# Application
APP_NAME=CityMatch
APP_VERSION=1.0.0
MAX_CITIES_IN_REPORT=10
LOG_LEVEL=INFO
```

Les secrets ne doivent pas être versionnés. Le fichier `.env` doit rester ignoré par Git.

---

## 🚀 Lancer le projet avec Docker

Docker est le mode recommandé pour la démo.

### 1. Construire et lancer l’application

```bash
docker compose up --build
```

Puis ouvrir :

```text
http://localhost:8501
```

### 2. Lancer en arrière-plan

```bash
docker compose up -d --build
```

### 3. Voir les logs

```bash
docker compose logs -f
```

### 4. Arrêter l’application

```bash
docker compose down
```

### 5. Supprimer aussi les volumes

Attention : cette commande supprime la base Docker, le cache et le vectorstore.

```bash
docker compose down -v
```

---

## 🧱 Ingestion des données avec Docker

La base réelle peut être longue à construire selon les sources et le nombre de communes.

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

## 📚 RAG documentaire

Les documents utilisés par le RAG peuvent être placés dans :

```text
data/docs/
data/pdfs/
```

Formats pris en charge :

```text
.txt
.md
.pdf
```

Après modification des documents RAG, reconstruire le vectorstore :

```bash
docker compose run --rm citymatch python -c "from agents.rag_agent import reset_vectorstore, build_vectorstore; reset_vectorstore(); build_vectorstore(force_rebuild=True)"
```

---

## 🧪 Commandes Docker utiles

### Ouvrir un shell dans le conteneur

```bash
docker compose run --rm citymatch bash
```

### Exécuter une commande Python

```bash
docker compose run --rm citymatch python --version
```

### Vérifier que Streamlit répond

```bash
curl http://localhost:8501/_stcore/health
```

### Reconstruire l’image sans cache

```bash
docker compose build --no-cache
docker compose up
```

---

## 💻 Développement local sans Docker

Le projet peut aussi être lancé localement.

### Créer le venv

```bash
python -m venv .venv
```

### Activer le venv

Linux / macOS :

```bash
source .venv/bin/activate
```

Windows PowerShell :

```powershell
.\.venv\Scripts\Activate.ps1
```

Windows Git Bash :

```bash
source .venv/Scripts/activate
```

### Installer les dépendances

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Lancer l’application

```bash
streamlit run ui/app.py
```

---

## 🧪 Commandes utiles hors Docker

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
  graph/*.py \
  ui/app.py \
  ui/components/*.py \
  ui/services/*.py \
  db/models.py \
  config/settings.py \
  utils/*.py
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

## 🔍 Auditer la base de données

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
    cur.execute(f'''
        SELECT COUNT(*), SUM({col} IS NULL), MIN({col}), AVG({col}), MAX({col})
        FROM cities
    ''')
    print(col, cur.fetchone())

conn.close()
PY
```

---

## 📁 Structure du projet

```text
citymatch/
  agents/                  # Agents IA
    common/                # Fonctions partagées
    database/              # Filtres SQL, repository, fallback
    profile/               # Parsing et règles de profil utilisateur
    reporting/             # Génération de rapports
  config/                  # Configuration centralisée
  data/                    # Ingestion, cache, docs
    docs/                  # Documents texte pour le RAG
    pdfs/                  # Documents PDF pour le RAG
    ingest/
      sources/
  db/                      # Modèles SQLAlchemy + base SQLite locale
  graph/                   # LangGraph state + orchestrator
  reports/output/          # Rapports générés
  ui/                      # Interface Streamlit
    components/
    services/
  utils/                   # Sécurité, validation, sérialisation
  vectorstore/             # ChromaDB pour le RAG

  Dockerfile
  docker-compose.yml
  docker-entrypoint.sh
  requirements.txt
  README.md
```

---

## 🗂️ Dossiers générés

Ces dossiers peuvent être créés automatiquement :

```text
db/cities.db
data/cache/
reports/output/
vectorstore/
logs/
```

Ils ne doivent pas forcément être versionnés dans Git.

---

## 🔐 Sécurité et limites

CityMatch applique plusieurs garde-fous :

- entrées utilisateur nettoyées avant l’appel LLM ;
- détection simple de prompt injection ;
- validation des critères par whitelist ;
- requêtes SQL paramétrées ;
- secrets chargés depuis `.env` ;
- noms de fichiers sécurisés ;
- contexte RAG marqué comme non fiable ;
- limitation de longueur des entrées et des contextes.

Limites importantes :

- les données publiques peuvent avoir un décalage temporel ;
- certaines communes ont des données incomplètes ;
- la qualité de l’air n’est pas disponible partout ;
- le climat est indicatif et agrégé ;
- le scoring aide à comparer, mais ne remplace pas une visite, une étude immobilière réelle ou l’analyse des transports quotidiens ;
- les critères subjectifs non mesurés, comme « ambiance » ou « nature proche », ne sont pas scorés artificiellement.

---

## 🧭 Exemples de demandes utilisateur

```text
On est un couple de 25 ans sans enfants, on cherche une ville bord de mer à moins de 30 km, air correct, sécurisée. Budget 200 000€ pour une maison.
```

```text
Famille avec 2 enfants en primaire, on cherche une ville avec de bonnes écoles, proche de Lyon, budget 350 000€.
```

```text
J'ai 67 ans, je cherche une ville avec beaucoup de médecins et spécialistes, climat doux, pas trop chère. Bretagne ou Sud.
```

```text
Je télétravaille 100%, je veux fibre obligatoire, ville calme moins de 50 000 habitants, prix immobilier bas, et si possible un cadre naturel.
```

---

## 🧯 Dépannage

### Le conteneur est `unhealthy`

Vérifier que l’application répond :

```bash
docker compose logs -f
curl http://localhost:8501/_stcore/health
```

Le `Dockerfile` installe `curl`, utilisé par le healthcheck.

### La base est vide

Lancer l’ingestion :

```bash
docker compose run --rm citymatch python data/ingest_real_data.py --workers 8
```

Pour une vérification rapide :

```bash
docker compose run --rm citymatch python data/ingest_real_data.py --test --workers 2
```

### Le LLM ne répond pas

Vérifier qu’au moins une clé est configurée dans `.env` :

```env
GROQ_API_KEY=
GOOGLE_API_KEY=
GEMINI_API_KEY=
```

### Le RAG ne trouve rien

Ajouter des documents dans `data/docs/` ou `data/pdfs/`, puis reconstruire le vectorstore :

```bash
docker compose run --rm citymatch python -c "from agents.rag_agent import reset_vectorstore, build_vectorstore; reset_vectorstore(); build_vectorstore(force_rebuild=True)"
```

### Les dépendances LangGraph échouent

Recréer l’environnement :

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

Si le problème concerne le checkpoint SQLite, vérifier la version installée :

```bash
python -c "import langgraph_checkpoint_sqlite; print('ok')"
```

---

## 📌 Résumé des commandes principales

```bash
# Lancer l’application avec Docker
docker compose up --build

# Ingestion complète avec Docker
docker compose run --rm citymatch python data/ingest_real_data.py --workers 8

# Ingestion test avec Docker
docker compose run --rm citymatch python data/ingest_real_data.py --test --workers 2

# Générer l’index communes
docker compose run --rm citymatch python data/build_communes_index.py --seuil 20000

# Reconstruire le vectorstore RAG
docker compose run --rm citymatch python -c "from agents.rag_agent import reset_vectorstore, build_vectorstore; reset_vectorstore(); build_vectorstore(force_rebuild=True)"

# Arrêter
docker compose down

# Logs
docker compose logs -f

# Lancer localement sans Docker
streamlit run ui/app.py
```

---

## 🎓 Contexte académique

CityMatch est développé dans le cadre d’un projet de Master 2 IA & Cybersécurité portant sur la conception d’un système d’IA agentique.

Le projet met en avant :

- la collaboration de plusieurs agents spécialisés ;
- l’utilisation d’outils externes ;
- la mémoire de session ;
- la restitution structurée ;
- la sécurité et les limites d’un système agentique.
