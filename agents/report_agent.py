"""
agents/report_agent.py
──────────────────────
Entrypoint de génération de rapports.

La logique Markdown/PDF détaillée est dans agents/reporting/core.py.
"""

from agents.reporting.core import run_report_agent

__all__ = ["run_report_agent"]
