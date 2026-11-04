#!/usr/bin/env bash
# Package the staged Linux build as BUCS-Linux-x64.AppImage.
#   python tools/desktop/build.py --target linux-x64 && tools/desktop/linux/build_appimage.sh dist
set -euo pipefail
OUT="$(mkdir -p "$1" && cd "$1" && pwd)"
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HERE="$ROOT/tools/desktop/linux"
WORK="$ROOT/build/desktop/linux-x64"
STAGE="$WORK/stage"
VERSION="$(cat "$WORK/version.txt")"
APPDIR="$WORK/AppDir"
rm -rf "$APPDIR"; mkdir -p "$APPDIR/usr"
cp -a "$STAGE/python" "$STAGE/launcher" "$STAGE/app" "$STAGE/build.json" "$APPDIR/usr/"
install -m 755 "$HERE/AppRun" "$APPDIR/AppRun"
python3 "$ROOT/tools/desktop/make_icon.py" "$WORK/icon"
cp "$WORK/icon/BUCS.iconset/icon_256x256.png" "$APPDIR/bucs.png"
cp "$APPDIR/bucs.png" "$APPDIR/.DirIcon"
cat > "$APPDIR/bucs.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=BUCS
Comment=Bobbiey Unified Command System
Exec=AppRun
Icon=bucs
Terminal=false
Categories=Utility;
X-AppImage-Version=$VERSION
EOF
find "$APPDIR" -name "__pycache__" -type d -prune -exec rm -rf {} +

TOOL="$WORK/appimagetool"
if [ ! -x "$TOOL" ]; then
  curl -fsSL -o "$TOOL" https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
  chmod +x "$TOOL"
fi
rm -f "$OUT/BUCS-Linux-x64.AppImage"
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream "$APPDIR" "$OUT/BUCS-Linux-x64.AppImage"
chmod +x "$OUT/BUCS-Linux-x64.AppImage"
echo "signing=unsigned (AppImage; verify with SHA256SUMS.txt)" > "$OUT/BUCS-Linux-x64.AppImage.signing.txt"
ls -la "$OUT"
