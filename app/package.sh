#!/bin/zsh
# Builds dist/Boulito-<version>.dmg: a standalone Boulito.app (its own Python 3.13, the code, no model) to drag
# into Applications. Models are downloaded from the app's Setup window; settings, models and logs go to
# ~/Library/Application Support/Boulito ("Erase everything" in Setup deletes them).
#
# Signed with the "Boulito Release" certificate (./app/signing.sh release, created on first use; no paid Apple
# Developer account): the first time, macOS asks to confirm in System Settings → Privacy & Security → Open Anyway.
# The same certificate on every version: macOS keeps the permissions across updates, and the app's updater only
# installs a version signed by it (update.RELEASE_REQUIREMENT).
# BOULITO_VERSION=0.2.0.9 ./app/package.sh builds a test version with another number.
#
# Needs uv and the project's .venv (install.sh). Downloads only Python 3.13 (python-build-standalone) into
# .cache/uv-python, once; the libraries are copied from .venv (same versions as uv.lock, nothing downloaded).
# The code is taken from the last commit (git archive): nothing personal or untracked can end up in the app.
set -e
setopt null_glob  # patterns with no match (files already absent): ignored
DIR="${0:A:h}"
ROOT="${DIR:h}"
VERSION="${BOULITO_VERSION:-$(sed -n 's/^version = "\(.*\)".*/\1/p' "$ROOT/pyproject.toml" | head -1)}"
BUNDLE_ID="io.github.bouliw.boulito"
BUILD="$ROOT/build"
APP="$BUILD/Boulito.app"
RES="$APP/Contents/Resources"
DMG="$ROOT/dist/Boulito-$VERSION.dmg"

export PYTHONDONTWRITEBYTECODE=1  # the app's Python writes nothing during the build (see step 3)
export UV_CACHE_DIR="$ROOT/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$ROOT/.cache/uv-python"
UV="$(command -v uv || true)"
for candidate in /opt/homebrew/bin/uv /usr/local/bin/uv "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
  [[ -n "$UV" ]] && break
  [[ -x "$candidate" ]] && UV="$candidate"
done
[[ -n "$UV" ]] || { echo "uv not found: brew install uv" >&2; exit 1; }
[[ -z "$(git -C "$ROOT" status --porcelain -- src app shortcuts config.example.toml pyproject.toml uv.lock)" ]] \
  || echo "Warning: uncommitted changes are NOT in the app (it is built from the last commit)"

echo "1/6 Python 3.13 (python-build-standalone)"
rm -rf "$BUILD" && mkdir -p "$BUILD" "$ROOT/dist"
cd "$BUILD"  # outside the project: its uv settings (system Python only) do not apply here
UV_PYTHON_DOWNLOADS=automatic "$UV" python install --no-config 3.13 >/dev/null
PYTHON="$("$UV" python find --no-config --managed-python 3.13)"
mkdir -p "$APP/Contents/MacOS" "$RES"
ditto "${PYTHON:A:h:h}" "$RES/python"
PY="$RES/python/bin/python3"
rm -f "$RES/python/lib/python3.13/EXTERNALLY-MANAGED"
# uv wrote its install folder (a path on this Mac) into Python's configuration: the app's instead
INSTALLED="/Applications/Boulito.app/Contents/Resources"
grep -rlIF --null -- "${PYTHON:A:h:h}" "$RES/python" | xargs -0 sed -i '' "s|${PYTHON:A:h:h}|$INSTALLED/python|g"
# Not needed by Boulito: Python's tests, the Tk and IDLE interfaces, build headers
# (libpython: Python is already in its executable; uv wrote a path on this Mac into it)
rm -rf "$RES/python/lib/python3.13/"{test,idlelib,tkinter,turtledemo,ensurepip} "$RES/python/lib/"{tcl,tk,itcl,thread,libtcl,libtk,libpython}* \
       "$RES/python/include" "$RES/python/share" "$RES/python/lib/python3.13/site-packages/pip"*

echo "2/6 Libraries (from the project's .venv, which must match uv.lock exactly)"
"$UV" sync --project "$ROOT" --frozen --offline --check >/dev/null 2>&1 \
  || { echo ".venv does not match uv.lock: run ./install.sh (or uv sync --frozen) first" >&2; exit 1; }
rsync -a --exclude "__pycache__" --exclude "voix.pth" --exclude "voix-*.dist-info" --exclude "_virtualenv.*" \
  --exclude "PyObjCTest" "$ROOT/.venv/lib/python3.13/site-packages/" "$RES/python/lib/python3.13/site-packages/"

echo "3/6 Boulito's code (last commit)"
mkdir -p "$RES/boulito"
git -C "$ROOT" archive HEAD src/voix app/icon shortcuts config.example.toml LICENSE | tar -x -C "$RES/boulito"
rm -f "$RES/boulito/app/icon/draw_icon.py"
# Everything compiled in advance: the app never writes a .pyc inside itself (its signature would break). The paths
# baked into the .pyc files are those of the installed app, never those of this Mac
find "$RES" -name __pycache__ -type d -prune -exec rm -rf {} +
"$PY" -m compileall -q -f -j 0 --invalidation-mode unchecked-hash -s "$RES" -p "$INSTALLED" \
  "$RES/boulito/src" "$RES/python/lib/python3.13" >/dev/null 2>&1 || true
# Nothing from this Mac in the app: no user name, no home folder (otherwise, no .dmg)
LEAKS=$(grep -rlaF -- "$HOME/" "$APP" | head -5)
[[ -z "$LEAKS" ]] || { echo "Personal path found in the app, stopping:" >&2; echo "$LEAKS" >&2; exit 1; }

echo "4/6 Launcher, icon, Info.plist"
clang -O2 -Wall -fobjc-arc -mmacosx-version-min=14.0 -framework AppKit -framework Foundation -framework UserNotifications \
  -o "$APP/Contents/MacOS/Boulito" "$DIR/launcher.m"
cp "$DIR/Info.plist" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier $BUNDLE_ID" "$APP/Contents/Info.plist"
for key in CFBundleShortVersionString CFBundleVersion; do
  /usr/libexec/PlistBuddy -c "Set :$key $VERSION" "$APP/Contents/Info.plist" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :$key string $VERSION" "$APP/Contents/Info.plist"
done
/usr/libexec/PlistBuddy -c "Set :LSMinimumSystemVersion 14.0" "$APP/Contents/Info.plist" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string 14.0" "$APP/Contents/Info.plist"
cp -R "$DIR/fr.lproj" "$RES/"
iconutil -c icns "$DIR/icon/Boulito.iconset" -o "$RES/Boulito.icns"

echo "5/6 Signature (Boulito Release)"
find "$RES" -type f \( -name "*.so" -o -name "*.dylib" -o -perm +111 \) -print0 | while IFS= read -r -d '' f; do
  file -b "$f" | grep -q "Mach-O" && codesign --force --sign - "$f" 2>/dev/null  # the nested code: ad hoc
done
"$DIR/signing.sh" release >/dev/null  # created the first time (private key in .signing/, never published)
IDENTITY=$("$DIR/signing.sh" release identity)
KEYCHAIN="$ROOT/.signing/release.keychain-db"
# codesign only looks for the identity in the session's keychain list: the release keychain is
# added to it while signing, then the original list is restored (even on error)
ORIGINAL=("${(@f)$(security list-keychains -d user | sed -e 's/^ *"//' -e 's/"$//')}")
trap 'security list-keychains -d user -s "${ORIGINAL[@]}"' EXIT
security list-keychains -d user -s "${ORIGINAL[@]}" "$KEYCHAIN"
codesign --force --sign "$IDENTITY" --keychain "$KEYCHAIN" --identifier "$BUNDLE_ID" "$APP"
security list-keychains -d user -s "${ORIGINAL[@]}"
trap - EXIT
codesign --verify --deep --strict "$APP"
LEAF=$(echo "$IDENTITY" | tr A-F a-f)  # the app must only accept versions signed by this certificate
grep -q "certificate leaf = H\"$LEAF\"" "$RES/boulito/src/voix/update.py" \
  || { echo "update.RELEASE_REQUIREMENT does not name this certificate ($LEAF): fix it, commit, build again" >&2; exit 1; }

echo "6/6 Disk image"
STAGE="$BUILD/dmg"
mkdir -p "$STAGE"
ditto "$APP" "$STAGE/Boulito.app"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -quiet -volname "Boulito $VERSION" -srcfolder "$STAGE" -format ULMO "$DMG"
echo "Built: $DMG ($(du -h "$DMG" | cut -f1); app $(du -sh "$APP" | cut -f1))"
