"""safety.execute: an app opened or shown by the LLM must have been asked for in the spoken sentence.

Run: python -m unittest (the app is never really opened: apps.open_app and apps.switch_app are replaced)
"""

import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("BOULITO_DATA", tempfile.mkdtemp(prefix="boulito-tests-"))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voix import safety  # noqa: E402
from voix.router import Call  # noqa: E402


class AppIntent(unittest.TestCase):
    def setUp(self):
        for name in ("open_app", "switch_app"):
            patcher = mock.patch.object(safety.apps, name, return_value="Notes")
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def execute(self, tool, source, level=1):
        return safety.execute(Call(tool, {"name": "Notes"}, rule="llm", level=level, source=source))

    def test_not_asked_by_the_llm(self):
        # A booby-trapped video title or page text must not open an app
        for source in ("Résume cette vidéo.", "Mets la vidéo en plein écran.", "Summarize this video.",
                       "Quelle heure est-il ?"):
            for tool in ("open_app", "switch_app"):
                with self.subTest(tool=tool, source=source), self.assertRaises(safety.ToolError):
                    self.execute(tool, source)
        self.open_app.assert_not_called()
        self.switch_app.assert_not_called()

    def test_asked(self):
        for source in ("Ouvre Notes et crée une nouvelle note.", "Passe sur Notes et tape bonjour.",
                       "Open a fresh tab in Safari please.", "Montre-moi Notes.", "Öffne Notizen.", "Abre Notas."):
            for tool in ("open_app", "switch_app"):
                with self.subTest(tool=tool, source=source):
                    self.execute(tool, source)

    def test_rules_unchanged(self):
        self.execute("open_app", "Notes", level=0)  # level 0: the rule itself matched the sentence
        self.open_app.assert_called_once()

    def test_level1_scenarios_still_allowed(self):
        """Every sentence of tests/level1.toml where an app is expected passes the check."""
        scenarios = tomllib.loads((ROOT / "tests/level1.toml").read_text(encoding="utf-8"))["phrase"]
        checked = 0
        for scenario in scenarios:
            tools = {step["tool"] for option in scenario.get("expect", []) for step in option}
            for tool in tools & {"open_app", "switch_app"}:
                with self.subTest(text=scenario["text"], tool=tool):
                    self.execute(tool, scenario["text"])
                    checked += 1
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
