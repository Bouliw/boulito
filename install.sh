#!/bin/zsh
# Installs Boulito in this folder: Python environment, models, and the Boulito.app menu bar app.
# Everything stays in this folder (delete it to remove everything, after ./uninstall.sh).
# Network: only here, to download Python packages, the speech models from Hugging Face and the voice detector from GitHub.
set -e
DIR="${0:A:h}"
cd "$DIR"

step() { print -P "\n%B→ $1%b" }
fail() { print -P "%F{red}✗ $1%f"; exit 1 }

step "Checking this Mac"
[[ "$(uname -m)" == "arm64" ]] || fail "Boulito needs a Mac with Apple Silicon (M1 or later)."
major=$(sw_vers -productVersion | cut -d. -f1)
(( major >= 14 )) || fail "Boulito needs macOS 14 or later (this Mac: $(sw_vers -productVersion))."
xcode-select -p >/dev/null 2>&1 || fail "Apple's Command Line Tools are missing: run  xcode-select --install  then run this script again."
UV="$(command -v uv || true)"
[[ -n "$UV" ]] || fail "uv is missing: run  brew install uv  (https://docs.astral.sh/uv/) then run this script again."
command -v python3.13 >/dev/null || fail "Python 3.13 is missing: run  brew install python@3.13  (or use the python.org installer)."
echo "✓ Apple Silicon, macOS $(sw_vers -productVersion), Command Line Tools, uv, Python 3.13"

step "Python environment (.venv)"
export UV_CACHE_DIR="$DIR/.cache/uv" UV_PYTHON_DOWNLOADS=never
"$UV" sync --frozen --quiet
echo "✓ .venv ready"

step "Speech models (speech recognition and voice detection: about 2.3 GB)"
./voix download --speech-only

step "Stable local signature (optional, recommended)"
echo "Signing Boulito.app with a certificate made on this Mac keeps its macOS permissions when it is rebuilt."
echo "macOS will ask for your session password to trust this certificate for code signing only."
if read -q "?Create and trust it now? [y/N] "; then
  echo
  ./app/signing.sh trust
else
  echo "\n· Skipped: Boulito.app will be signed ad hoc (run ./app/signing.sh trust later if you wish)."
fi

step "Building Boulito.app"
./app/build.sh

step "Done"
echo "Start Boulito with  ./boulito  (or double-click Boulito.app). Its face appears in the menu bar, and the"
echo "Setup window guides you: permissions (microphone, key, Safari…), then the AI model of your choice (Download button)."
