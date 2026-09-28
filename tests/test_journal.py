"""Logs: folder readable by the user only (0700), files 0600, and private sentences never written in clear.

Run: python -m unittest
"""

import json
import os
import re
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("BOULITO_DATA", tempfile.mkdtemp(prefix="boulito-tests-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voix import cli, journal, router  # noqa: E402


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class Permissions(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="boulito-logs-")) / "logs"
        patcher = mock.patch.object(journal, "log_dir", return_value=self.folder)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.umask = os.umask(0o022)
        self.addCleanup(os.umask, self.umask)

    def test_new_folder_and_file(self):
        journal.write({"cmd": "test"})
        self.assertEqual(mode(self.folder), 0o700)
        files = list(self.folder.glob("*.jsonl"))
        self.assertEqual(len(files), 1)
        self.assertEqual(mode(files[0]), 0o600)
        self.assertEqual(json.loads(files[0].read_text())["cmd"], "test")

    def test_folder_and_file_of_an_older_version(self):
        self.folder.mkdir(mode=0o755)
        old = self.folder / f"{journal.date.today()}.jsonl"
        old.write_text("")
        old.chmod(0o644)
        journal.write({"cmd": "test"})
        self.assertEqual(mode(self.folder), 0o700)
        self.assertEqual(mode(old), 0o600)

    def test_private_opener(self):
        with open(journal.ensure_log_dir() / "x.download.log", "a", opener=journal.private) as f:
            f.write("x")
        self.assertEqual(mode(self.folder / "x.download.log"), 0o600)


class PrivateText(unittest.TestCase):
    def test_dictated_text_hidden(self):
        for text in ("Écris bonjour Paul, je serai en retard.", "Envoie un message à Alice : j'arrive dans dix minutes.",
                     "Rappelle-moi d'appeler le médecin demain."):
            with self.subTest(text=text):
                self.assertEqual(cli.private_text(text, []), f"(private, {len(text)} characters)")

    def test_private_tool_hides_text(self):
        call = router.Call("type_text", {"text": "mon code"}, rule="write")
        self.assertTrue(cli.private_text("tape mon code", [call]).startswith("(private"))

    def test_command_kept(self):
        call = router.Call("system_volume", {"action": "up"}, rule="volume")
        self.assertEqual(cli.private_text("Monte le son.", [call]), "Monte le son.")

    def test_no_raw_sentence_in_the_logs(self):
        source = Path(cli.__file__).read_text(encoding="utf-8")
        raw = re.findall(r'journal\.write\(\{[^}]*"text": (?:text|h\.text)\b', source)
        self.assertEqual(raw, [], "a sentence goes to the logs without private_text()")


if __name__ == "__main__":
    unittest.main()
