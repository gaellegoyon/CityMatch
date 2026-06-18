import sys
import types
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path


if "streamlit" not in sys.modules:
    fake_streamlit = types.ModuleType("streamlit")
    fake_streamlit.session_state = {}
    fake_streamlit.query_params = {}
    fake_streamlit.spinner = lambda *args, **kwargs: nullcontext()
    fake_streamlit.rerun = lambda: None
    sys.modules["streamlit"] = fake_streamlit

if "config" not in sys.modules:
    sys.modules["config"] = types.ModuleType("config")

fake_settings = types.ModuleType("config.settings")
fake_settings.REPORTS_DIR = Path(tempfile.gettempdir()) / "citymatch_reports"
sys.modules["config.settings"] = fake_settings

if "graph" not in sys.modules:
    sys.modules["graph"] = types.ModuleType("graph")

fake_orchestrator = types.ModuleType("graph.orchestrator")


class _DummyOrchestrator:
    def __init__(self, *args, **kwargs):
        pass


fake_orchestrator.CityMatchOrchestrator = _DummyOrchestrator
sys.modules["graph.orchestrator"] = fake_orchestrator

import ui.services.session as session_module


class SessionSecurityTests(unittest.TestCase):
    def test_load_report_markdown_rejects_files_outside_reports_dir(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            reports_dir = Path(tmp_dir) / "reports"
            reports_dir.mkdir()
            safe_report = reports_dir / "citymatch_report_ok.md"
            safe_report.write_text("safe report", encoding="utf-8")

            outside_report = Path(tmp_dir) / "outside.md"
            outside_report.write_text("outside report", encoding="utf-8")

            original_reports_dir = session_module.REPORTS_DIR
            session_module.REPORTS_DIR = reports_dir
            try:
                self.assertEqual(
                    session_module._load_report_markdown(str(safe_report)),
                    "safe report",
                )
                self.assertEqual(
                    session_module._load_report_markdown(str(outside_report)),
                    "",
                )
            finally:
                session_module.REPORTS_DIR = original_reports_dir


if __name__ == "__main__":
    unittest.main()
