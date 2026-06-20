"""
agents/rag_agent.py
────────────────────
Agent RAG (Retrieval Augmented Generation) de CityMatch.

Rôle :
- indexer les documents de méthodologie présents dans data/docs/ et data/pdfs/ ;
- récupérer un contexte documentaire quand l'utilisateur pose une question sur
  les sources, les critères ou la méthode ;
- stocker ce contexte dans l'état LangGraph.

Important :
ce module ne fait pas le scoring et ne modifie pas les critères utilisateur.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Final

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config.settings import EMBEDDING_MODEL, PDF_DIR, VECTORSTORE_DIR
from db.models import AgentLog, SessionLocal
from graph.state import CityMatchState
from utils.security import sanitize_untrusted_context
from utils.serialization import to_python


logger = logging.getLogger(__name__)

RAG_AGENT_NAME: Final[str] = "RAGAgent"
RAG_AGENT_ACTION: Final[str] = "retrieve_context"
COLLECTION_NAME: Final[str] = "citymatch_docs"

SUPPORTED_EXTENSIONS: Final[frozenset[str]] = frozenset({".txt", ".md", ".pdf"})
MAX_AGENT_TRACE_ENTRIES: Final[int] = 200
DEFAULT_RETRIEVAL_K: Final[int] = 4
CHUNK_SIZE: Final[int] = 900
CHUNK_OVERLAP: Final[int] = 120

RAG_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
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
        "sécurité",
        "securite",
        "criminalité",
        "criminalite",
        "fibre",
        "prix immobilier",
        "chômage",
        "chomage",
        "pdf",
        "document",
        "rag",
    }
)

_vectorstore = None
_embeddings = None


def _append_agent_trace(state: CityMatchState, message: str) -> None:
    """Ajoute une trace courte dans l'état LangGraph."""
    trace = list(state.get("agent_trace") or [])
    trace.append(message)
    state["agent_trace"] = trace[-MAX_AGENT_TRACE_ENTRIES:]


def get_document_dirs() -> tuple[Path, ...]:
    """
    Retourne les dossiers documentaires à scanner pour le RAG.

    Le RAG prend explicitement en compte :
    - data/docs/ pour les fichiers texte / markdown ;
    - data/pdfs/ pour les fichiers PDF.
    """
    directories: list[Path] = []

    try:
        from config.settings import DOCS_DIR

        directories.append(Path(DOCS_DIR))
    except ImportError:
        logger.debug("DOCS_DIR non défini dans config.settings")
    except Exception:
        logger.exception("Impossible de lire DOCS_DIR depuis config.settings")

    pdf_dir = Path(PDF_DIR)
    data_docs = pdf_dir.parent / "docs"
    directories.extend([data_docs, pdf_dir])

    unique_directories: list[Path] = []
    seen: set[Path] = set()

    for directory in directories:
        try:
            resolved = directory.resolve()
        except OSError:
            resolved = directory

        if resolved in seen:
            continue

        unique_directories.append(directory)
        seen.add(resolved)

    return tuple(unique_directories)


def discover_rag_files() -> list[Path]:
    """
    Retourne tous les documents indexables par le RAG.

    Formats acceptés : .txt, .md, .pdf.
    Dossiers scannés : data/docs/ et data/pdfs/.
    """
    files: list[Path] = []
    seen: set[Path] = set()

    for directory in get_document_dirs():
        directory = Path(directory)

        if not directory.exists():
            logger.info("Dossier documentaire RAG absent : %s", directory)
            continue

        for filepath in sorted(directory.rglob("*")):
            if not filepath.is_file():
                continue

            if filepath.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue

            try:
                resolved = filepath.resolve()
            except OSError:
                resolved = filepath

            if resolved in seen:
                continue

            files.append(filepath)
            seen.add(resolved)

    logger.info("Fichiers RAG découverts : %s", len(files))
    return files


def get_embeddings():
    """Retourne le modèle d'embeddings local, chargé en singleton."""
    global _embeddings

    if _embeddings is None:
        from langchain_community.embeddings import HuggingFaceEmbeddings

        logger.info("Chargement du modèle d'embeddings : %s", EMBEDDING_MODEL)

        _embeddings = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )

    return _embeddings


def read_pdf_text(filepath: Path) -> str:
    """
    Extrait le texte d'un PDF avec pypdf.

    Les numéros de page sont ajoutés pour améliorer la traçabilité du contexte RAG.
    """
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(filepath))
    except Exception:
        logger.exception("Impossible d'ouvrir le PDF RAG : %s", filepath)
        return ""

    pages: list[str] = []

    for page_index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            logger.exception("Impossible d'extraire la page %s du PDF %s", page_index, filepath)
            continue

        text = text.strip()

        if text:
            pages.append(f"[Page {page_index}]\n{text}")

    return "\n\n".join(pages).strip()


def read_document_text(filepath: Path) -> str:
    """Lit un fichier supporté et retourne son contenu texte."""
    try:
        suffix = filepath.suffix.lower()

        if suffix == ".pdf":
            return read_pdf_text(filepath)

        if suffix in {".txt", ".md"}:
            return filepath.read_text(encoding="utf-8", errors="replace").strip()

        return ""

    except Exception:
        logger.exception("Impossible de lire le document RAG : %s", filepath)
        return ""


def load_documents() -> list[Document]:
    """
    Charge tous les documents texte / Markdown / PDF du projet.

    La recherche est récursive pour accepter par exemple :
        data/docs/sources/*.md
        data/pdfs/methodologie/*.pdf
    """
    documents: list[Document] = []

    for filepath in discover_rag_files():
        text = read_document_text(filepath)

        if not text.strip():
            logger.warning("Document RAG vide ou illisible ignoré : %s", filepath)
            continue

        documents.append(
            Document(
                page_content=text,
                metadata={
                    "source": filepath.name,
                    "path": str(filepath),
                    "file_type": filepath.suffix.lower(),
                    "document_type": "pdf" if filepath.suffix.lower() == ".pdf" else "text",
                    "category": "citymatch_methodology",
                },
            )
        )

    logger.info("Documents RAG chargés : %s", len(documents))
    return documents


def load_documents_from_dir(directory: Path) -> list[Document]:
    """
    Compatibilité avec l'ancienne API.

    Charge seulement les documents d'un dossier donné.
    La construction normale du vectorstore doit utiliser load_documents().
    """
    documents: list[Document] = []
    directory = Path(directory)

    if not directory.exists():
        logger.warning("Dossier documentaire RAG absent : %s", directory)
        return documents

    for filepath in sorted(directory.rglob("*")):
        if not filepath.is_file():
            continue

        if filepath.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue

        text = read_document_text(filepath)

        if not text.strip():
            continue

        documents.append(
            Document(
                page_content=text,
                metadata={
                    "source": filepath.name,
                    "path": str(filepath),
                    "file_type": filepath.suffix.lower(),
                    "document_type": "pdf" if filepath.suffix.lower() == ".pdf" else "text",
                    "category": "citymatch_methodology",
                },
            )
        )

    logger.info("Documents RAG chargés depuis %s : %s", directory, len(documents))
    return documents


def split_documents(documents: list[Document]) -> list[Document]:
    """Découpe les documents en chunks adaptés au retrieval."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " "],
    )

    chunks = splitter.split_documents(documents)
    logger.info("Découpage RAG : %s documents -> %s chunks", len(documents), len(chunks))

    return chunks


def build_fallback_documents() -> list[Document]:
    """Document minimal si aucun fichier n'est présent."""
    logger.warning("Aucun document RAG trouvé. Utilisation d'un fallback minimal.")

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
            metadata={
                "source": "fallback",
                "category": "default",
                "file_type": "text",
                "document_type": "fallback",
            },
        )
    ]


def reset_vectorstore() -> None:
    """
    Vide le vectorstore existant sans supprimer le dossier racine.

    Important Docker :
    /app/vectorstore peut être un volume monté. Dans ce cas, supprimer le
    dossier lui-même avec shutil.rmtree('/app/vectorstore') peut échouer avec
    "Device or resource busy". On supprime donc uniquement son contenu.
    """
    global _vectorstore

    _vectorstore = None

    vectorstore_dir = Path(VECTORSTORE_DIR)
    vectorstore_dir.mkdir(parents=True, exist_ok=True)

    for child in vectorstore_dir.iterdir():
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        except FileNotFoundError:
            continue

    logger.info("Vectorstore RAG vidé : %s", vectorstore_dir)


def load_existing_vectorstore(persist_path: str):
    """Charge un vectorstore Chroma existant."""
    from langchain_community.vectorstores import Chroma

    vectorstore = Chroma(
        persist_directory=persist_path,
        embedding_function=get_embeddings(),
        collection_name=COLLECTION_NAME,
    )

    try:
        count = vectorstore._collection.count()
    except Exception:
        count = "inconnu"

    logger.info("Vectorstore RAG chargé : %s chunks", count)

    return vectorstore


def create_vectorstore(chunks: list[Document], persist_path: str):
    """Crée un vectorstore Chroma depuis des chunks."""
    from langchain_community.vectorstores import Chroma

    Path(persist_path).mkdir(parents=True, exist_ok=True)

    vectorstore = Chroma.from_documents(
        documents=chunks,
        embedding=get_embeddings(),
        persist_directory=persist_path,
        collection_name=COLLECTION_NAME,
    )

    logger.info("Vectorstore RAG créé : %s chunks indexés", len(chunks))

    return vectorstore


def build_vectorstore(force_rebuild: bool = False):
    """
    Construit ou charge le vectorstore ChromaDB.

    Args:
        force_rebuild: si True, supprime l'index existant avant reconstruction.
    """
    global _vectorstore

    if _vectorstore is not None and not force_rebuild:
        return _vectorstore

    if force_rebuild:
        reset_vectorstore()

    vectorstore_dir = Path(VECTORSTORE_DIR)
    persist_path = str(vectorstore_dir)
    chroma_db = vectorstore_dir / "chroma.sqlite3"

    if not force_rebuild and chroma_db.exists():
        _vectorstore = load_existing_vectorstore(persist_path)
        return _vectorstore

    document_dirs = get_document_dirs()
    logger.info(
        "Construction du vectorstore RAG depuis : %s",
        ", ".join(str(directory) for directory in document_dirs),
    )

    documents = load_documents()

    if not documents:
        documents = build_fallback_documents()

    chunks = split_documents(documents)

    if not chunks:
        documents = build_fallback_documents()
        chunks = split_documents(documents)

    _vectorstore = create_vectorstore(chunks, persist_path)

    return _vectorstore


def retrieve_context(question: str, k: int = DEFAULT_RETRIEVAL_K) -> str:
    """Recherche les k chunks les plus pertinents pour une question."""
    clean_question = str(question or "").strip()

    if not clean_question:
        return ""

    vectorstore = build_vectorstore()
    docs = vectorstore.similarity_search(clean_question, k=k)

    if not docs:
        return "Aucun document pertinent trouvé."

    context_parts: list[str] = []

    for doc in docs:
        source = doc.metadata.get("source", "inconnu")
        file_type = doc.metadata.get("file_type", "")
        source_label = f"{source} ({file_type})" if file_type else str(source)

        sanitized_content = sanitize_untrusted_context(
            doc.page_content,
            source_label=str(source_label),
        )
        context_parts.append(sanitized_content)

    return "\n\n---\n\n".join(context_parts)


def should_use_rag(question: str | None) -> bool:
    """Détermine si une question mérite une recherche documentaire."""
    if not question:
        return False

    question_lower = str(question).lower()
    return any(keyword in question_lower for keyword in RAG_KEYWORDS)


def run_rag_agent(state: CityMatchState) -> CityMatchState:
    """
    Nœud LangGraph : récupère un contexte documentaire si nécessaire.

    Le RAG est déclenché uniquement pour les questions portant sur les sources,
    la méthode, les critères, les indicateurs ou le calcul des scores.
    """
    start_time = time.perf_counter()

    session_id = str(state.get("session_id") or "unknown")
    question = state.get("rag_question") or state.get("user_input") or ""

    db = SessionLocal()

    log_entry = AgentLog(
        session_id=session_id,
        agent_name=RAG_AGENT_NAME,
        action=RAG_AGENT_ACTION,
        input_data=to_python(
            {
                "question": question,
                "should_use_rag": should_use_rag(question),
            }
        ),
        success=False,
    )

    try:
        if not question:
            state["rag_context"] = ""

            duration_ms = int((time.perf_counter() - start_time) * 1000)
            log_entry.output_data = {
                "rag_used": False,
                "reason": "no_question",
                "context_length": 0,
            }
            log_entry.duration_ms = duration_ms
            log_entry.success = True

            _append_agent_trace(state, f"{RAG_AGENT_NAME}: aucune question RAG")
            return state

        if not should_use_rag(question):
            state["rag_context"] = ""

            duration_ms = int((time.perf_counter() - start_time) * 1000)
            log_entry.output_data = {
                "rag_used": False,
                "reason": "not_needed",
                "context_length": 0,
            }
            log_entry.duration_ms = duration_ms
            log_entry.success = True

            _append_agent_trace(state, f"{RAG_AGENT_NAME}: recherche documentaire non nécessaire")
            return state

        context = retrieve_context(question, k=DEFAULT_RETRIEVAL_K)
        state["rag_context"] = context

        duration_ms = int((time.perf_counter() - start_time) * 1000)

        log_entry.output_data = to_python(
            {
                "rag_used": True,
                "context_length": len(context),
                "retrieval_k": DEFAULT_RETRIEVAL_K,
            }
        )
        log_entry.duration_ms = duration_ms
        log_entry.success = True

        _append_agent_trace(
            state,
            f"{RAG_AGENT_NAME}: contexte récupéré en {duration_ms} ms",
        )

    except Exception as exc:
        db.rollback()

        duration_ms = int((time.perf_counter() - start_time) * 1000)

        logger.exception("Erreur pendant l'exécution du RAGAgent")

        state["rag_context"] = ""
        state["error"] = f"{RAG_AGENT_NAME}: {exc}"

        log_entry.duration_ms = duration_ms
        log_entry.success = False
        log_entry.error_message = str(exc)

        _append_agent_trace(
            state,
            f"{RAG_AGENT_NAME}: erreur après {duration_ms} ms",
        )

    finally:
        try:
            db.add(log_entry)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Impossible d'enregistrer le log du RAGAgent")
        finally:
            db.close()

    return state


__all__ = [
    "build_vectorstore",
    "discover_rag_files",
    "get_document_dirs",
    "load_documents",
    "read_document_text",
    "reset_vectorstore",
    "retrieve_context",
    "run_rag_agent",
    "should_use_rag",
]
