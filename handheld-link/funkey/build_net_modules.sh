#!/bin/sh
# Builds the USB network host drivers Handheld Link loads on the RG Nano,
# for its factory kernel (FunKey OS by DrUm78, 4.14.14-funkey): mii, usbnet,
# cdc_ether and cdc_ncm. With them the Nano, as the USB host, gets a network
# link to the other handheld's NCM gadget, much faster than ADB.
#
#   funkey/build_net_modules.sh                 build into $HLINK_NET_MODULES
#   funkey/build_net_modules.sh --audit ZIMAGE  ...and check every symbol the
#                                               modules import against a
#                                               kernel image (/boot/zImage
#                                               pulled off the Nano)
#
# The kernel source is DrUm78's (commit below), built with kernel/config (the
# factory configuration) plus these drivers as modules; nothing built into
# the kernel changes. The modules only load into kernels listed in
# kernel/kernels (funkey.py checks uname). Needs the FunKey SDK (FUNKEY_SDK,
# default ~/FunKey-sdk-2.3.0), git, make, gcc, bc, and lzop for --audit.
# LINUX_SRC=<dir> uses a kernel tree you already have; by default the one
# rg-nano-adb-tools' Wi-Fi build fetched is reused if it is there.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
SDK="${FUNKEY_SDK:-$HOME/FunKey-sdk-2.3.0}"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}"
WORK="${HLINK_KWORK:-$CACHE/handheld-usb-link/kernel}"
OUT="${HLINK_NET_MODULES:-$CACHE/handheld-usb-link/net}"
T=arm-funkey-linux-musleabihf
J="$(nproc 2>/dev/null || echo 4)"
LINUX_COMMIT=1f6d3fc2f17bc1d31aa2cf89d5642da8bbf4517b
LINUX_GIT=https://github.com/DrUm78/linux.git
VERMAGIC="4.14.14-funkey SMP mod_unload ARMv7 p2v8 "
# 4.14 builds everything with -fcommon; the module loader refuses COMMON
# symbols, so modules get -fno-common on top
KFLAGS="-march=armv7-a -mcpu=cortex-a7 -fno-common"
MODULES="mii usbnet cdc_ether cdc_ncm"
export PATH="$SDK/bin:$PATH"

die() { echo "build_net_modules: $*" >&2; exit 1; }
kmake() { make ARCH=arm CROSS_COMPILE=$T- HOSTCFLAGS=-fcommon KCFLAGS="$KFLAGS" "$@"; }

AUDIT=""
case "${1:-}" in
  "") ;;
  --audit) AUDIT="${2:-}"; [ -f "$AUDIT" ] || die "--audit needs the Nano's /boot/zImage" ;;
  *) sed -n '2,19p' "$0"; exit 2 ;;
esac
command -v $T-gcc >/dev/null || die "FunKey SDK not found (set FUNKEY_SDK; looked in $SDK)"
mkdir -p "$WORK"

L="${LINUX_SRC:-}"
if [ -z "$L" ]; then
  if [ -f "$CACHE/rg-nano-adb-tools/wifi/linux/.fetched-$LINUX_COMMIT" ]; then
    L="$CACHE/rg-nano-adb-tools/wifi/linux"
  else
    L="$WORK/linux"
    if [ ! -f "$L/.fetched-$LINUX_COMMIT" ]; then
      # git checks every object against the commit hash
      rm -rf "$L"; mkdir -p "$L"
      ( cd "$L" && git init -q && git fetch -q --depth 1 "$LINUX_GIT" "$LINUX_COMMIT" \
        && git -c advice.detachedHead=false checkout -q FETCH_HEAD ) || die "could not fetch the kernel source"
      rm -rf "$L/.git"
      touch "$L/.fetched-$LINUX_COMMIT"
    fi
  fi
fi
echo "kernel source: $L"

K="$WORK/kbuild-net"
rm -rf "$K"; mkdir -p "$K"
{
  grep -v -E '^(# )?CONFIG_(USB_USBNET|USB_NET_|MII)[ =]' "$HERE/kernel/config"
  echo CONFIG_USB_USBNET=m
  echo CONFIG_USB_NET_CDCETHER=m
  echo CONFIG_USB_NET_CDC_NCM=m
  for c in AX8817X AX88179_178A CDC_EEM CDC_MBIM DM9601 SR9700 SR9800 SMSC75XX SMSC95XX \
           GL620A NET1080 PLUSB MCS7830 RNDIS_HOST CDC_SUBSET ZAURUS CX82310_ETH KALMIA QMI_WWAN \
           INT51X1 CH9200 HUAWEI_CDC_NCM; do
    echo "# CONFIG_USB_NET_$c is not set"
  done
} > "$K/.config"
kmake -C "$L" O="$K" olddefconfig >/dev/null
# every option of the factory configuration must come out the same: only
# the new modules may differ
grep '^CONFIG_' "$HERE/kernel/config" | sort > "$WORK/factory.cfg"
grep '^CONFIG_' "$K/.config" | sort > "$WORK/net.cfg"
changed="$(comm -23 "$WORK/factory.cfg" "$WORK/net.cfg")"
[ -z "$changed" ] || die "the configuration changed factory options: $changed"
added="$(comm -13 "$WORK/factory.cfg" "$WORK/net.cfg" | grep -v -E '^CONFIG_(USB_USBNET|USB_NET_CDCETHER|USB_NET_CDC_NCM|MII)=m$' || true)"
[ -z "$added" ] || die "the configuration added more than the network modules: $added"

kmake -C "$L" O="$K" -j"$J" modules_prepare > "$WORK/net.log" 2>&1 || { tail -20 "$WORK/net.log"; die "modules_prepare failed"; }
# mii lives in drivers/net with every other driver: build just it, outside
M="$WORK/mii"
rm -rf "$M"; mkdir -p "$M"
cp "$L/drivers/net/mii.c" "$M/"
echo 'obj-m := mii.o' > "$M/Makefile"
kmake -C "$L" O="$K" M="$M" -j"$J" modules >> "$WORK/net.log" 2>&1 || { tail -20 "$WORK/net.log"; die "mii failed"; }
kmake -C "$L" O="$K" M=drivers/net/usb KBUILD_EXTRA_SYMBOLS="$M/Module.symvers" -j"$J" modules \
  >> "$WORK/net.log" 2>&1 || { tail -20 "$WORK/net.log"; die "the USB network drivers failed"; }

rm -rf "$OUT"; mkdir -p "$OUT"
for m in $MODULES; do
  f="$K/drivers/net/usb/$m.ko"
  [ "$m" = mii ] && f="$M/mii.ko"
  [ -f "$f" ] || die "$m.ko was not built"
  v="$(modinfo -F vermagic "$f")"
  [ "$v" = "$VERMAGIC" ] || die "unexpected vermagic [$v] in $m.ko"
  [ -z "$($T-nm "$f" | awk '$2 == "C"')" ] || die "COMMON symbols in $m.ko (the loader refuses them)"
  $T-strip --strip-debug -o "$OUT/$m.ko" "$f"
  echo "$m.ko: needs [$(modinfo -F depends "$f")]"
done
sed 's/\r$//' "$HERE/kernel/kernels" > "$OUT/kernels"

if [ -n "$AUDIT" ]; then
  command -v lzop >/dev/null || die "lzop is required for --audit"
  A="$WORK/audit"
  rm -rf "$A"; mkdir -p "$A"
  # the image wraps an LZO payload; try each lzop magic, keep what decompresses
  for off in $(python3 -c 'import sys
b = open(sys.argv[1], "rb").read(); i = b.find(b"\x89LZO\0\r\n\x1a\n")
while i >= 0: print(i); i = b.find(b"\x89LZO\0\r\n\x1a\n", i + 1)' "$AUDIT"); do
    tail -c +$((off + 1)) "$AUDIT" > "$A/payload.lzo"
    lzop -d -f -o "$A/vmlinux" "$A/payload.lzo" 2>/dev/null || true   # trailing data makes lzop fail
    grep -aq "Linux version" "$A/vmlinux" 2>/dev/null && break
  done
  # shellcheck disable=SC2046   # one argument per module, on purpose
  python3 "$HERE/kernel/audit_exports.py" "$A/vmlinux" $(for m in $MODULES; do echo "$OUT/$m.ko"; done)
fi
echo "modules in $OUT"
