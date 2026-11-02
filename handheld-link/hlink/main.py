"""Handheld Link on FunKey OS: python3 -m hlink.main

Settings (environment, or KEY=value lines in /mnt/FunKey/hlink/hlink.conf):
    NAME   what the other handheld calls this one (default: the model)
    ROLE   host or device (default: host if the USB port can be one)
    NET    no: stay on ADB, never switch to the USB network link
HLINK_ROOT (the card, /mnt), HLINK_SCREEN (WxH+X+Y), HLINK_FONT and
HLINK_PORT are for testing.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time

from . import VERSION, funkey, usbnet
from .content import FunKeyContent
from .link import DeviceManager, HostManager
from .screen import Buttons, Screen
from .service import LocalSide, Server, hello_info

DATA = os.environ.get("HLINK_DATA", "/mnt/FunKey/hlink")
OPK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# the USB network drivers the OPK brings (funkey/build_net_modules.sh), and
# the script that rebuilds the USB gadget with a network function
NET_DIR = os.environ.get("HLINK_NET_DIR", os.path.join(OPK, "net"))
GADGET_SH = os.environ.get("HLINK_GADGET_SH", os.path.join(OPK, "gadget.sh"))


def settings() -> dict:
    out = {}
    try:
        with open(os.path.join(DATA, "hlink.conf")) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip().upper()] = v.strip().strip('"')
    except OSError:
        pass
    for k in ("NAME", "ROLE", "NET"):
        if os.environ.get("HLINK_" + k):
            out[k] = os.environ["HLINK_" + k]
    return out


class Log:
    def __init__(self, path: str) -> None:
        self.lock = threading.Lock()
        self.f = None
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if os.path.exists(path) and os.path.getsize(path) > 512 * 1024:
                os.replace(path, path + ".old")
            self.f = open(path, "a", buffering=1)
        except OSError:
            pass

    def __call__(self, msg: str) -> None:
        line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n"
        with self.lock:
            if self.f:
                self.f.write(line)
            else:
                sys.stderr.write(line)


def _quit(signum, frame):
    raise SystemExit(0)


def main() -> int:
    conf = settings()
    log = Log(os.path.join(DATA, "hlink.log"))
    name = conf.get("NAME") or funkey.model()
    role = conf.get("ROLE") or ("host" if funkey.can_host() else "device")
    log("start: Handheld Link %s as %s, %s" % (VERSION, name, role))
    content = FunKeyContent(os.environ.get("HLINK_ROOT", "/mnt"))
    info = hello_info(name, "funkey")
    server = Server(content, info, log)
    port = None
    net = conf.get("NET", "yes") != "no"
    if role == "host":
        port = funkey.Port(log)
        drivers = usbnet.HostDrivers(NET_DIR, log) if net else None
        manager = HostManager(server, info, log, port, drivers)
    else:
        gadget = usbnet.Gadget(GADGET_SH, log) if net else None
        manager = DeviceManager(server, info, log, funkey.rebind_gadget, gadget)
    server.note = manager.state.note
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGUSR1):
        signal.signal(sig, _quit)

    if "--serve" in sys.argv:
        # no screen: only the link and the file service (for tests)
        manager.start()
        seen = None
        try:
            while manager.is_alive():
                st = manager.state
                if (st.status, st.text) != seen:
                    seen = (st.status, st.text)
                    log("state: %s: %s" % seen)
                time.sleep(0.5)
        finally:
            manager.stop()
            manager.join(3)
            if port:
                port.release()
            log("stop")
        return 0

    from .app import Ui
    screen = Screen(funkey.screen())
    screen.take_console()
    buttons = Buttons()
    manager.start()
    try:
        Ui(screen, buttons, LocalSide(content, name), manager, name, log).run()
    except Exception as e:
        import traceback
        log("crash: " + "".join(traceback.format_exception(type(e), e, e.__traceback__)))
        raise
    finally:
        manager.stop()
        manager.join(3)
        if port:
            port.release()
        screen.fill((0, 0, 0))
        screen.flush()
        screen.close()
        buttons.close()
        log("stop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
