"""
tools/convert_docs_to_pdf.py
────────────────────────────
Convertit les documents méthodologiques texte/markdown de data/docs/
en PDF dans data/pdfs/.

Usage :
    python tools/convert_docs_to_pdf.py

Résultat :
    data/pdfs/guide_selection_ville.pdf
    data/pdfs/methodologie_bpe.pdf
    ...
"""

from __future__ import annotations

import argparse
import html
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR = PROJECT_ROOT / "data" / "docs"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "pdfs"

SUPPORTED_EXTENSIONS = {".txt", ".md"}


def clean_text_for_pdf(text: str) -> str:
    """Nettoie les caractères problématiques pour les polices PDF standard."""
    replacements = {
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "–": "-",
        "—": "-",
        "→": "->",
        "←": "<-",
        "•": "-",
        "…": "...",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    # Conserve les accents français, remplace seulement les caractères non compatibles.
    return text.encode("cp1252", errors="replace").decode("cp1252")


def build_styles() -> dict[str, ParagraphStyle]:
    """Construit les styles ReportLab."""
    base = getSampleStyleSheet()

    styles = {
        "title": ParagraphStyle(
            "CityMatchTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            spaceAfter=18,
        ),
        "heading1": ParagraphStyle(
            "CityMatchHeading1",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=18,
            spaceBefore=12,
            spaceAfter=8,
        ),
        "heading2": ParagraphStyle(
            "CityMatchHeading2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=16,
            spaceBefore=10,
            spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "CityMatchBody",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            spaceAfter=6,
        ),
        "bullet": ParagraphStyle(
            "CityMatchBullet",
            parent=base["BodyText"],
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            leftIndent=14,
            firstLineIndent=-8,
            spaceAfter=4,
        ),
    }

    return styles


def line_to_flowable(line: str, styles: dict[str, ParagraphStyle]) -> Paragraph | Spacer:
    """Convertit une ligne texte/markdown simple en élément PDF."""
    stripped = line.strip()

    if not stripped:
        return Spacer(1, 0.25 * cm)

    stripped = clean_text_for_pdf(stripped)

    if stripped.startswith("# "):
        return Paragraph(html.escape(stripped[2:].strip()), styles["heading1"])

    if stripped.startswith("## "):
        return Paragraph(html.escape(stripped[3:].strip()), styles["heading2"])

    if stripped.startswith("### "):
        return Paragraph(html.escape(stripped[4:].strip()), styles["heading2"])

    if stripped.startswith(("- ", "* ")):
        return Paragraph(f"- {html.escape(stripped[2:].strip())}", styles["bullet"])

    return Paragraph(html.escape(stripped), styles["body"])


def convert_text_file_to_pdf(source_path: Path, output_dir: Path) -> Path:
    """Convertit un fichier .txt/.md en PDF."""
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"{source_path.stem}.pdf"
    styles = build_styles()

    text = source_path.read_text(encoding="utf-8", errors="replace")
    text = clean_text_for_pdf(text)

    document = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        title=source_path.stem,
        author="CityMatch",
    )

    story = [
        Paragraph(html.escape(source_path.stem.replace("_", " ").title()), styles["title"]),
        Spacer(1, 0.3 * cm),
    ]

    for line in text.splitlines():
        story.append(line_to_flowable(line, styles))

    document.build(story)

    return output_path


def convert_all_documents(source_dir: Path, output_dir: Path) -> list[Path]:
    """Convertit tous les documents texte/markdown en PDF."""
    if not source_dir.exists():
        raise FileNotFoundError(f"Dossier introuvable : {source_dir}")

    source_files = sorted(
        path
        for path in source_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )

    converted_files: list[Path] = []

    for source_path in source_files:
        output_path = convert_text_file_to_pdf(source_path, output_dir)
        converted_files.append(output_path)
        print(f"✅ {source_path.name} -> {output_path.relative_to(PROJECT_ROOT)}")

    return converted_files


def build_parser() -> argparse.ArgumentParser:
    """Construit le parser CLI."""
    parser = argparse.ArgumentParser(description="Convertit les docs CityMatch en PDF.")
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE_DIR,
        help="Dossier contenant les fichiers .txt/.md.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Dossier de sortie des PDF.",
    )
    return parser


def main() -> None:
    """Point d'entrée CLI."""
    args = build_parser().parse_args()

    converted_files = convert_all_documents(
        source_dir=args.source,
        output_dir=args.output,
    )

    print("")
    print(f"PDF générés : {len(converted_files)}")
    print(f"Dossier : {args.output}")


if __name__ == "__main__":
    main()