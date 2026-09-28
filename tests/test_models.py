"""Hugging Face models: each model Boulito offers is pinned to a commit, used to download, find and load it.

Run: python -m unittest (Hugging Face is replaced by a fake: no network)
"""

import os
import re
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("BOULITO_DATA", tempfile.mkdtemp(prefix="boulito-tests-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voix import config, llm, stt  # noqa: E402

PINNED = "0123456789abcdef0123456789abcdef01234567"


class Revisions(unittest.TestCase):
    def test_every_model_offered_is_pinned(self):
        example = tomllib.loads(config.EXAMPLE_FILE.read_text(encoding="utf-8"))
        for repo in [*llm.MODELS.values(), example["stt"]["model"]]:
            with self.subTest(repo=repo):
                self.assertRegex(config.model_revision(repo) or "", r"^[0-9a-f]{40}$",
                                 "missing commit hash in config.MODEL_REVISIONS")

    def test_model_chosen_by_hand_follows_main(self):
        self.assertIsNone(config.model_revision("someone/another-model"))


class Downloads(unittest.TestCase):
    def setUp(self):
        self.hub = mock.MagicMock()
        patches = [mock.patch.dict(sys.modules, {"huggingface_hub": self.hub}),
                   mock.patch.dict(config.MODEL_REVISIONS, {"org/model": PINNED})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_llm_download_and_lookup(self):
        llm.download("org/model")
        self.assertEqual(self.hub.snapshot_download.call_args.kwargs["revision"], PINNED)
        llm.local_path("org/model")
        self.assertEqual(self.hub.snapshot_download.call_args.kwargs["revision"], PINNED)
        self.assertTrue(self.hub.snapshot_download.call_args.kwargs["local_files_only"])

    def test_stt_download_and_lookup(self):
        with mock.patch.object(stt, "model_id", return_value="org/model"):
            stt.download()
            self.assertTrue(self.hub.hf_hub_download.call_args_list)
            for call in self.hub.hf_hub_download.call_args_list:
                self.assertEqual(call.kwargs["revision"], PINNED)
            stt.is_downloaded()
            self.assertEqual(self.hub.try_to_load_from_cache.call_args.kwargs["revision"], PINNED)


class Loading(unittest.TestCase):
    def test_parakeet_loaded_from_the_pinned_folder(self):
        source = (Path(stt.__file__)).read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"from_pretrained\(model_id\(\)", source),
                          "from_pretrained(model_id()) would load the main branch, not the pinned commit")


if __name__ == "__main__":
    unittest.main()
