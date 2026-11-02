#!/bin/sh
# Builds HandheldLink.opk for FunKey OS (DrUm78's builds for the RG Nano, the
# GBA Mini, the FunKey S and clones):
#
#   funkey/build_opk.sh          -> funkey/HandheldLink.opk
#
# Copy it to /mnt/Applications on each handheld.
#
# FunKey OS has no Python, so the OPK brings one: a Python 3.12 runtime built
# for it (32-bit ARM, musl). HLINK_PY_RUNTIME points at it; by default the one
# rg-nano-adb-tools' wifi/build_wifi_opk.sh --build-only leaves in
# ~/.cache/rg-nano-adb-tools/wifi/runtime. It also brings the RG Nano's USB
# network drivers (HLINK_NET_MODULES, from funkey/build_net_modules.sh).
# Needs mksquashfs and python3.12 (the app's .pyc are compiled with it).
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/.."
OUT="$HERE/HandheldLink.opk"
RT="${HLINK_PY_RUNTIME:-$HOME/.cache/rg-nano-adb-tools/wifi/runtime}"
# the RG Nano's USB network drivers, from funkey/build_net_modules.sh; without
# them the link stays on ADB
NET="${HLINK_NET_MODULES:-$HOME/.cache/handheld-usb-link/net}"
PY="$(command -v python3.12 || command -v python3)"

die() { echo "build_opk: $*" >&2; exit 1; }
command -v mksquashfs >/dev/null || die "mksquashfs is required"
"$PY" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))' || die "python3.12 is required"
[ -x "$RT/bin/python3" ] || die "no Python runtime in $RT (set HLINK_PY_RUNTIME)"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/rt"
cp -a "$RT/bin" "$RT/lib" "$STAGE/rt/"
rm -f "$STAGE/rt/bin/qjs"
# sourceless .pyc: the OPK is read-only, so nothing could cache them later
mkdir "$STAGE/hlink"
cp "$SRC/hlink/"*.py "$STAGE/hlink/"
"$PY" -m compileall -b -q -d /hlink "$STAGE/hlink" >/dev/null
rm "$STAGE/hlink/"*.py
rm -rf "$STAGE/hlink/__pycache__"
if [ -f "$NET/cdc_ncm.ko" ] && [ -f "$NET/kernels" ]; then
  mkdir "$STAGE/net"
  cp "$NET/"*.ko "$NET/kernels" "$STAGE/net/"
else
  echo "build_opk: no USB network drivers in $NET (run funkey/build_net_modules.sh): ADB only" >&2
fi
for f in run.sh gadget.sh; do
  sed 's/\r$//' "$HERE/$f" > "$STAGE/$f"
  chmod +x "$STAGE/$f"
done
"$PY" "$SRC/tools/make_icon.py" "$STAGE/hlink.png"
cat > "$STAGE/default.funkey-s.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Handheld Link
Comment=Send ROMs, saves and apps to another handheld over USB
Exec=run.sh
Icon=hlink
Terminal=false
Categories=applications;
EOF
mksquashfs "$STAGE" "$OUT" -all-root -noappend -no-exports -no-xattrs -comp gzip >/dev/null
echo "built $OUT ($(wc -c < "$OUT") bytes)"
