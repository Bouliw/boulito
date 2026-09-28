"""update.download: size capped (announced and received), digest checked. No network: the response is faked.

Run: python -m unittest
"""

import hashlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("BOULITO_DATA", tempfile.mkdtemp(prefix="boulito-tests-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voix import update  # noqa: E402

DATA = b"dmg" * 1000


class Endless(io.RawIOBase):
    """A server that keeps sending (bounded here, so that a regression fails instead of hanging)."""

    def __init__(self):
        self.reads = 0

    def read(self, n=-1):
        self.reads += 1
        return b"x" * n if self.reads <= 64 else b""


def info(data=DATA, **changes):
    return {"version": "9.9.9", "url": update.DOWNLOADS + "v9.9.9/Boulito.dmg", "size": len(data),
            "digest": "sha256:" + hashlib.sha256(data).hexdigest(), "page": ""} | changes


class Download(unittest.TestCase):
    def serve(self, response):
        patcher = mock.patch.object(update, "_open", return_value=response)
        opened = patcher.start()
        self.addCleanup(patcher.stop)
        return opened

    def reason(self, info_):
        with self.assertRaises(update.UpdateError) as caught:
            update.download(info_)
        return caught.exception.reason

    def test_expected_file(self):
        self.serve(io.BytesIO(DATA))
        self.assertEqual(update.download(info()).read_bytes(), DATA)

    def test_announced_too_large(self):
        opened = self.serve(io.BytesIO(DATA))
        self.assertEqual(self.reason(info(size=update.MAX_DMG_BYTES + 1)), "size")
        opened.assert_not_called()

    def test_size_missing(self):
        opened = self.serve(io.BytesIO(DATA))
        self.assertEqual(self.reason(info(size=0)), "size")
        opened.assert_not_called()

    def test_more_than_announced(self):
        endless = Endless()
        self.serve(endless)
        self.assertEqual(self.reason(info()), "size")
        self.assertLessEqual(endless.reads, 2)  # stopped at once, not at the end of the stream
        self.assertFalse((update.config.CACHE_DIR / "update").exists())

    def test_less_than_announced(self):
        self.serve(io.BytesIO(DATA[:-10]))
        self.assertEqual(self.reason(info()), "digest")

    def test_reason_has_a_message(self):
        from voix import i18n

        self.assertEqual(set(i18n.TEXTS["update.reason.size"]), set(i18n.LANGUAGES))


if __name__ == "__main__":
    unittest.main()
