"""Configuration: project paths, environment variables and config.toml.

Imported before any third-party library: it puts HF_HOME and the other caches in the data folder.
Source version (project folder): the data lives in this folder, deleting it is enough to erase everything.
.dmg version (Boulito.app): the code lives in the app, which must never be modified (its signature checks this);
the data goes to ~/Library/Application Support/Boulito ("Erase everything" in the setup window).
"""

import os
import re
import tomllib
from functools import cache
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]  # the code (Boulito.app/Contents/Resources/boulito in the .dmg version)
PACKAGED = PROJECT_DIR.parent.name == "Resources" and PROJECT_DIR.parents[1].name == "Contents"
APP_SUPPORT = Path.home() / "Library" / "Application Support" / "Boulito"
DATA_DIR = Path(os.environ.get("BOULITO_DATA") or (APP_SUPPORT if PACKAGED else PROJECT_DIR))  # settings, models, logs
APP_PATH = Path(os.environ.get("BOULITO_APP") or (PROJECT_DIR.parents[2] if PACKAGED else PROJECT_DIR / "Boulito.app"))
BUNDLE_ID = "io.github.bouliw.boulito" if PACKAGED else "local.boulito"
MODELS_DIR = DATA_DIR / "models"
CACHE_DIR = DATA_DIR / ".cache"
CONFIG_FILE = DATA_DIR / "config.toml"
EXAMPLE_FILE = PROJECT_DIR / "config.example.toml"  # shipped with the project; config.toml (per-user settings) is not versioned
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Same values as in the ./voix script, in case the code is launched another way.
ENV = {
    "HF_HOME": str(MODELS_DIR),
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_OFFLINE": "1",  # no network calls, except ./voix download which lifts it
    "DO_NOT_TRACK": "1",
    "UV_CACHE_DIR": str(CACHE_DIR / "uv"),
    "UV_PYTHON_INSTALL_DIR": str(CACHE_DIR / "uv-python"),
    "UV_PYTHON_DOWNLOADS": "never",
    "XDG_CACHE_HOME": str(CACHE_DIR),
    "TORCH_HOME": str(CACHE_DIR / "torch"),
    "NUMBA_CACHE_DIR": str(CACHE_DIR / "numba"),
}
os.environ.update(ENV)

# Variables that point to a folder: all of them must point inside the data folder.
PATH_VARS = ["HF_HOME", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR", "XDG_CACHE_HOME", "TORCH_HOME", "NUMBA_CACHE_DIR"]


# Hugging Face models offered by Boulito, each pinned to a commit: a repository rewritten upstream cannot change
# what is downloaded or loaded. To move to a newer version, test it, then change its hash here.
# A model set by hand in config.toml (another repository) follows its main branch.
MODEL_REVISIONS: dict[str, str | None] = {
    "mlx-community/parakeet-tdt-0.6b-v3": "ed2b7e8c15f9aaa0b5772e2efb986255eaef7e15",
    "mlx-community/Qwen3.5-4B-MLX-4bit": "32f3e8ecf65426fc3306969496342d504bfa13f3",
    "mlx-community/Qwen3.5-9B-MLX-4bit": "938d8919941c6e7efd3c7150eff7fe9d12afa631",
    "mlx-community/Qwen3.5-35B-A3B-OptiQ-4bit-REAP-19B": "212e00f5c653fcf4753a1513272ff598cf5a1f4e",
    "mlx-community/Qwen3.5-35B-A3B-4bit": "1e20fd8d42056f870933bf98ca6211024744f7ec",
}


def model_revision(repo: str) -> str | None:
    """Commit a model is pinned to (None: a model chosen by hand, main branch)."""
    return MODEL_REVISIONS.get(repo)


LANGUAGES = ("en", "fr", "es", "de", "it", "pt")  # interface languages (see i18n.LANGUAGES)


def mac_language() -> str | None:
    """The Mac's first preferred language among Boulito's ("fr-FR" → fr), otherwise None."""
    try:
        from Foundation import NSLocale

        for code in NSLocale.preferredLanguages():
            base = str(code).split("-")[0].lower()
            if base in LANGUAGES:
                return base
    except Exception:
        pass
    return None


def first_config() -> str:
    """config.example.toml, in the Mac's language: the interface speaks it, and Boulito understands it, plus English
    (loanwords: « play », « skip »). Everything can still be changed in the setup window."""
    text = EXAMPLE_FILE.read_text(encoding="utf-8")
    language = mac_language() or "en"
    understood = [language] if language == "en" else [language, "en"]
    text = re.sub(r'^language = "en"', f'language = "{language}"', text, count=1, flags=re.M)
    text = re.sub(r'^understood = \["en"\]', "understood = [" + ", ".join(f'"{c}"' for c in understood) + "]", text,
                  count=1, flags=re.M)
    return text


@cache
def load() -> dict:
    """Reads config.toml (created from config.example.toml on first launch, in the Mac's language)."""
    if not CONFIG_FILE.exists() and EXAMPLE_FILE.exists():
        CONFIG_FILE.write_text(first_config(), encoding="utf-8")
    with CONFIG_FILE.open("rb") as f:
        cfg = tomllib.load(f)
    return cfg


def project_path(relative: str) -> Path:
    """A path from config.toml (relative to the data folder: the project's, or Application Support), made absolute."""
    return DATA_DIR / relative


def _toml_string(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_value(section: str, key: str, value: str | bool | int | float | list) -> None:
    """Changes a value in config.toml (in the right section), keeping the comments.

    Missing section or key (config.toml from an older version): added. The new file is parsed again before
    replacing the old one: a malformed value can never stop Boulito from restarting.
    """
    text = CONFIG_FILE.read_text(encoding="utf-8")
    if isinstance(value, bool):
        written = "true" if value else "false"
    elif isinstance(value, str):
        written = _toml_string(value)
    elif isinstance(value, (list, tuple)):
        written = "[" + ", ".join(_toml_string(v) for v in value) + "]"
    else:
        written = str(value)
    header = re.compile(rf"^\[{re.escape(section)}\][ \t]*(?:#.*)?$", re.M)  # never an "[audio]" quoted in a comment
    if not header.search(text):
        text = text.rstrip("\n") + f"\n\n[{section}]\n"
    start = header.search(text).start()
    following = re.compile(r"^\[", re.M).search(text, start + 1)
    end = following.start() - 1 if following else len(text)
    string = r'"(?:[^"\\]|\\.)*"|\'[^\']*\''
    part, n = re.subn(rf'^({re.escape(key)}\s*=\s*)(?:{string}|true|false|-?[\d.]+|\[[^\]]*\])', lambda m: m[1] + written,
                      text[start:end], count=1, flags=re.M)
    if n != 1:
        part = text[start:end].rstrip("\n") + f"\n{key} = {written}\n"
    new = text[:start] + part + text[end:]
    tomllib.loads(new)  # fail here rather than leave an unreadable file for the next launch
    temporary = CONFIG_FILE.with_suffix(".toml.tmp")
    temporary.write_text(new, encoding="utf-8")
    os.replace(temporary, CONFIG_FILE)
    load.cache_clear()
