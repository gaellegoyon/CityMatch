"""
agents/rag_agent.py
────────────────────
Agent RAG (Retrieval Augmented Generation) de CityMatch.

Rôle :
- indexer les documents de méthodologie présents dans data/docs/ ou data/pdfs/ ;
- récupérer un contexte documentaire quand l'utilisateur pose une question sur
  les sources, les critères ou la méthode ;
- stocker ce contexte dans l'état LangGraph.

Important :
ce module ne fait pas le scoring et ne modifie pas les critères utilisateur.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from rich.console import Console

from config.settings import EMBEDDING_MODEL, PDF_DIR, VECTORSTORE_DIR
from graph.state import CityMatchState
from utils.security import sanitize_untrusted_context


console = Console()

COLLECTION_NAME = "citymatch_docs"
SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf"}

# Mots-clés pour déclencher la recherche documentaire.
RAG_KEYWORDS = {
    "comment",
    "pourquoi",
    "définition",
    "definition",
    "signifie",
    "calculé",
    "calcule",
    "méthodologie",
    "methodologie",
    "source",
    "sources",
    "données",
    "donnees",
    "indicateur",
    "mesure",
    "taux",
    "bpe",
    "insee",
    "critère",
    "critere",
    "score",
    "scoring",
    "dvf",
    "arcep",
    "atmo",
    "ssmsi",
}


# Singletons pour éviter de recharger le modèle et Chroma à chaque appel.
_vectorstore = None
_embeddings = None


def get_docs_dir() -> Path:
    """
    Retourne le dossier documentaire à utiliser.

    Priorité :
    1. config.settings.DOCS_DIR si défini ;
    2. data/docs si le dossier existe ;
    3. PDF_DIR pour compatibilité avec l'ancien projet.
    """
    try:
        from config.settings import DOCS_DIR

        docs_dir = Path(DOCS_DIR)
        if docs_dir.exists():
            return docs_dir
    except Exception:
        pass

    data_docs = Path(PDF_DIR).parent / "docs"
    if data_docs.exists():
        return data_docs

    return Path(PDF_DIR)


def get_embeddings():
    """
    Retourne le modèle d'embeddings local, chargé en singleton.

    Le modèle est multilingue et fonctionne sans API externe.
    """
    global _embeddings

    if _embeddings is None:
        from langchain_community.embeddings import HuggingFaceEmbeddings

        console.print(f"[dim]Chargement embeddings : {EMBEDDING_MODEL}...[/dim]")
        _embeddings = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )

    return _embeddings


def load_documents_from_dir(directory: Path) -> list[Document]:
    """
    Charge tous les documents texte / Markdown / PDF du dossier fourni.
    """
    documents: list[Document] = []

    if not directory.exists():
        console.print(f"[yellow]⚠️  Dossier documentaire absent : {directory}[/yellow]")
        return documents

    for filepath in sorted(directory.iterdir()):
        if not filepath.is_file() or filepath.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        text = read_document_text(filepath)
        if not text.strip():
            continue

        documents.append(
            Document(
                page_content=text,
                metadata={
                    "source": filepath.name,
                    "file_type": filepath.suffix.lower(),
                    "category": "citymatch_methodology",
                },
            )
        )
        console.print(f"[dim]  📄 {filepath.name} ({len(text)} caractères)[/dim]")

    return documents


def read_document_text(filepath: Path) -> str:
    """
    Lit un fichier supporté et retourne son contenu texte.
    """
    try:
        if filepath.suffix.lower() == ".pdf":
            return read_pdf_text(filepath)

        return filepath.read_text(encoding="utf-8", errors="replace")

    except Exception as exc:
        console.print(f"[yellow]⚠️  Impossible de lire {filepath.name} : {exc}[/yellow]")
        return ""


def read_pdf_text(filepath: Path) -> str:
    """
    Extrait le texte d'un PDF avec pypdf.
    """
    from pypdf import PdfReader

    reader = PdfReader(str(filepath))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def build_vectorstore(force_rebuild: bool = False):
    """
    Construit ou charge le vectorstore ChromaDB.

    Args:
        force_rebuild: si True, supprime l'index existant avant reconstruction.
    """
    global _vectorstore

    if force_rebuild:
        reset_vectorstore()

    persist_path = str(VECTORSTORE_DIR)
    chroma_db = VECTORSTORE_DIR / "chroma.sqlite3"

    if not force_rebuild and chroma_db.exists():
        _vectorstore = load_existing_vectorstore(persist_path)
        return _vectorstore

    docs_dir = get_docs_dir()
    console.print(f"[bold blue]📚 Construction du vectorstore RAG depuis {docs_dir}[/bold blue]")

    documents = load_documents_from_dir(docs_dir)
    if not documents:
        documents = build_fallback_documents()

    chunks = split_documents(documents)
    _vectorstore = create_vectorstore(chunks, persist_path)

    console.print(f"[green]✅ Vectorstore créé : {len(chunks)} chunks indexés[/green]")
    return _vectorstore


def load_existing_vectorstore(persist_path: str):
    """Charge un vectorstore Chroma existant."""
    from langchain_community.vectorstores import Chroma

    console.print("[dim]Chargement du vectorstore existant...[/dim]")

    vectorstore = Chroma(
        persist_directory=persist_path,
        embedding_function=get_embeddings(),
        collection_name=COLLECTION_NAME,
    )

    try:
        count = vectorstore._collection.count()
    except Exception:
        count = "?"

    console.print(f"[green]✅ Vectorstore chargé : {count} chunks[/green]")
    return vectorstore


def create_vectorstore(chunks: list[Document], persist_path: str):
    """Crée un vectorstore Chroma depuis des chunks."""
    from langchain_community.vectorstores import Chroma

    return Chroma.from_documents(
        documents=chunks,
        embedding=get_embeddings(),
        persist_directory=persist_path,
        collection_name=COLLECTION_NAME,
    )


def split_documents(documents: list[Document]) -> list[Document]:
    """Découpe les documents en chunks adaptés au retrieval."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=900,
        chunk_overlap=120,
        separators=["\n\n", "\n", ". ", " "],
    )
    chunks = splitter.split_documents(documents)
    console.print(f"[dim]{len(documents)} docs → {len(chunks)} chunks[/dim]")
    return chunks


def build_fallback_documents() -> list[Document]:
    """
    Document minimal si aucun fichier n'est présent.

    Le fallback reste aligné avec les critères fiables actuellement utilisés.
    """
    console.print("[yellow]⚠️  Aucun document trouvé. Utilisation d'un fallback minimal.[/yellow]")

    text = (
        "CityMatch compare des villes françaises à partir de données publiques fiables : "
        "INSEE, BPE, DVF, SSMSI, ARCEP, ATMO et calculs géographiques simples. "
        "Les critères incluent notamment : prix immobilier, taux de chômage, sécurité, "
        "santé, équipements BPE, distance à la mer, distance à la montagne, fibre, "
        "qualité de l'air quand disponible et climat orientatif. "
        "Les critères sans source fiable, comme un score nature incomplet ou des avis habitants "
        "subjectifs, ne sont pas utilisés dans le scoring."
    )

    return [
        Document(
            page_content=text,
            metadata={"source": "fallback", "category": "default"},
        )
    ]


def reset_vectorstore() -> None:
    """
    Supprime le vectorstore existant.

    Utile après modification des fichiers dans data/docs ou data/pdfs.
    """
    global _vectorstore

    _vectorstore = None

    if VECTORSTORE_DIR.exists():
        shutil.rmtree(VECTORSTORE_DIR)

    VECTORSTORE_DIR.mkdir(parents=True, exist_ok=True)


def retrieve_context(question: str, k: int = 4) -> str:
    """
    Recherche les k chunks les plus pertinents pour une question.
    """
    vectorstore = build_vectorstore()
    docs = vectorstore.similarity_search(question, k=k)

    if not docs:
        return "Aucun document pertinent trouvé."

    context_parts = []
    for doc in docs:
        source = doc.metadata.get("source", "inconnu")
        context_parts.append(
            sanitize_untrusted_context(doc.page_content, source_label=str(source))
        )

    return "\n\n---\n\n".join(context_parts)


def should_use_rag(question: str) -> bool:
    """
    Détermine si une question mérite une recherche documentaire.
    """
    if not question:
        return False

    question_lower = question.lower()
    return any(keyword in question_lower for keyword in RAG_KEYWORDS)


def run_rag_agent(state: CityMatchState) -> CityMatchState:
    """
    Nœud LangGraph : récupère un contexte documentaire si nécessaire.
    """
    start_time = time.time()
    console.print("\n[bold cyan]📚 RAGAgent activé[/bold cyan]")

    question = state.get("rag_question") or state.get("user_input", "")

    if not question:
        console.print("[yellow]⚠️  Aucune question RAG définie.[/yellow]")
        state["rag_context"] = ""
        return state

    if not should_use_rag(question):
        console.print("[dim]Pas de recherche RAG nécessaire pour cette question.[/dim]")
        state["rag_context"] = ""
        return state

    try:
        context = retrieve_context(question, k=4)
        state["rag_context"] = context
        console.print(f"[green]✅ Contexte RAG récupéré ({len(context)} caractères)[/green]")
    except Exception as exc:
        console.print(f"[yellow]⚠️  Erreur RAG : {exc}[/yellow]")
        state["rag_context"] = ""

    duration_ms = int((time.time() - start_time) * 1000)
    trace = list(state.get("agent_trace", []))
    trace.append(f"RAGAgent: contexte récupéré en {duration_ms} ms")
    state["agent_trace"] = trace

    return state
