"""
scripts/patch_report_agent_robust.py
────────────────────────────────────
Patch robuste du ReportAgent.

Corrige :
1. Les balises ReportLab mal fermées du type <b>Généré le :<b>.
2. Les points de vigilance trompeurs sur la qualité de l'air.
3. Les formulations "filtres stricts" quand les filtres sont relâchés.

Le script modifie :
- agents/report_agent.py
- agents/reporting/*.py si présents

Il crée un backup .bak_before_report_robust_patch.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

TARGETS = []
main_report = ROOT / "agents" / "report_agent.py"
if main_report.exists():
    TARGETS.append(main_report)

reporting_dir = ROOT / "agents" / "reporting"
if reporting_dir.exists():
    TARGETS.extend(sorted(reporting_dir.glob("*.py")))


HELPER_CODE = 'def _is_real_vigilance(criterion_key: str, raw_value, normalized_score) -> bool:\n    """\n    Détermine si un critère doit vraiment être affiché en point de vigilance.\n\n    Pour la qualité de l\'air, on regarde d\'abord la valeur brute :\n    un air à 7/10 ou 8/10 ne doit pas apparaître comme critique simplement\n    parce que d\'autres villes ont 10/10.\n    """\n    if normalized_score is None:\n        return False\n\n    if criterion_key == "qualite_air_score":\n        try:\n            return float(raw_value) < 7\n        except Exception:\n            return False\n\n    try:\n        return float(normalized_score) < 4\n    except Exception:\n        return False\n'


def patch_reportlab_bold_tags(text: str) -> str:
    """
    Corrige les balises <b> mal fermées dans les Paragraph ReportLab.
    """
    text = re.sub(
        r"<b>(G[ée]n[ée]r[ée]\s+le\s*:?)<b>",
        r"<b>\1</b>",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"<b>([^<>]{1,80}:)<b>",
        r"<b>\1</b>",
        text,
    )

    return text


def patch_markdown_wording(text: str) -> str:
    """Remplace les formulations incohérentes dans le rapport Markdown."""
    replacements = {
        "Les 3 premières villes ont été sélectionnées en appliquant vos filtres\n"
        "de manière stricte.": (
            "Les premières villes sont les meilleurs compromis trouvés selon vos critères.\n"
            "Certains filtres peuvent être relâchés progressivement lorsqu'ils sont trop restrictifs."
        ),
        "Les 3 premières villes ont été sélectionnées en appliquant vos filtres\r\n"
        "de manière stricte.": (
            "Les premières villes sont les meilleurs compromis trouvés selon vos critères.\r\n"
            "Certains filtres peuvent être relâchés progressivement lorsqu'ils sont trop restrictifs."
        ),
        "Les 3 premières villes ont été sélectionnées en appliquant vos filtres de manière stricte.": (
            "Les premières villes sont les meilleurs compromis trouvés selon vos critères. "
            "Certains filtres peuvent être relâchés progressivement lorsqu'ils sont trop restrictifs."
        ),
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return text


def patch_air_vigilance_logic(text: str) -> str:
    """
    Ajoute une sécurité dans les helpers de rapport si on trouve une logique
    de points faibles basée seulement sur normalized_score.
    """
    if "def _is_real_vigilance(" not in text:
        marker_candidates = [
            "\ndef generate_",
            "\ndef create_",
            "\ndef run_report_agent",
        ]
        insert_at = -1
        for marker in marker_candidates:
            insert_at = text.find(marker)
            if insert_at != -1:
                break

        if insert_at != -1:
            text = text[:insert_at] + "\n" + HELPER_CODE + "\n" + text[insert_at:]
        else:
            text += "\n" + HELPER_CODE + "\n"

    replacements = {
        'if detail.get("normalized_score", 0) < 4:':
            'if _is_real_vigilance(key, detail.get("raw_value"), detail.get("normalized_score")):',
        "if detail.get('normalized_score', 0) < 4:":
            "if _is_real_vigilance(key, detail.get('raw_value'), detail.get('normalized_score')):",
        'if d.get("normalized_score", 0) < 4:':
            'if _is_real_vigilance(k, d.get("raw_value"), d.get("normalized_score")):',
        "if d.get('normalized_score', 0) < 4:":
            "if _is_real_vigilance(k, d.get('raw_value'), d.get('normalized_score')):",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    return text


def patch_sources(text: str) -> str:
    """Ajoute ARCEP / ATMO dans les sources si la section est construite en dur."""
    if "Sources de Données" not in text:
        return text

    if "ARCEP" not in text and "Ministère de l'Intérieur" in text:
        text = text.replace(
            "Statistiques de criminalité par commune",
            "Statistiques de criminalité par commune\\n"
            "- **ARCEP**\\n  Couverture fibre par commune",
        )

    if "ATMO" not in text and "ARCEP" in text:
        text = text.replace(
            "Couverture fibre par commune",
            "Couverture fibre par commune\\n"
            "- **ATMO / associations régionales agréées**\\n  Qualité de l'air quand disponible",
        )

    return text


def patch_file(path: Path) -> bool:
    original = path.read_text(encoding="utf-8")
    text = original

    text = patch_reportlab_bold_tags(text)
    text = patch_markdown_wording(text)
    text = patch_air_vigilance_logic(text)
    text = patch_sources(text)

    if text == original:
        return False

    backup = path.with_suffix(path.suffix + ".bak_before_report_robust_patch")
    backup.write_text(original, encoding="utf-8")
    path.write_text(text, encoding="utf-8")
    return True


def main() -> None:
    if not TARGETS:
        raise FileNotFoundError("Aucun fichier report_agent.py / agents/reporting/*.py trouvé.")

    changed = []
    for path in TARGETS:
        if patch_file(path):
            changed.append(path)

    if changed:
        print("✅ Fichiers corrigés :")
        for path in changed:
            print(f" - {path.relative_to(ROOT)}")
        return

    print("Aucun fichier modifié.")
    print("Commandes de diagnostic :")
    print('  grep -R "<b>.*<b>" agents/report_agent.py agents/reporting || true')
    print('  grep -R "normalized_score" agents/report_agent.py agents/reporting || true')
    print('  grep -R "filtres" agents/report_agent.py agents/reporting || true')


if __name__ == "__main__":
    main()
