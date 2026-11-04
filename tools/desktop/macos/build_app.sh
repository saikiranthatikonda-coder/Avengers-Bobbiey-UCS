#!/usr/bin/env bash
# Assemble, sign (Developer ID + notarize when the secrets exist, ad-hoc otherwise) and package
# BUCS.app into a .dmg.   Usage: tools/desktop/macos/build_app.sh macos-arm64|macos-x64 OUT_DIR
#
# Optional signing env (GitHub secrets):
#   APPLE_DEVELOPER_ID_CERT_P12 (base64 .p12 with the "Developer ID Application" cert + key),
#   APPLE_CERT_PASSWORD, and for notarization APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD
set -euo pipefail
TARGET="$1"; OUT="$(mkdir -p "$2" && cd "$2" && pwd)"
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HERE="$ROOT/tools/desktop/macos"
WORK="$ROOT/build/desktop/$TARGET"
STAGE="$WORK/stage"
VERSION="$(cat "$WORK/version.txt")"
case "$TARGET" in
  macos-arm64) ARCH=arm64;  DMG_NAME="BUCS-macOS-AppleSilicon.dmg" ;;
  macos-x64)   ARCH=x86_64; DMG_NAME="BUCS-macOS-Intel.dmg" ;;
  *) echo "unknown target $TARGET"; exit 2 ;;
esac
APP="$WORK/BUCS.app"
rm -rf "$APP"; mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

echo "::group::compile launcher ($ARCH)"
swiftc -O -target "$ARCH-apple-macos11.0" "$HERE/main.swift" -o "$APP/Contents/MacOS/BUCS"
lipo -info "$APP/Contents/MacOS/BUCS"
echo "::endgroup::"

sed "s/__VERSION__/$VERSION/g" "$HERE/Info.plist" > "$APP/Contents/Info.plist"
plutil -lint "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"
ditto "$STAGE/python" "$APP/Contents/Resources/python"
ditto "$STAGE/launcher" "$APP/Contents/Resources/launcher"
ditto "$STAGE/app" "$APP/Contents/Resources/app"
cp "$STAGE/build.json" "$APP/Contents/Resources/build.json"
python3 "$ROOT/tools/desktop/make_icon.py" "$WORK/icon"
iconutil -c icns "$WORK/icon/BUCS.iconset" -o "$APP/Contents/Resources/BUCS.icns"
find "$APP" -name "__pycache__" -type d -prune -exec rm -rf {} +
xattr -cr "$APP"

# ── signing ────────────────────────────────────────────────────────────
IDENTITY="-"; SIGN_OPTS=(); NOTARIZE=0
if [ -n "${APPLE_DEVELOPER_ID_CERT_P12:-}" ]; then
  echo "::group::import Developer ID certificate"
  KC="$RUNNER_TEMP/bucs-sign.keychain-db"; KCPW="$(uuidgen)"
  security create-keychain -p "$KCPW" "$KC"
  security set-keychain-settings -lut 3600 "$KC"
  security unlock-keychain -p "$KCPW" "$KC"
  echo "$APPLE_DEVELOPER_ID_CERT_P12" | base64 --decode > "$RUNNER_TEMP/cert.p12"
  security import "$RUNNER_TEMP/cert.p12" -k "$KC" -P "${APPLE_CERT_PASSWORD:-}" -T /usr/bin/codesign
  rm -f "$RUNNER_TEMP/cert.p12"
  security set-key-partition-list -S apple-tool:,apple: -s -k "$KCPW" "$KC" >/dev/null
  security list-keychains -d user -s "$KC" $(security list-keychains -d user | tr -d '"')
  IDENTITY="$(security find-identity -v -p codesigning "$KC" | grep 'Developer ID Application' | head -1 | awk '{print $2}')"
  [ -n "$IDENTITY" ] || { echo "::error::no 'Developer ID Application' identity in APPLE_DEVELOPER_ID_CERT_P12"; exit 1; }
  SIGN_OPTS=(--options runtime --timestamp)
  [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ] && [ -n "${APPLE_APP_PASSWORD:-}" ] && NOTARIZE=1
  echo "::endgroup::"
  echo "signing with Developer ID (hardened runtime); notarize=$NOTARIZE"
else
  echo "::warning::No Apple signing secrets: ad-hoc signing $DMG_NAME (users must approve it once in System Settings > Privacy & Security > Open Anyway)"
fi
ENT="$HERE/entitlements.plist"

echo "::group::codesign nested code"
n=0
while IFS= read -r -d '' f; do
  kind="$(file -b "$f")"
  case "$kind" in
    *Mach-O*executable*) codesign --force ${SIGN_OPTS[@]+"${SIGN_OPTS[@]}"} --entitlements "$ENT" -s "$IDENTITY" "$f"; n=$((n+1)) ;;
    *Mach-O*)            codesign --force ${SIGN_OPTS[@]+"${SIGN_OPTS[@]}"} -s "$IDENTITY" "$f"; n=$((n+1)) ;;
  esac
done < <(find "$APP/Contents/Resources" -type f \( -name "*.so" -o -name "*.dylib" -o -perm -u+x \) -print0)
echo "signed $n nested Mach-O files"
echo "::endgroup::"
codesign --force ${SIGN_OPTS[@]+"${SIGN_OPTS[@]}"} --entitlements "$ENT" -s "$IDENTITY" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
codesign -dv --verbose=2 "$APP" 2>&1 | grep -E "Identifier|Signature|TeamIdentifier|Runtime|flags" || true

# ── dmg ────────────────────────────────────────────────────────────────
DMGSRC="$WORK/dmg"; rm -rf "$DMGSRC"; mkdir -p "$DMGSRC"
ditto "$APP" "$DMGSRC/BUCS.app"
ln -s /Applications "$DMGSRC/Applications"
cat > "$DMGSRC/Read me first.txt" <<'TXT'
BUCS for macOS

1. Drag BUCS onto the Applications folder.
2. Open BUCS from Applications (or Launchpad). It runs in the Dock and opens the
   dashboard window; quit it from the Dock or with Cmd-Q.

If macOS says BUCS "cannot be opened" or "Apple could not verify" it:
   open System Settings > Privacy & Security, scroll down and click "Open Anyway"
   next to BUCS, then confirm. (On macOS 14 and older you can also Control-click
   BUCS in Applications and choose Open.) You only need to do this once.

Your data (licence, settings, memory, logs) lives in
   ~/Library/Application Support/BUCS
Deleting BUCS from Applications keeps that data; delete that folder to remove it too.
TXT
rm -f "$OUT/$DMG_NAME"
hdiutil create -volname "BUCS" -srcfolder "$DMGSRC" -ov -fs HFS+ -format UDZO "$OUT/$DMG_NAME"
if [ "$IDENTITY" != "-" ]; then
  codesign --force --timestamp -s "$IDENTITY" "$OUT/$DMG_NAME"
fi
if [ "$NOTARIZE" = "1" ]; then
  echo "::group::notarize"
  xcrun notarytool submit "$OUT/$DMG_NAME" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
    --password "$APPLE_APP_PASSWORD" --wait --timeout 60m
  xcrun stapler staple "$OUT/$DMG_NAME"
  xcrun stapler validate "$OUT/$DMG_NAME"
  echo "::endgroup::"
fi
echo "signing=$([ "$IDENTITY" = "-" ] && echo adhoc || echo developer-id) notarized=$NOTARIZE" > "$OUT/$DMG_NAME.signing.txt"
ls -la "$OUT"
