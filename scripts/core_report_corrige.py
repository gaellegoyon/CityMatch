"""
agents/reporting/core.py
───────────────────────
Agent de génération de rapports CityMatch.

Produit :
- un rapport Markdown ;
- un rapport PDF ReportLab ;
- une analyse lisible des points forts et points de vigilance.

Principes :
- ne pas confondre "villes affichées" et "villes analysées" ;
- ne pas annoncer des filtres stricts si les filtres ont été relâchés ;
- ne pas afficher une bonne valeur brute comme vigilance uniquement à cause
  d'une normalisation relative ;
- générer un PDF sans balises ReportLab mal fermées.
"""

from __future__ import annotations

import html
import io
import math
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import AVAILABLE_CRITERIA, MAX_CITIES_IN_REPORT, REPORTS_DIR
from graph.state import CityMatchState
from rich.console import Console


console = Console()


# ─────────────────────────────────────────────────────────────────────────────
# Temps local
# ─────────────────────────────────────────────────────────────────────────────
def _now_paris() -> datetime:
    """Retourne l'heure locale Europe/Paris."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Europe/Paris")).replace(tzinfo=None)
    except Exception:
        try:
            import pytz

            return datetime.now(pytz.timezone("Europe/Paris")).replace(tzinfo=None)
        except Exception:
            utc = datetime.now(timezone.utc).replace(tzinfo=None)
            return utc + timedelta(hours=2 if 4 <= utc.month <= 10 else 1)


# ─────────────────────────────────────────────────────────────────────────────
# Template Markdown
# ─────────────────────────────────────────────────────────────────────────────
MARKDOWN_TEMPLATE = """# 🏙️ CityMatch — Rapport de Recommandation de Villes

**Généré le :** {date}
**Session :** {session_id}
**Profil :** {profil}

---

## 📋 Résumé Exécutif

{resume_executif}

---

## 🏆 Classement des Villes Recommandées

| Rang | Ville | Région | Score | Population | Chômage | Prix m² |
|------|-------|--------|-------|------------|---------|---------|
{tableau_villes}

---

## 📊 Analyse Détaillée

{analyses_detaillees}

---

## 🔍 Critères Utilisés

| Critère | Poids | Description |
|---------|-------|-------------|
{tableau_criteres}

---

## 📚 Sources de Données

- **INSEE — Base Permanente des Équipements (BPE 2024)**
  Crèches, écoles, médecins, transports
- **INSEE — Recensement de la Population**
  Taux de chômage, démographie, revenus, logements
- **DVF — Demandes de Valeurs Foncières** (data.gouv.fr)
  Prix immobiliers réels issus des transactions enregistrées
- **Ministère de l'Intérieur / SSMSI** (data.gouv.fr)
  Statistiques de criminalité par commune
- **ARCEP**
  Couverture fibre par commune
- **ATMO / associations régionales agréées**
  Qualité de l'air quand disponible

## ⚠️ Limites & Avertissements

- Les données publiques peuvent avoir un décalage de 1 à 2 ans selon les sources
- Les scores sont relatifs à l'échantillon de villes candidates
- Une valeur manquante ne signifie pas une mauvaise performance
- Ce rapport est une aide à la décision, pas une vérité absolue
- Toujours compléter par une visite sur place, une recherche immobilière réelle et l'analyse des transports quotidiens

---

*Rapport généré par CityMatch v1.0*
"""


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de formatage
# ─────────────────────────────────────────────────────────────────────────────
def _fmt_value(raw, unit: str, critere_key: str) -> str:
    """Formate une valeur brute lisiblement selon le type de critère."""
    if raw is None:
        return "N/A"

    try:
        v = float(raw)
    except (TypeError, ValueError):
        return str(raw)

    if "km" in unit or "distance" in critere_key:
        return f"{v:.0f} km"
    if "pct" in critere_key or "taux" in critere_key or "%" in unit:
        return f"{v:.1f}%"
    if "m2" in critere_key or "immo" in critere_key:
        return f"{v:,.0f} €/m²"
    if "pour_1000" in critere_key:
        return f"{v:.2f} ‰"
    if "score" in critere_key or "/10" in unit:
        return f"{v:.1f}/10"
    if "h_an" in critere_key:
        return f"{v:.0f} h/an"
    if "mm" in unit:
        return f"{v:.0f} mm"
    if "°C" in unit:
        return f"{v:.1f}°C"

    return f"{v:.1f} {unit}".strip()


def _markdown_inline_to_reportlab(text: str) -> str:
    """
    Convertit un markdown inline minimal vers un markup ReportLab valide.

    Corrige notamment le bug de l'ancien code :
        line.replace("**", "<b>").replace("**", "</b>")
    qui transformait toutes les balises en <b> sans jamais les fermer.
    """
    escaped = html.escape(text)

    # **texte** → <b>texte</b>
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)

    # *texte* → <i>texte</i>, sans toucher aux doubles astérisques déjà traités.
    escaped = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", escaped)

    return escaped


def _safe_score(value) -> float | None:
    """Convertit un score normalisé nullable."""
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _raw_float(value) -> float | None:
    """Convertit une valeur brute nullable."""
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _is_real_vigilance(criterion_key: str, raw_value, normalized_score) -> bool:
    """
    Détermine si un critère doit vraiment apparaître en point de vigilance.

    On évite d'afficher une bonne valeur brute comme critique uniquement parce
    qu'elle est moins bonne que les meilleures villes de l'échantillon.
    """
    score = _safe_score(normalized_score)
    raw = _raw_float(raw_value)

    if score is None:
        return False

    if criterion_key == "qualite_air_score":
        # 7/10 ou 8/10 est correct et ne doit pas apparaître comme critique.
        return raw is not None and raw < 7

    if criterion_key == "score_securite":
        return raw is not None and raw < 5.5

    if criterion_key == "distance_mer_km":
        return raw is not None and raw > 30

    if criterion_key == "distance_montagne_km":
        return raw is not None and raw > 80

    if criterion_key == "fibre_pct":
        return raw is not None and raw < 80

    if criterion_key == "prix_immo_m2":
        # Pour le prix, la valeur brute dépend du budget : le score normalisé reste utile.
        return score < 4

    return score < 4


def _is_real_strength(criterion_key: str, raw_value, normalized_score) -> bool:
    """Détermine si un critère peut être affiché comme point fort."""
    score = _safe_score(normalized_score)
    raw = _raw_float(raw_value)

    if score is None:
        return False

    if criterion_key == "qualite_air_score":
        return raw is not None and raw >= 7

    if criterion_key == "score_securite":
        return raw is not None and raw >= 6

    return score >= 6


# ─────────────────────────────────────────────────────────────────────────────
# Sections Markdown
# ─────────────────────────────────────────────────────────────────────────────
def build_resume_executif(
    top_cities: list[dict],
    user_criteria: dict,
    candidate_count: int | None = None,
) -> str:
    """Génère un résumé exécutif en langage naturel."""
    if not top_cities:
        return "Aucune ville trouvée correspondant à vos critères."

    best = top_cities[0]
    profil = user_criteria.get("profil", "inconnu")
    pref_texte = user_criteria.get("preferences_texte", "")

    display_count = len(top_cities)
    candidate_count = candidate_count or display_count

    criteres = user_criteria.get("criteres", {})
    top_criteres = sorted(criteres.items(), key=lambda x: x[1], reverse=True)[:3]
    criteres_texte = ", ".join(
        AVAILABLE_CRITERIA.get(k, {}).get("label", k)
        for k, _ in top_criteres
    ) or "vos critères"

    details_best = []
    if best.get("distance_mer_km") is not None:
        details_best.append(f"à **{best['distance_mer_km']:.0f} km de la mer**")
    if best.get("prix_immo_m2") is not None:
        details_best.append(f"prix immobilier moyen **{best['prix_immo_m2']:,.0f} €/m²**")
    if best.get("taux_chomage") is not None:
        details_best.append(f"taux de chômage **{best['taux_chomage']:.1f}%**")
    if best.get("score_securite") is not None:
        details_best.append(f"score sécurité **{best['score_securite']:.1f}/10**")
    if best.get("qualite_air_score") is not None:
        details_best.append(f"qualité de l'air **{best['qualite_air_score']:.1f}/10**")

    details_str = " — ".join(details_best)

    return f"""Sur la base de votre profil **{profil}**, {candidate_count} villes candidates ont été analysées,
dont les {display_count} meilleures sont présentées ci-dessous.

Le classement tient compte de vos critères prioritaires : **{criteres_texte}**.

**La ville recommandée en premier choix est {best['nom']}** ({best.get('region', '?')})
avec un score global de **{best['total_score']:.1f}/100**.
{details_str}

{pref_texte if pref_texte else ''}

Les premières villes sont les meilleurs compromis trouvés selon vos critères.
Certains filtres peuvent être relâchés progressivement lorsqu'ils sont trop restrictifs.
Consultez l'analyse détaillée ci-dessous pour affiner votre choix."""


def build_tableau_villes(top_cities: list[dict]) -> str:
    """Génère les lignes du tableau Markdown des villes."""
    rows = []

    for city in top_cities:
        chomage = city.get("taux_chomage")
        prix = city.get("prix_immo_m2")

        rows.append(
            f"| #{city.get('rank', '?')} | **{city.get('nom', '?')}** | {city.get('region', '?')} | "
            f"**{city.get('total_score', 0):.1f}/100** | {city.get('population', 0):,} | "
            f"{chomage:.1f}% | " if chomage is not None else
            f"| #{city.get('rank', '?')} | **{city.get('nom', '?')}** | {city.get('region', '?')} | "
            f"**{city.get('total_score', 0):.1f}/100** | {city.get('population', 0):,} | N/A | "
        )

        rows[-1] += f"{prix:,.0f} €/m² |" if prix is not None else "N/A |"

    return "\n".join(rows)


def build_analyse_ville(city: dict) -> str:
    """Génère l'analyse détaillée d'une ville."""
    score_details = city.get("score_details", {})

    sorted_criteres = sorted(
        score_details.items(),
        key=lambda x: x[1].get("normalized_score") or 0,
        reverse=True,
    )

    points_forts = [
        (k, d)
        for k, d in sorted_criteres
        if d.get("has_data", True)
        and d.get("normalized_score") is not None
        and _is_real_strength(k, d.get("raw_value"), d.get("normalized_score"))
    ][:5]

    points_faibles = [
        (k, d)
        for k, d in sorted_criteres
        if d.get("has_data", True)
        and d.get("normalized_score") is not None
        and d.get("raw_value") not in (None, 0, 0.0)
        and _is_real_vigilance(k, d.get("raw_value"), d.get("normalized_score"))
    ][:3]

    forts_text = "\n".join(
        f"  - ✅ **{d['label']}** : {_fmt_value(d.get('raw_value'), d.get('unit', ''), ck)} "
        f"(score {d.get('normalized_score', 0):.1f}/10)"
        for ck, d in points_forts
    )

    faibles_text = "\n".join(
        f"  - ⚠️  **{d['label']}** : {_fmt_value(d.get('raw_value'), d.get('unit', ''), ck)} "
        f"(score {d.get('normalized_score', 0):.1f}/10)"
        for ck, d in points_faibles
    )

    web_insights = city.get("web_insights", "")
    web_section = (
        f"\n**Informations récentes :** {web_insights[:300]}..."
        if web_insights
        else ""
    )

    return f"""### #{city.get('rank', '?')} — {city.get('nom', '?')} ({city.get('region', '?')})

> **Score global : {city.get('total_score', 0):.1f}/100** | Population : {city.get('population', 0):,} hab.

**Points forts :**
{forts_text if forts_text else "  - Performances globalement équilibrées"}

**Points de vigilance :**
{faibles_text if faibles_text else "  - Aucun point critique détecté"}
{web_section}

---
"""


def build_tableau_criteres(user_criteria: dict) -> str:
    """Génère le tableau Markdown des critères utilisateur."""
    criteres = user_criteria.get("criteres", {})

    return "\n".join(
        f"| {AVAILABLE_CRITERIA.get(k, {}).get('label', k)} | {'⭐' * int(v)} ({int(v)}/5) | "
        f"{AVAILABLE_CRITERIA.get(k, {}).get('description', '?')} |"
        for k, v in sorted(criteres.items(), key=lambda x: x[1], reverse=True)
    )


def generate_markdown_report(state: CityMatchState) -> str:
    """Génère le rapport complet en Markdown."""
    top_cities = state.get("top_cities", [])[:MAX_CITIES_IN_REPORT]
    scored_cities = state.get("scored_cities") or top_cities
    user_criteria = state.get("user_criteria", {})
    session_id = state.get("session_id", "unknown")

    return MARKDOWN_TEMPLATE.format(
        date=_now_paris().strftime("%d/%m/%Y à %H:%M"),
        session_id=session_id[:8] + "...",
        profil=user_criteria.get("profil", "Non défini"),
        resume_executif=build_resume_executif(
            top_cities=top_cities,
            user_criteria=user_criteria,
            candidate_count=len(scored_cities),
        ),
        tableau_villes=build_tableau_villes(top_cities),
        analyses_detaillees="\n".join(build_analyse_ville(c) for c in top_cities),
        tableau_criteres=build_tableau_criteres(user_criteria),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Graphique radar
# ─────────────────────────────────────────────────────────────────────────────
def generate_radar_chart(top_cities: list[dict], user_criteria: dict) -> bytes | None:
    """Génère un graphique radar comparant les TOP 5 villes en PNG."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np  # noqa: F401

        criteres = user_criteria.get("criteres", {})
        if not criteres or not top_cities:
            return None

        top_criteres = sorted(criteres.items(), key=lambda x: x[1], reverse=True)[:7]
        labels = [
            AVAILABLE_CRITERIA.get(k, {}).get("label", k)[:20]
            for k, _ in top_criteres
        ]

        n_labels = len(labels)
        if n_labels < 3:
            return None

        angles = [n / float(n_labels) * 2 * math.pi for n in range(n_labels)]
        angles += angles[:1]

        colors_list = ["#4299e1", "#48bb78", "#ed8936", "#9f7aea", "#f56565"]

        fig, ax = plt.subplots(figsize=(7, 6), subplot_kw={"projection": "polar"})
        ax.set_facecolor("#f8fafc")
        fig.patch.set_facecolor("white")

        for idx, city in enumerate(top_cities[:5]):
            details = city.get("score_details", {})
            values = []

            for key, _ in top_criteres:
                detail = details.get(key, {})
                value = detail.get("normalized_score", 5.0) if detail else 5.0
                values.append(float(value))

            values += values[:1]
            color = colors_list[idx % len(colors_list)]

            ax.plot(
                angles,
                values,
                "o-",
                linewidth=2,
                color=color,
                label=city.get("nom", f"Ville {idx + 1}"),
                alpha=0.9,
            )
            ax.fill(angles, values, alpha=0.08, color=color)

        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(labels, size=8, color="#2d3748")
        ax.set_ylim(0, 10)
        ax.set_yticks([2, 4, 6, 8, 10])
        ax.set_yticklabels(["2", "4", "6", "8", "10"], size=7, color="#718096")
        ax.grid(color="#e2e8f0", linestyle="--", linewidth=0.7, alpha=0.8)
        ax.spines["polar"].set_color("#cbd5e0")
        ax.set_title(
            "Comparaison des villes — critères pondérés",
            size=11,
            color="#1a365d",
            pad=20,
            fontweight="bold",
        )
        ax.legend(
            loc="upper right",
            bbox_to_anchor=(1.35, 1.15),
            fontsize=8,
            framealpha=0.9,
            edgecolor="#cbd5e0",
        )

        plt.tight_layout()

        buf = io.BytesIO()
        fig.savefig(
            buf,
            format="png",
            dpi=150,
            bbox_inches="tight",
            facecolor="white",
            edgecolor="none",
        )
        buf.seek(0)
        png_bytes = buf.read()
        plt.close(fig)
        return png_bytes

    except ImportError:
        console.print("[yellow]⚠️  matplotlib non disponible — radar ignoré[/yellow]")
        return None
    except Exception as exc:
        console.print(f"[yellow]⚠️  Erreur radar : {exc}[/yellow]")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# PDF
# ─────────────────────────────────────────────────────────────────────────────
def generate_pdf_report(
    markdown_content: str,
    output_path: Path,
    top_cities: list | None = None,
    user_criteria: dict | None = None,
) -> bool:
    """Convertit le rapport Markdown en PDF avec ReportLab."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            HRFlowable,
            Image as RLImage,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
        )

        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            rightMargin=2 * cm,
            leftMargin=2 * cm,
            topMargin=2.5 * cm,
            bottomMargin=2 * cm,
        )

        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            "CityTitle",
            parent=styles["Title"],
            fontSize=20,
            textColor=colors.HexColor("#1a365d"),
            spaceAfter=12,
        )
        h1_style = ParagraphStyle(
            "CityH1",
            parent=styles["Heading1"],
            fontSize=14,
            textColor=colors.HexColor("#2b6cb0"),
            spaceBefore=16,
            spaceAfter=8,
        )
        h2_style = ParagraphStyle(
            "CityH2",
            parent=styles["Heading2"],
            fontSize=11,
            textColor=colors.HexColor("#2c5282"),
        )
        body_style = ParagraphStyle(
            "CityBody",
            parent=styles["Normal"],
            fontSize=9,
            leading=14,
            spaceAfter=6,
        )

        story = [
            Paragraph("CityMatch — Rapport de Recommandation", title_style),
            Paragraph(
                f"<b>Généré le :</b> {_now_paris().strftime('%d/%m/%Y à %H:%M')}",
                body_style,
            ),
            HRFlowable(width="100%", thickness=2, color=colors.HexColor("#2b6cb0")),
            Spacer(1, 0.5 * cm),
        ]

        if top_cities and user_criteria:
            radar_png = generate_radar_chart(top_cities, user_criteria)
            if radar_png:
                radar_buf = io.BytesIO(radar_png)
                img = RLImage(radar_buf, width=12 * cm, height=10 * cm)
                story.append(Paragraph("Comparaison radar des TOP villes", h1_style))
                story.append(img)
                story.append(Spacer(1, 0.5 * cm))
                story.append(
                    Paragraph(
                        "<i>Axes = critères pondérés par l'utilisateur. "
                        "Valeurs normalisées 0–10 (10 = optimal).</i>",
                        body_style,
                    )
                )
                story.append(
                    HRFlowable(
                        width="100%",
                        thickness=1,
                        color=colors.HexColor("#e2e8f0"),
                    )
                )
                story.append(Spacer(1, 0.3 * cm))

        for line in markdown_content.split("\n"):
            line = line.strip()

            if not line or line.startswith("---"):
                story.append(Spacer(1, 0.25 * cm))
                continue

            if line.startswith("| "):
                # Les tableaux Markdown sont ignorés dans la conversion PDF simplifiée.
                continue

            if line.startswith("# "):
                story.append(Paragraph(_markdown_inline_to_reportlab(line[2:]), title_style))
            elif line.startswith("## "):
                story.append(Paragraph(_markdown_inline_to_reportlab(line[3:]), h1_style))
            elif line.startswith("### "):
                story.append(Paragraph(_markdown_inline_to_reportlab(line[4:]), h2_style))
            elif line.startswith("- ") or line.startswith("  - "):
                clean = line.lstrip("- ").strip()
                story.append(Paragraph(f"• {_markdown_inline_to_reportlab(clean)}", body_style))
            else:
                story.append(Paragraph(_markdown_inline_to_reportlab(line), body_style))

        doc.build(story)
        return True

    except Exception as exc:
        console.print(f"[yellow]⚠️  Erreur génération PDF : {exc}[/yellow]")

        md_path = output_path.with_suffix(".md")
        with open(md_path, "w", encoding="utf-8") as handle:
            handle.write(markdown_content)

        console.print(f"[dim]Rapport sauvegardé en Markdown : {md_path}[/dim]")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint agent
# ─────────────────────────────────────────────────────────────────────────────
def run_report_agent(state: CityMatchState) -> CityMatchState:
    """Nœud LangGraph : génère le rapport Markdown/PDF."""
    start_time = time.time()
    console.print("\n[bold cyan]📄 ReportAgent activé[/bold cyan]")

    if not state.get("top_cities"):
        console.print("[yellow]⚠️  Aucune ville top pour le rapport.[/yellow]")
        state["analysis_complete"] = True
        return state

    markdown_content = generate_markdown_report(state)
    state["report_markdown"] = markdown_content

    timestamp = _now_paris().strftime("%Y%m%d_%H%M%S")
    session_short = state.get("session_id", "unknown")[:8]
    filename = f"citymatch_report_{session_short}_{timestamp}"

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    md_path = REPORTS_DIR / f"{filename}.md"
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(markdown_content)

    console.print(f"[green]✅ Rapport Markdown : {md_path}[/green]")

    pdf_path = REPORTS_DIR / f"{filename}.pdf"
    pdf_success = generate_pdf_report(
        markdown_content=markdown_content,
        output_path=pdf_path,
        top_cities=state.get("top_cities", []),
        user_criteria=state.get("user_criteria", {}),
    )

    if pdf_success:
        state["report_pdf_path"] = str(pdf_path)
        console.print(f"[green]✅ Rapport PDF : {pdf_path}[/green]")
    else:
        state["report_pdf_path"] = str(md_path)

    from db.models import SearchSession, SessionLocal

    db = SessionLocal()
    try:
        session = db.query(SearchSession).filter_by(id=state.get("session_id")).first()
        if session:
            session.top_cities = [
                {
                    "nom": city.get("nom"),
                    "score": city.get("total_score"),
                    "rank": city.get("rank"),
                }
                for city in state.get("top_cities", [])
            ]
            session.report_path = state["report_pdf_path"]
            session.state = "completed"
            db.commit()
    except Exception as exc:
        console.print(f"[yellow]⚠️  Erreur sauvegarde session : {exc}[/yellow]")
    finally:
        db.close()

    state["analysis_complete"] = True

    duration_ms = int((time.time() - start_time) * 1000)
    console.print(f"[green]✅ Rapport généré en {duration_ms}ms[/green]")

    trace = list(state.get("agent_trace", []))
    trace.append(f"ReportAgent: rapport généré en {duration_ms}ms → {filename}")
    state["agent_trace"] = trace

    return state
