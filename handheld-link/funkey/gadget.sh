#!/bin/sh
# Handheld Link's USB gadget on FunKey OS, for the network link to the USB
# host:
#
#   gadget.sh net     ADB + an NCM network function (in place of the serial
#                     console: the USB controller has too few endpoints for
#                     all three); prints the network interface's name
#   gadget.sh stock   ADB + serial console, as FunKey OS builds it at boot
#
# Done the way USB Link (rg-nano-adb-tools) does it: the running gadget and
# adbd come down completely first, since changing the gadget under a running
# adbd leaves USB dead until a reboot. If the network gadget won't bind, the
# stock one is built again and the exit status is 1.
G=/sys/kernel/config/usb_gadget/FunKey
C="$G/configs/FunKey.1"
FFS=/dev/usb-ffs/adb
NET=ncm.hlink
DEV_MAC="${HLINK_DEV_MAC:-02:68:6c:00:00:02}"
HOST_MAC="${HLINK_HOST_MAC:-02:68:6c:00:00:01}"
SERIAL="$(sed -n 's/^Serial[[:space:]]*: *//p' /proc/cpuinfo)"
UDC="$(ls /sys/class/udc 2>/dev/null | head -n 1)"

teardown() {
    echo "" > "$G/UDC" 2>/dev/null
    killall adbd 2>/dev/null
    sleep 1
    umount "$FFS" 2>/dev/null
    for l in "$C/acm.GS0" "$C/ffs.adb" "$C/$NET"; do
        [ -L "$l" ] && rm "$l"
    done
    rmdir "$C/strings/0x409" "$C" "$G/functions/acm.GS0" "$G/functions/ffs.adb" \
          "$G/functions/$NET" "$G/strings/0x409" "$G" 2>/dev/null
}

# build net|stock; returns 1 if it won't bind
build() {
    modprobe libcomposite
    modprobe usb_f_fs
    mkdir "$G" "$G/strings/0x409" "$C" "$C/strings/0x409"
    if [ "$1" = net ]; then
        FUNC=$NET
        modprobe usb_f_ncm
        mkdir "$G/functions/$FUNC"
        echo "$DEV_MAC" > "$G/functions/$FUNC/dev_addr"
        echo "$HOST_MAC" > "$G/functions/$FUNC/host_addr"
        CFG="ADB+NCM"
    else
        FUNC=acm.GS0
        modprobe usb_f_serial
        modprobe usb_f_acm
        mkdir "$G/functions/$FUNC"
        CFG="ADB+CDC"
    fi
    mkdir "$G/functions/ffs.adb"
    echo 0x8087 > "$G/idVendor"
    echo 0x011e > "$G/idProduct"
    echo "Intel" > "$G/strings/0x409/manufacturer"
    echo "FunKey S" > "$G/strings/0x409/product"
    echo "$SERIAL" > "$G/strings/0x409/serialnumber"
    echo 120 > "$C/MaxPower"
    echo "$CFG" > "$C/strings/0x409/configuration"
    # the network (or serial) function first, so ADB stays interface 2
    ln -s "$G/functions/$FUNC" "$C/"
    ln -s "$G/functions/ffs.adb" "$C/"
    mkdir -p "$FFS"
    mount -t functionfs adb "$FFS"
    setsid adbd </dev/null >/dev/null 2>&1 &
    # adbd is ready once it has written its descriptors and the endpoints exist
    i=0
    while [ ! -e "$FFS/ep1" ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i + 1)); done
    sleep 1
    echo "$UDC" > "$G/UDC" 2>/dev/null
}

case "${1:-}" in
    net)
        teardown
        if build net; then
            cat "$G/functions/$NET/ifname"
            exit 0
        fi
        echo "gadget: the network gadget won't bind: $(dmesg | tail -n 2 | tr '\n' ' ')" >&2
        teardown
        build stock
        exit 1
        ;;
    stock)
        teardown
        build stock
        ;;
    *)
        sed -n '2,13p' "$0"
        exit 2
        ;;
esac
