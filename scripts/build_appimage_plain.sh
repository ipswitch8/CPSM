#!/bin/bash
# Build CPSM's AppImage with plain appimagetool instead of appimage-builder.
#
# WHY THIS EXISTS
#
# appimage-builder's AppRun LD_PRELOADs libapprun_hooks.so, which leaks
# ~413 MB/day under CPSM's workload. Measured A/B (scripts/diagnose_appimage_leak.sh):
#
#     hooks INTACT     +1176 KB / 240s  ->  413.4 MB/day
#     hooks STUBBED       +0 KB / 240s  ->    0.0 MB/day
#
# The leak is independent of anything CPSM does -- an empty document still leaks
# 406 MB/day -- so it cannot be fixed in application code. See
# docs/MEMORY-LEAK-INVESTIGATION.md.
#
# plain appimagetool does none of that. It takes an AppDir and squashes it into
# a runtime. No LD_PRELOAD shim, no bundled glibc, no ELF interpreter rewriting,
# no environment munging. The AppRun below is four lines of shell.
#
# WHAT THIS TRADES AWAY
#
# appimage-builder bundles system libraries and a compat glibc so the result
# runs on older distributions. This does not: the PyInstaller bundle ships
# Python and Qt, but anything it links from the host (libc, libstdc++, libGL,
# libssl) must be present and new enough on the target. In practice that means
# the AppImage requires a host of roughly the build machine's vintage or newer.
#
# That is the deliberate trade: portability across old distros, in exchange for
# not shipping a memory leak. If wide portability matters more, build on the
# oldest distribution you intend to support rather than reintroducing the shim.
#
# REQUIREMENTS
#   - PyInstaller one-folder build at dist/cpsm/ (python -m PyInstaller packaging/cpsm.spec)
#   - appimagetool; fetched to .build-tools/ automatically if absent
#
# USAGE
#   CPSM_VERSION=0.2.0 scripts/build_appimage_plain.sh
#
# Output: CPSM-<version>-x86_64.AppImage at the repo root.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

VERSION="${CPSM_VERSION:?CPSM_VERSION must be set (e.g. CPSM_VERSION=0.2.0)}"
APPDIR="$REPO/AppDir-plain"
TOOL="$REPO/.build-tools/appimagetool"
OUT="CPSM-${VERSION}-x86_64.AppImage"

[ -x dist/cpsm/cpsm ] || {
    echo "ERROR: dist/cpsm/cpsm not found. Run:"
    echo "  ./.venv/bin/python -m PyInstaller --noconfirm packaging/cpsm.spec"
    exit 1
}

if [ ! -x "$TOOL" ]; then
    echo "fetching appimagetool ..."
    mkdir -p "$(dirname "$TOOL")"
    curl -sSL -o "$TOOL" \
      https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x "$TOOL"
fi

echo "building AppDir ..."
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" \
         "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/scalable/apps" \
         "$APPDIR/usr/share/cpsm"

cp -r dist/cpsm/. "$APPDIR/usr/bin/"
cp cpsm/resources/icons/cpsm.svg "$APPDIR/usr/share/icons/hicolor/scalable/apps/cpsm.svg"
cp cpsm/resources/icons/cpsm.svg "$APPDIR/cpsm.svg"
# AGPLv3 sections 4 and 6 require conveying the licence with the binary.
cp LICENSE   "$APPDIR/usr/share/cpsm/LICENSE"
cp README.md "$APPDIR/usr/share/cpsm/README.md"

# The entire fix, in four lines: exec the binary directly. No LD_PRELOAD, no
# hook library, no interpreter rewriting, no environment rewriting.
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
export PATH="$HERE/usr/bin:$PATH"
exec "$HERE/usr/bin/cpsm" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/net.itwerx.cpsm.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=CPSM
GenericName=Session Manager
Comment=Cross-Platform Session Manager
Exec=cpsm gui %u
Icon=cpsm
Terminal=false
Categories=Development;System;TerminalEmulator;
X-AppImage-Arch=x86_64
X-AppImage-Version=${VERSION}
X-AppImage-Name=cpsm
DESKTOP
cp "$APPDIR/net.itwerx.cpsm.desktop" \
   "$APPDIR/usr/share/applications/net.itwerx.cpsm.desktop"

echo "running appimagetool ..."
rm -f "$OUT"
# ARCH is required; appimagetool cannot infer it from an AppDir alone.
ARCH=x86_64 "$TOOL" "$APPDIR" "$OUT"

echo
ls -la "$OUT"
echo
echo "Verify it does NOT carry the hook shim:"
echo "  ./$OUT --appimage-extract >/dev/null && \\"
echo "    find squashfs-root -name 'libapprun_hooks.so' | grep . || echo 'clean: no hook shim'"
echo
echo "Then measure it. NOTE: scripts/diagnose_appimage_leak.sh does NOT work on"
echo "this build -- it is written against an appimage-builder AppDir and aborts"
echo "with 'hook library missing' precisely because this build has no shim,"
echo "which is the point. Measure the running process directly instead:"
echo
echo "    ./$OUT gui &"
echo "    sleep 150                       # discard startup allocation"
echo "    P=\$(pgrep -f 'cpsm gui' | tail -1)"
echo "    A=\$(pmap -X \$P | tail -1 | awk '{print \$2+\$13}')"
echo "    sleep 300"
echo "    B=\$(pmap -X \$P | tail -1 | awk '{print \$2+\$13}')"
echo "    awk -v a=\$A -v b=\$B 'BEGIN{printf \"%.1f MB/day\\n\", (b-a)*86400/300/1024}'"
echo
echo "Reference: the appimage-builder build measured 413-460 MB/day; this one"
echo "measured 3.4 MB/day. See docs/MEMORY-LEAK-INVESTIGATION.md."
