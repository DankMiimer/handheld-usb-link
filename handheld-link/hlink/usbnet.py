"""A USB network link between the two handhelds, for speed.

ADB through an old adbd moves 4 KB per round trip, which keeps an RG Nano's
processor busy at about 1.2 MB/s. So once the ADB link is up, the host asks
the other handheld to rebuild its USB gadget as ADB + a CDC NCM network
function (funkey/gadget.sh); the host loads the USB network drivers built
for its kernel (funkey/build_net_modules.sh), and the two talk TCP over a
private /30. Whatever fails on the way, the link stays on ADB.
"""

from __future__ import annotations

import glob
import os
import subprocess
import time

DEV_IP = os.environ.get("HLINK_NET_DEV", "169.254.217.2")     # the USB device end
HOST_IP = os.environ.get("HLINK_NET_HOST", "169.254.217.1")   # the USB host end
BITS = 30
GADGETS = "/sys/kernel/config/usb_gadget"
HOST_MODULES = ["mii", "usbnet", "cdc_ether", "cdc_ncm"]


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def _run(*cmd: str) -> str | None:
    """Run a command; None if it worked, else what it said."""
    try:
        p = subprocess.run(list(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        return str(e)
    return None if p.returncode == 0 else (p.stdout.decode(errors="replace").strip() or "failed")


def address(ifname: str, ip: str) -> str | None:
    """Give an interface this link's address (and only it), and bring it up."""
    _run("ip", "addr", "flush", "dev", ifname)
    return (_run("ip", "addr", "add", "%s/%d" % (ip, BITS), "dev", ifname)
            or _run("ip", "link", "set", ifname, "up"))


class Gadget:
    """The USB device end: ADB + an NCM network function while the app runs,
    FunKey OS's own ADB + serial console gadget again afterwards.

    script is funkey/gadget.sh, which rebuilds the gadget the way USB Link
    does (adbd comes down and up with it). Only done when the gadget is
    FunKey OS's stock one: never under USB Link's network mode."""

    def __init__(self, script: str, log=lambda s: None) -> None:
        self.script = script
        self.log = log
        self.added = False
        self.ifname: str | None = None

    def possible(self) -> bool:
        g = os.path.join(GADGETS, "FunKey")
        c = os.path.join(g, "configs", "FunKey.1")
        links = sorted(os.listdir(c)) if os.path.isdir(c) else []
        functions = [n for n in links if "." in n and os.path.islink(os.path.join(c, n))]
        if functions != ["acm.GS0", "ffs.adb"]:
            return False                # not the stock gadget (USB Link's network mode, say)
        pid = _read("/tmp/usblink-watch.pid")
        if pid.isdigit() and os.path.isdir("/proc/" + pid):
            return False                # USB Link's watcher would rebuild it under us
        if not os.path.isfile(self.script):
            return False
        return bool(glob.glob("/lib/modules/%s/kernel/drivers/usb/gadget/function/usb_f_ncm.ko"
                              % os.uname().release)) or os.path.isdir("/sys/module/usb_f_ncm")

    def _script(self, what: str) -> tuple[int, str]:
        try:
            p = subprocess.run(["sh", self.script, what], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            return 1, str(e)
        if p.stderr:
            self.log("net: " + p.stderr.decode(errors="replace").strip())
        return p.returncode, p.stdout.decode(errors="replace").strip()

    def on(self) -> str | None:
        """Rebuild the gadget with the network function and address it; its
        interface name, or None (the stock gadget is back then)."""
        if not self.added:
            rc, out = self._script("net")
            if rc != 0:
                self.log("net: the network gadget didn't come up; back to the stock gadget")
                return None
            self.added = True
            self.ifname = out.splitlines()[-1] if out else None
            self.log("net: USB gadget is ADB + network (%s)" % self.ifname)
        if not self.ifname:
            return None
        err = address(self.ifname, DEV_IP)
        if err:
            self.log("net: could not address %s: %s" % (self.ifname, err))
            return None
        return self.ifname

    def off(self) -> None:
        """The stock gadget again (only if this app changed it)."""
        if not self.added:
            return
        rc, _ = self._script("stock")
        self.added = False
        self.ifname = None
        self.log("net: USB gadget is back to ADB + serial%s" % ("" if rc == 0 else " (the bind failed)"))


class HostDrivers:
    """The USB host end: the network drivers this app brings for its kernel."""

    def __init__(self, moddir: str, log=lambda s: None) -> None:
        self.moddir = moddir
        self.log = log
        self.loaded: list[str] = []

    def possible(self) -> bool:
        if not all(os.path.isfile(os.path.join(self.moddir, m + ".ko")) for m in HOST_MODULES):
            return False
        # only into the kernels the modules were built and checked for
        me = "%s %s" % (os.uname().release, os.uname().version)
        with open(os.path.join(self.moddir, "kernels")) as f:
            return me in (line.strip() for line in f)

    def load(self) -> str | None:
        for m in HOST_MODULES:
            if os.path.isdir("/sys/module/" + m):
                continue
            err = _run("insmod", os.path.join(self.moddir, m + ".ko"))
            if err:
                return "%s: %s" % (m, err)
            self.loaded.append(m)
            self.log("net: loaded %s" % m)
        return None

    def unload(self) -> None:
        for m in reversed(self.loaded):
            _run("rmmod", m)
        self.loaded = []

    def interface(self, sysfs_dev: str, wait: float = 6.0) -> str | None:
        """The network interface the drivers made for this USB device."""
        end = time.monotonic() + wait
        while True:
            nets = glob.glob(os.path.join(sysfs_dev, os.path.basename(sysfs_dev) + ":*", "net", "*"))
            if nets:
                return os.path.basename(sorted(nets)[0])
            if time.monotonic() > end:
                return None
            time.sleep(0.3)
