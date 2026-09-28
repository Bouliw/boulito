"""GitHub Actions: every action used by a workflow is pinned to a full commit hash, and Dependabot updates them.

Run: python -m unittest
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.y*ml"))


class PinnedActions(unittest.TestCase):
    def test_workflows_found(self):
        self.assertTrue(WORKFLOWS)

    def test_every_action_pinned_to_a_commit(self):
        for workflow in WORKFLOWS:
            for n, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
                m = re.match(r"\s*-?\s*uses:\s*([^\s#]+)", line)
                if m is None or m[1].startswith(("./", "docker://")):
                    continue
                with self.subTest(file=workflow.name, line=n):
                    self.assertRegex(m[1], r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$", "action not pinned to a commit hash")
                    self.assertRegex(line, r"#\s*v\d", "version comment missing (Dependabot keeps it up to date)")

    def test_dependabot_updates_actions(self):
        config = (ROOT / ".github/dependabot.yml").read_text(encoding="utf-8")
        self.assertRegex(config, r"package-ecosystem:\s*github-actions")


if __name__ == "__main__":
    unittest.main()
