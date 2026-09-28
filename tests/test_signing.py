"""app/signing.sh: the keychain's password is never written to a file nor passed as an argument.

A fake security (first in PATH) records every command line and what it reads on standard input, and keeps
the login keychain's items in a JSON file. The real openssl makes the certificate. Run: python -m unittest
"""

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FAKE_SECURITY = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, shlex, sys
    from pathlib import Path

    state_file = Path(os.environ["FAKE_STATE"])
    state = json.loads(state_file.read_text()) if state_file.exists() else {"items": {}, "keychains": {}}
    log = open(os.environ["FAKE_LOG"], "a")

    def option(args, name):
        return args[args.index(name) + 1] if name in args else None

    def run(args, via_stdin):
        log.write(json.dumps({"argv": args, "stdin": via_stdin}) + "\\n")
        command, rest = args[0], args[1:]
        if command == "create-keychain":
            Path(rest[-1]).write_text("keychain")
            state["keychains"][rest[-1]] = option(rest, "-p")
        elif command == "add-generic-password":
            state["items"][option(rest, "-s") + "|" + option(rest, "-a")] = {"password": option(rest, "-w"),
                                                                               "trusted": option(rest, "-T")}
        elif command == "find-generic-password":
            item = state["items"].get(option(rest, "-s") + "|" + option(rest, "-a"))
            if item is None:
                return 44
            print(item["password"])
        elif command == "unlock-keychain":
            if state["keychains"].get(rest[-1]) != option(rest, "-p"):
                print("security: SecKeychainUnlock: The user name or passphrase you entered is not correct.",
                      file=sys.stderr)
                return 51
        elif command == "find-identity":
            print('  1) 0123456789ABCDEF0123456789ABCDEF01234567 "Boulito Release"')
            print('  2) 89ABCDEF0123456789ABCDEF0123456789ABCDEF "Boulito Local Signing"')
        return 0

    status = 0
    if sys.argv[1:] == ["-i"]:
        for line in sys.stdin:
            if line.strip():
                status = run(shlex.split(line), True) or status
    else:
        status = run(sys.argv[1:], False)
    state_file.write_text(json.dumps(state))
    sys.exit(status)
''')


@unittest.skipUnless(shutil.which("zsh") and Path("/usr/bin/openssl").exists(), "needs zsh and /usr/bin/openssl")
class SigningPassword(unittest.TestCase):
    def setUp(self):
        # Resolved like signing.sh does (${0:A}): on macOS the temporary folder /var/… is a link to /private/var/…
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "app").mkdir()
        shutil.copy(ROOT / "app/signing.sh", self.tmp / "app/signing.sh")
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "security").write_text(FAKE_SECURITY)
        (bin_dir / "security").chmod(0o755)
        self.state, self.log = self.tmp / "state.json", self.tmp / "log.jsonl"
        self.env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
                    "FAKE_STATE": str(self.state), "FAKE_LOG": str(self.log)}
        self.signing = self.tmp / ".signing"

    def run_signing(self, *args):
        return subprocess.run(["zsh", str(self.tmp / "app/signing.sh"), *args], env=self.env,
                              capture_output=True, text=True, timeout=120)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def assert_never_exposed(self, password):
        for call in self.calls():
            if password in " ".join(call["argv"]):
                self.assertTrue(call["stdin"], f"password passed as an argument: {call['argv'][0]}")
        for f in self.signing.rglob("*"):
            if f.is_file():
                self.assertNotIn(password.encode(), f.read_bytes(), f"password written to {f.name}")

    def test_release_identity_created(self):
        out = self.run_signing("release")
        self.assertEqual(out.returncode, 0, out.stderr)
        keychain = str(self.signing / "release.keychain-db")
        item = json.loads(self.state.read_text())["items"][f"Boulito signing keychain|{keychain}"]
        self.assertRegex(item["password"], r"^[0-9a-f]{48}$")
        self.assertEqual(item["trusted"], "")  # no app trusted: macOS asks before handing it out
        self.assert_never_exposed(item["password"])
        self.assertFalse((self.signing / "release-password").exists())
        self.assertFalse((self.signing / "key.pem").exists())
        self.assertIn(["set-keychain-settings", "-lut", "900", keychain], [c["argv"] for c in self.calls()])
        unlocks = [c for c in self.calls() if c["argv"][0] == "unlock-keychain"]
        self.assertTrue(unlocks and all(c["stdin"] for c in unlocks))

    def test_identity_after_creation(self):
        self.assertEqual(self.run_signing("release").returncode, 0)
        out = self.run_signing("release", "identity")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "0123456789ABCDEF0123456789ABCDEF01234567")

    def test_legacy_password_file_migrated(self):
        self.signing.mkdir()
        keychain = str(self.signing / "release.keychain-db")
        Path(keychain).write_text("keychain")
        legacy = "ab" * 24
        (self.signing / "release-password").write_text(legacy + "\n")
        self.state.write_text(json.dumps({"items": {}, "keychains": {keychain: legacy}}))
        out = self.run_signing("release", "identity")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse((self.signing / "release-password").exists())
        item = json.loads(self.state.read_text())["items"][f"Boulito signing keychain|{keychain}"]
        self.assertEqual(item["password"], legacy)
        self.assert_never_exposed(legacy)

    def test_legacy_file_kept_if_the_password_does_not_unlock(self):
        self.signing.mkdir()
        keychain = str(self.signing / "boulito.keychain-db")
        Path(keychain).write_text("keychain")
        (self.signing / "password").write_text("wrong\n")
        self.state.write_text(json.dumps({"items": {}, "keychains": {keychain: "right"}}))
        out = self.run_signing()
        self.assertNotEqual(out.returncode, 0)
        self.assertTrue((self.signing / "password").exists())


class NoPasswordOnCommandLines(unittest.TestCase):
    def test_script_text(self):
        script = (ROOT / "app/signing.sh").read_text()
        self.assertNotIn("pass:", script)  # openssl reads the PKCS#12 password on stdin
        for line in script.splitlines():
            code = line.split("#")[0]
            if code.lstrip().startswith("security ") and "$PASSWORD" in code:
                self.fail(f"password on a security command line: {line.strip()}")


if __name__ == "__main__":
    unittest.main()
