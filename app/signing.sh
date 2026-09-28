#!/bin/zsh
# Stable local signing identity for Boulito.app.
#
# Signed "ad hoc", the app gets a new identity at every rebuild, and macOS forgets its permissions
# (microphone, Input Monitoring, Accessibility…). Signed with this certificate, its identity stays
# "local.boulito + this certificate": the permissions survive rebuilds.
#
# Everything stays in the project: a dedicated keychain in .signing/ (excluded from git: the private key never
# leaves), not the login keychain. Deleting the project folder deletes it.
# Its password is random and kept in the login keychain (item "Boulito signing keychain", account = the keychain's
# path), never in a file or on a command line (ps shows those to every process): the commands that carry it are
# read by security on its standard input (security -i). The item trusts no app, so macOS asks before handing
# it out; the project keychain locks itself after 15 minutes and when the Mac sleeps.
#
#   ./app/signing.sh           creates the identity if needed
#   ./app/signing.sh trust     trusts the certificate for code signing (macOS asks for your password)
#   ./app/signing.sh identity  prints the identity's fingerprint if it can be used (otherwise nothing)
#   ./app/signing.sh migrate   moves the password of an older version (.signing/password) to the login keychain
#
# "release" (./app/signing.sh release [identity|migrate]): a second identity, "Boulito Release", for the app shipped
# as a .dmg (app/package.sh). The same on every version, so macOS keeps the users' permissions across updates, and
# the updater only installs an app signed by it. Never trusted on this Mac (not needed to sign); its private key must
# never leave .signing/: keep an encrypted backup of .signing/ somewhere safe, and its password (shown by
# security find-generic-password -s "Boulito signing keychain" -a "<project>/.signing/release.keychain-db" -w)
# in a password manager: the keychain cannot be opened without it.
set -e
DIR="${0:A:h:h}/.signing"
if [[ "${1:-}" == release ]]; then
  shift
  KEYCHAIN="$DIR/release.keychain-db"
  NAME="Boulito Release"
  LEGACY="$DIR/release-password"
  CERT="$DIR/release-cert.pem"
else
  KEYCHAIN="$DIR/boulito.keychain-db"
  NAME="Boulito Local Signing"
  LEGACY="$DIR/password"
  CERT="$DIR/cert.pem"
fi
SERVICE="Boulito signing keychain"
# "identity" never creates anything: no keychain, no identity (build.sh then signs ad hoc)
[[ "${1:-}" == identity && ! -f "$KEYCHAIN" ]] && exit 0
# security -i parses its own command line: a quote or a backslash in the path would break it
[[ "$DIR" != *[\"\\]* ]] || { echo "The project path must not contain a quote or a backslash: $DIR" >&2; exit 1 }
mkdir -p "$DIR" && chmod 700 "$DIR"
# macOS's openssl (LibreSSL): present everywhere, and its PKCS#12 export can be read by "security import"
# (OpenSSL 3's -legacy option does not exist in it)
OPENSSL=/usr/bin/openssl

# Runs one security command read on standard input: the password it carries never appears in the process list.
# print is a zsh builtin (no process either). Fails if security reports an error.
secure() {
  local errors
  errors=$(print -r -- "$1" | security -i 2>&1 >/dev/null)
  [[ -z "$errors" ]] || { print -r -- "$errors" >&2; return 1 }
}

if [[ ! -f "$KEYCHAIN" ]]; then
  PASSWORD=$($OPENSSL rand -hex 24)
  $OPENSSL req -x509 -newkey rsa:2048 -nodes -days 7300 -subj "/CN=$NAME" \
    -keyout "$DIR/key.pem" -out "$CERT" \
    -addext "basicConstraints=critical,CA:false" -addext "keyUsage=critical,digitalSignature" \
    -addext "extendedKeyUsage=critical,codeSigning" 2>/dev/null
  print -r -- "$PASSWORD" | $OPENSSL pkcs12 -export -inkey "$DIR/key.pem" -in "$CERT" -name "$NAME" \
    -out "$DIR/identity.p12" -passout stdin
  secure "create-keychain -p $PASSWORD \"$KEYCHAIN\""
  secure "add-generic-password -U -s \"$SERVICE\" -a \"$KEYCHAIN\" -l \"$NAME keychain\" -T \"\" -w $PASSWORD"
  secure "unlock-keychain -p $PASSWORD \"$KEYCHAIN\""
  secure "import \"$DIR/identity.p12\" -k \"$KEYCHAIN\" -P $PASSWORD -T /usr/bin/codesign"
  secure "set-key-partition-list -S apple-tool:,apple: -s -k $PASSWORD \"$KEYCHAIN\""
  unset PASSWORD
  security find-identity "$KEYCHAIN" | grep -q "$NAME"  # import checked before discarding the key
  rm -f "$DIR/identity.p12" "$DIR/key.pem"  # the key only remains in the keychain
  echo "Signing identity created: $NAME" >&2
elif [[ -f "$LEGACY" ]]; then
  # Older versions kept the password in this file: it moves to the login keychain, then the file is deleted
  # (only once the keychain has been unlocked with the moved password, below)
  secure "add-generic-password -U -s \"$SERVICE\" -a \"$KEYCHAIN\" -l \"$NAME keychain\" -T \"\" -w $(<"$LEGACY")"
fi
# macOS asks (login keychain) before handing out the password; the project keychain is then unlocked with it
PASSWORD=$(security find-generic-password -s "$SERVICE" -a "$KEYCHAIN" -w) \
  || { echo "No password for $KEYCHAIN in the login keychain (or access refused)" >&2; exit 1 }
secure "unlock-keychain -p $PASSWORD \"$KEYCHAIN\""
unset PASSWORD
security set-keychain-settings -lut 900 "$KEYCHAIN"  # locks itself after 15 minutes and when the Mac sleeps
if [[ -f "$LEGACY" ]]; then
  rm -f "$LEGACY"
  echo "Password moved to the login keychain, $LEGACY deleted" >&2
fi
case "${1:-}" in
  trust)
    echo "macOS will ask for your password to trust “$NAME” (code signing only)." >&2
    security add-trusted-cert -p codeSign -k "$KEYCHAIN" "$CERT"
    security find-identity -v -p codesigning "$KEYCHAIN" | grep "$NAME" >&2 && echo "✓ identity ready to use" >&2 ;;
  identity)  # the release identity is trusted nowhere: no -v ("valid") for it
    security find-identity $([[ "$NAME" == "Boulito Release" ]] || echo -v) -p codesigning "$KEYCHAIN" \
      | awk -v n="\"$NAME\"" '$0 ~ n {print $2; exit}' ;;
esac
