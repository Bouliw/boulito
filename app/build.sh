#!/bin/zsh
# Builds Boulito.app in the project folder (compiled launcher, Info.plist, icon), signed locally.
# Run it again after changing launcher.m, Info.plist or the icon. Installs nothing.
# With the trusted local identity (./app/signing.sh trust), macOS permissions survive a
# rebuild; without it, the app is signed ad hoc and macOS asks for them again.
set -e
DIR="${0:A:h}"
APP="$DIR/../Boulito.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
iconutil -c icns "$DIR/icon/Boulito.iconset" -o "$APP/Contents/Resources/Boulito.icns"  # generated icon, not versioned
cp "$DIR/Info.plist" "$APP/Contents/Info.plist"
cp -R "$DIR/fr.lproj" "$APP/Contents/Resources/"  # French texts for the permission requests
clang -O2 -Wall -fobjc-arc -framework AppKit -framework Foundation -framework UserNotifications -o "$APP/Contents/MacOS/Boulito" "$DIR/launcher.m"
# Stable signature if the local identity is trusted (./app/signing.sh trust): the permissions survive
IDENTITY=$("$DIR/signing.sh" identity 2>/dev/null || true)
if [[ -n "$IDENTITY" ]]; then
  # codesign only looks for the identity in the session's keychain list: the project's keychain
  # is added to it while signing, then the original list is restored (even on error)
  KEYCHAIN="${DIR:h}/.signing/boulito.keychain-db"
  ORIGINAL=("${(@f)$(security list-keychains -d user | sed -e 's/^ *"//' -e 's/"$//')}")
  trap 'security list-keychains -d user -s "${ORIGINAL[@]}"' EXIT
  security list-keychains -d user -s "${ORIGINAL[@]}" "$KEYCHAIN"
  codesign --force --sign "$IDENTITY" --keychain "$KEYCHAIN" --identifier local.boulito "$APP"
  security list-keychains -d user -s "${ORIGINAL[@]}"
  trap - EXIT
  echo "Signed with the stable local identity ($IDENTITY)"
else
  codesign --force --sign - --identifier local.boulito "$APP"
  echo "Signed ad hoc: macOS will ask for the permissions again (see ./app/signing.sh trust)"
fi
echo "Boulito.app built: ${APP:A}"
