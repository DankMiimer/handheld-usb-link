#!/usr/bin/env python3
"""Handheld Link's file service, content map and copy loop, offline.

Two fake FunKey cards in temp folders, the service over a socket pair.
    python3 tests/test_hlink.py
"""

import os
import socket
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "handheld-link"))

from hlink import adb, link  # noqa: E402
from hlink.content import PART, ContentError, FunKeyContent, safe_rel  # noqa: E402
from hlink.service import (REPLACE, REPLACE_ALL, SKIP, STOP, CopyUI, LocalSide, PeerError,  # noqa: E402
                           RemoteSide, Server, Client, copy_items, hello_info)
from hlink.wire import Conn, Striped  # noqa: E402

CARD_A = {
    "Game Boy Advance/Foo (USA).gba": b"gba" * 1000,
    "Game Boy Advance/Foo (USA).png": b"png",
    "Game Boy Advance/Bar.gba": b"bar",
    "Game Boy/Tetris.gb": b"tetris",
    "Game Boy Color/Crystal.gbc": b"crystal",
    "PS1/Spyro.cue": b'FILE "Spyro (Track 1).bin" BINARY\n  TRACK 01 MODE2/2352\n',
    "PS1/Spyro (Track 1).bin": b"track" * 100,
    "PICO-8/cart.p8.png": b"cart",
    "FunKey/.picoarch/data/gpsp/Foo (USA).srm": b"save",
    "FunKey/.picoarch/data/gpsp/Foo (USA).st0": b"state",
    "FunKey/.picoarch/data/gpsp/Foo (USA).st0.bmp": b"thumb",
    "FunKey/.picoarch/data/gpsp/Foo (USA).cfg": b"cfg",
    "FunKey/.picoarch/data/gpsp/last_opened.txt": b"x",
    "FunKey/.picoarch/data/gambatte/Crystal.sav": b"s1",
    "FunKey/.picoarch/data/gambatte/Crystal.rtc": b"r1",
    "FunKey/.picoarch/data/gambatte/Tetris.sav": b"s2",
    "FunKey/.picoarch/data/gambatte/Lost.sav": b"s3",
    "FunKey/.picoarch/data/prboom/DOOM1.st99": b"doom",
    "drastic/backup/Mario Kart DS.dsv": b"ds",
    "drastic/savestates/Mario Kart DS_0.dss": b"dss",
    "drastic/savestates/_savestate_temp.dss": b"tmp",
    "Native games/NRK.opk": b"opk",
    "Native games/NRK.png": b"icon",
    "Libretro/cores/prboom_libretro.so": b"so",
    "FunKey/nrk/settings.conf": b"conf",
    "FunKey/nrk/cache/x.json": b"{}",
    "FunKey/.retrofe/layouts/x": b"menu",
}


def make_card(root, files):
    for rel, data in files.items():
        p = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)


def read(root, rel):
    with open(os.path.join(root, *rel.split("/")), "rb") as f:
        return f.read()


class Answers(CopyUI):
    def __init__(self, answers=(), stop_after=None):
        self.answers = list(answers)
        self.asked = []
        self.problems = []
        self.calls = 0
        self.stop_after = stop_after

    def ask(self, name, theirs, ours):
        self.asked.append(name)
        return self.answers.pop(0) if self.answers else SKIP

    def progress(self, name, index, count, done, total):
        self.calls += 1
        return self.stop_after is None or self.calls <= self.stop_after

    def problem(self, text):
        self.problems.append(text)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.a = os.path.join(self.tmp.name, "a")
        self.b = os.path.join(self.tmp.name, "b")
        make_card(self.a, CARD_A)
        make_card(self.b, {"Game Boy Advance/Old.gba": b"old"})
        self.ca, self.cb = FunKeyContent(self.a), FunKeyContent(self.b)
        s1, s2 = socket.socketpair()
        self.server = Server(self.cb, hello_info("B", "funkey"))
        self.thread = threading.Thread(target=self.server.serve, args=(Conn(s2),), daemon=True)
        self.thread.start()
        self.client = Client(Conn(s1))
        self.local = LocalSide(self.ca, "A")
        self.remote = RemoteSide(self.client, "B")

    def tearDown(self):
        self.client.close()
        self.thread.join(2)
        self.tmp.cleanup()


class ContentTest(Base):
    def test_groups_and_items(self):
        roms = {g["id"]: g["count"] for g in self.ca.groups("roms")}
        self.assertEqual(roms, {"gba": 2, "gb": 1, "gbc": 1, "ps1": 1, "pico8": 1})
        names = [i["id"] for i in self.ca.items("roms", "ps1")]
        self.assertEqual(names, ["Spyro.cue"])          # the track is part of the .cue
        art = [i["id"] for i in self.ca.items("art", "gba")]
        self.assertEqual(art, ["Foo (USA).png"])
        self.assertEqual(self.ca.items("art", "pico8"), [])   # carts are pictures

    def test_saves_belong_to_the_system_with_the_game(self):
        gb = [i["id"] for i in self.ca.items("saves", "gb")]
        gbc = [i["id"] for i in self.ca.items("saves", "gbc")]
        self.assertEqual(sorted(gb), ["Lost", "Tetris"])      # no game anywhere: the first system
        self.assertEqual(gbc, ["Crystal"])
        self.assertEqual([f["rel"] for f in self.ca.files("saves", "gbc", "Crystal")],
                         ["Crystal.rtc", "Crystal.sav"])
        self.assertEqual([f["rel"] for f in self.ca.files("states", "gba", "Foo (USA)")],
                         ["Foo (USA).st0", "Foo (USA).st0.bmp"])
        cores = {g["id"] for g in self.ca.groups("states")}
        self.assertIn("core:prboom", cores)
        self.assertEqual([i["id"] for i in self.ca.items("saves", "nds")], ["Mario Kart DS"])
        self.assertEqual([i["id"] for i in self.ca.items("states", "nds")], ["Mario Kart DS"])

    def test_apps_and_settings(self):
        self.assertEqual([f["rel"] for f in self.ca.files("apps", "Native games", "NRK.opk")],
                         ["NRK.opk", "NRK.png"])
        self.assertIn("cores/prboom_libretro.so", [i["id"] for i in self.ca.items("apps", "Libretro")])
        appdata = [i["id"] for i in self.ca.items("settings", "appdata")]
        self.assertEqual(appdata, ["nrk"])                    # not the menu's own folders
        self.assertEqual([f["rel"] for f in self.ca.files("settings", "appdata", "nrk")],
                         ["nrk/settings.conf", "nrk/cache/x.json"])
        self.assertEqual([i["id"] for i in self.ca.items("settings", "game")], ["gpsp/Foo (USA).cfg"])

    def test_names_cannot_leave_their_folder(self):
        for bad in ("../x", "/etc/passwd", "a/../../b", "", "x" + PART, "a//b"):
            with self.assertRaises(ContentError, msg=bad):
                safe_rel(bad)
        with self.assertRaises(ContentError):
            self.cb.path("roms", "gba", "../../etc/x")
        with self.assertRaises(ContentError):
            self.cb.path("saves", "core:../..", "x")


class ServiceTest(Base):
    def test_hello_and_browse(self):
        self.assertEqual(self.client.call("hello")["name"], "B")
        self.assertEqual(self.remote.items("roms", "gba")[0]["id"], "Old.gba")
        self.assertEqual(self.remote.sizes("roms", "gba", ["Old.gba", "gone.gba"]),
                         {"Old.gba": 3, "gone.gba": None})
        self.assertEqual(self.local.sizes("roms", "ps1", ["Spyro.cue"]),
                         {"Spyro.cue": len(CARD_A["PS1/Spyro.cue"]) + 500})
        with self.assertRaises(PeerError):
            self.client.call("nonsense")
        self.assertTrue(self.client.ping(0))

    def test_send_rom_save_and_cue(self):
        ui = Answers()
        r = copy_items(self.local, self.remote, "roms", "gba",
                       [{"id": "Foo (USA).gba", "name": "Foo"}], ui)
        self.assertEqual(r["copied"], 1, r)
        self.assertEqual(read(self.b, "Game Boy Advance/Foo (USA).gba"), CARD_A["Game Boy Advance/Foo (USA).gba"])
        src = os.stat(os.path.join(self.a, "Game Boy Advance", "Foo (USA).gba")).st_mtime
        dst = os.stat(os.path.join(self.b, "Game Boy Advance", "Foo (USA).gba")).st_mtime
        self.assertEqual(int(src), int(dst))
        r = copy_items(self.local, self.remote, "saves", "gba", [{"id": "Foo (USA)", "name": "Foo"}], ui)
        self.assertEqual(r["copied"], 1, r)
        self.assertEqual(read(self.b, "FunKey/.picoarch/data/gpsp/Foo (USA).srm"), b"save")
        r = copy_items(self.local, self.remote, "roms", "ps1", [{"id": "Spyro.cue", "name": "Spyro"}], ui)
        self.assertEqual(r["copied"], 1, r)
        self.assertEqual(read(self.b, "PS1/Spyro (Track 1).bin"), b"track" * 100)
        r = copy_items(self.local, self.remote, "settings", "appdata", [{"id": "nrk", "name": "nrk"}], ui)
        self.assertEqual(read(self.b, "FunKey/nrk/cache/x.json"), b"{}")

    def test_pull_from_the_other_side(self):
        r = copy_items(self.remote, self.local, "roms", "gba", [{"id": "Old.gba", "name": "Old"}], Answers())
        self.assertEqual(r["copied"], 1, r)
        self.assertEqual(read(self.a, "Game Boy Advance/Old.gba"), b"old")

    def test_already_there_asks(self):
        make_card(self.b, {"Game Boy Advance/Bar.gba": b"older", "Game Boy Advance/Foo (USA).gba": b"x"})
        items = [{"id": "Bar.gba", "name": "Bar"}, {"id": "Foo (USA).gba", "name": "Foo"}]
        ui = Answers([SKIP, REPLACE])
        r = copy_items(self.local, self.remote, "roms", "gba", items, ui)
        self.assertEqual((r["copied"], r["skipped"]), (1, 1), r)
        self.assertEqual(ui.asked, ["Bar", "Foo"])
        self.assertEqual(read(self.b, "Game Boy Advance/Bar.gba"), b"older")
        self.assertEqual(read(self.b, "Game Boy Advance/Foo (USA).gba"), CARD_A["Game Boy Advance/Foo (USA).gba"])
        # "all" answers stick for the rest of the copy
        make_card(self.b, {"Game Boy Advance/Bar.gba": b"older", "Game Boy Advance/Foo (USA).gba": b"x"})
        ui = Answers([REPLACE_ALL])
        r = copy_items(self.local, self.remote, "roms", "gba", items, ui)
        self.assertEqual((r["copied"], len(ui.asked)), (2, 1), r)
        ui = Answers([STOP])
        r = copy_items(self.local, self.remote, "roms", "gba", items, ui)
        self.assertTrue(r["stopped"])
        self.assertEqual(r["copied"], 0)

    def test_stopping_mid_file_leaves_nothing(self):
        big = {"Game Boy Advance/Big.gba": os.urandom(700 * 1024)}
        make_card(self.a, big)
        r = copy_items(self.local, self.remote, "roms", "gba", [{"id": "Big.gba", "name": "Big"}],
                       Answers(stop_after=1))
        self.assertTrue(r["stopped"])
        names = os.listdir(os.path.join(self.b, "Game Boy Advance"))
        self.assertEqual(names, ["Old.gba"])
        # and the service still answers afterwards
        self.assertEqual(self.client.call("hello")["name"], "B")
        r = copy_items(self.local, self.remote, "roms", "gba", [{"id": "Big.gba", "name": "Big"}], Answers())
        self.assertEqual(read(self.b, "Game Boy Advance/Big.gba"), big["Game Boy Advance/Big.gba"])
        r = copy_items(self.remote, self.local, "roms", "gba", [{"id": "Big.gba", "name": "Big"}],
                       Answers([REPLACE], stop_after=1))
        self.assertTrue(r["stopped"])
        self.assertNotIn("Big.gba" + PART, os.listdir(os.path.join(self.a, "Game Boy Advance")))

    def test_no_space(self):
        self.cb.free = lambda cat, group: 10
        ui = Answers()
        r = copy_items(self.local, self.remote, "roms", "gba", [{"id": "Bar.gba", "name": "Bar"}], ui)
        self.assertTrue(r["stopped"])
        self.assertIn("Not enough space", ui.problems[0])

    def test_the_server_refuses_bad_writes(self):
        with self.assertRaises(PeerError):
            self.client.call("write", cat="roms", group="gba", rel="../escape", size=1, mtime=0)
        with self.assertRaises(PeerError):
            self.client.call("write", cat="roms", group="nope", rel="x", size=1, mtime=0)
        self.assertEqual(self.client.call("ping")["ok"], True)


class StripedTest(unittest.TestCase):
    def test_order_kept_over_several_sockets(self):
        pairs = [socket.socketpair() for _ in range(3)]
        a = Striped([p[0] for p in pairs])
        b = Striped([p[1] for p in pairs])
        data = os.urandom(300 * 1024 + 7)
        threading.Thread(target=a.sendall, args=(data,), daemon=True).start()
        got = b""
        while len(got) < len(data):
            got += b.recv(65536)
        self.assertEqual(got, data)
        # and the file service over it, both ways
        server = Server(FunKeyContent(tempfile.mkdtemp()), hello_info("B", "funkey"))
        threading.Thread(target=server.serve, args=(Conn(b),), daemon=True).start()
        client = Client(Conn(a))
        self.assertEqual(client.call("hello")["name"], "B")
        a.close()
        with self.assertRaises(ConnectionError):
            a.sendall(b"x")


class GroupTest(unittest.TestCase):
    """The device end of the link, with a stand-in host over plain TCP."""

    def test_join_serve_and_reverse(self):
        tmp = tempfile.TemporaryDirectory()
        make_card(os.path.join(tmp.name, "dev"), {"NES/Dev.nes": b"d"})
        make_card(os.path.join(tmp.name, "host"), {"NES/Host.nes": b"h"})
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        link.PORT = probe.getsockname()[1]
        probe.close()
        dev_server = Server(FunKeyContent(os.path.join(tmp.name, "dev")), hello_info("GBA Mini", "funkey"))
        m = link.DeviceManager(dev_server, hello_info("GBA Mini", "funkey"), lambda s: None)
        m.start()

        def group(role):
            socks = []
            for i in range(3):
                for _ in range(50):
                    try:
                        s = socket.create_connection(("127.0.0.1", link.PORT))
                        break
                    except OSError:
                        import time
                        time.sleep(0.05)
                Conn(s).send({"op": "join", "group": role, "index": i, "of": 3, "role": role})
                socks.append(s)
            self.assertTrue(Conn(socks[0]).recv()["ok"])
            return Conn(Striped(socks))

        client = Client(group("serve"))
        self.assertEqual(client.call("hello")["name"], "GBA Mini")
        self.assertEqual(RemoteSide(client, "x").items("roms", "nes")[0]["id"], "Dev.nes")
        host_server = Server(FunKeyContent(os.path.join(tmp.name, "host")), hello_info("RG Nano", "funkey"))
        threading.Thread(target=host_server.serve, args=(group("reverse"),), daemon=True).start()
        import time
        for _ in range(100):
            if m.state.status == link.LINKED:
                break
            time.sleep(0.05)
        self.assertEqual(m.state.status, link.LINKED)
        self.assertEqual(m.state.peer.items("roms", "nes")[0]["id"], "Host.nes")
        m.stop()
        client.close()
        m.join(3)
        tmp.cleanup()


class BridgeAdbd:
    """A stand-in adbd: tcp:PORT streams go to 127.0.0.1:PORT, with adbd's
    4 KB payloads and one unacknowledged write per stream."""

    def __init__(self):
        import queue
        self.q = queue.Queue()
        self.streams = {}       # our id -> [socket, host's id, ack event]
        self.next = 500
        self.lock = threading.Lock()

    def _pump(self, mine):
        s, host_id, acked = self.streams[mine]
        while True:
            try:
                data = s.recv(4096)
            except OSError:
                data = b""
            if not data:
                self.q.put((adb.CLSE, mine, host_id, b""))
                return
            acked.clear()
            self.q.put((adb.WRTE, mine, host_id, data))
            acked.wait(10)

    def send(self, cmd, a0, a1, data=b""):
        assert len(data) <= 4096
        if cmd == adb.CNXN:
            self.q.put((adb.CNXN, adb.VERSION, 4096, b"device::bridge\0"))
        elif cmd == adb.OPEN:
            port = int(data.rstrip(b"\0").split(b":")[1])
            try:
                s = socket.create_connection(("127.0.0.1", port))
            except OSError:
                self.q.put((adb.CLSE, 0, a0, b""))
                return
            with self.lock:
                self.next += 1
                mine = self.next
            self.streams[mine] = [s, a0, threading.Event()]
            self.q.put((adb.OKAY, mine, a0, b""))
            threading.Thread(target=self._pump, args=(mine,), daemon=True).start()
        elif cmd == adb.WRTE:
            self.streams[a1][0].sendall(data)
            self.q.put((adb.OKAY, a1, a0, b""))
        elif cmd == adb.OKAY and a1 in self.streams:
            self.streams[a1][2].set()
        elif cmd == adb.CLSE and a1 in self.streams:
            self.streams[a1][0].close()

    def recv(self):
        return self.q.get()

    def close(self):
        pass


class HostOverAdbTest(unittest.TestCase):
    """The host end's real code (ADB link, stream groups) against the device end."""

    def test_link_and_copy_both_ways(self):
        tmp = tempfile.TemporaryDirectory()
        big = os.urandom(600 * 1024)
        make_card(os.path.join(tmp.name, "dev"), {"NES/Dev.nes": big})
        make_card(os.path.join(tmp.name, "host"), {"NES/Host.nes": big[::-1]})
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        link.PORT = probe.getsockname()[1]
        probe.close()
        dev = link.DeviceManager(Server(FunKeyContent(os.path.join(tmp.name, "dev")),
                                        hello_info("GBA Mini", "funkey")),
                                 hello_info("GBA Mini", "funkey"), lambda s: None)
        dev.start()
        host_local = LocalSide(FunKeyContent(os.path.join(tmp.name, "host")), "RG Nano")
        host = link.HostManager(Server(host_local.content, hello_info("RG Nano", "funkey")),
                                hello_info("RG Nano", "funkey"), lambda s: None, port=None)
        import time
        time.sleep(0.3)
        a = adb.Link(BridgeAdbd())
        a.connect()
        conn = host._group(a, "serve")
        client = Client(conn)
        self.assertEqual(client.call("hello")["name"], "GBA Mini")
        back = host._group(a, "reverse")
        threading.Thread(target=host.server.serve, args=(back,), daemon=True).start()
        for _ in range(100):
            if dev.state.status == link.LINKED:
                break
            time.sleep(0.05)
        self.assertEqual(dev.state.status, link.LINKED)
        peer = RemoteSide(client, "GBA Mini")
        r = copy_items(host_local, peer, "roms", "nes", [{"id": "Host.nes", "name": "h"}], Answers())
        self.assertEqual(r["copied"], 1, r)
        self.assertEqual(read(os.path.join(tmp.name, "dev"), "NES/Host.nes"), big[::-1])
        r = copy_items(peer, host_local, "roms", "nes", [{"id": "Dev.nes", "name": "d"}], Answers())
        self.assertEqual(r["copied"], 1, r)
        self.assertEqual(read(os.path.join(tmp.name, "host"), "NES/Dev.nes"), big)
        # the device end asks the host end too, over the reverse group
        self.assertEqual(sorted(i["id"] for i in dev.state.peer.items("roms", "nes")),
                         ["Dev.nes", "Host.nes"])
        dev.stop()
        client.close()
        dev.join(3)
        tmp.cleanup()


class NetworkLinkTest(unittest.TestCase):
    """From ADB to the USB network link: the host asks, the device adds its
    network function and listens, the host moves both connections to TCP.
    Loopback addresses stand in for the link's, fakes for the kernel parts."""

    def test_switch_to_the_network_link(self):
        import time
        from hlink import usbnet

        class FakeGadget:
            added = False

            def possible(self):
                return True

            def on(self):
                self.added = True
                return "lo"

            def off(self):
                self.added = False

        class FakeDrivers:
            loaded = False

            def possible(self):
                return True

            def load(self):
                self.loaded = True

            def interface(self, sysfs):
                return "lo"

            def unload(self):
                self.loaded = False

        saved = (usbnet.DEV_IP, usbnet.HOST_IP, usbnet.address, link.REPLUG_WAIT)
        usbnet.DEV_IP, usbnet.HOST_IP = "127.0.0.2", "127.0.0.1"
        usbnet.address = lambda name, ip: None
        link.REPLUG_WAIT = 0.3
        tmp = tempfile.TemporaryDirectory()
        big = os.urandom(900 * 1024)
        make_card(os.path.join(tmp.name, "dev"), {"NES/Dev.nes": b"d"})
        make_card(os.path.join(tmp.name, "host"), {"NES/Big.nes": big})
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        link.PORT = probe.getsockname()[1]
        probe.close()
        logs = []
        gadget = FakeGadget()
        dev = link.DeviceManager(Server(FunKeyContent(os.path.join(tmp.name, "dev")),
                                        hello_info("GBA Mini", "funkey")),
                                 hello_info("GBA Mini", "funkey"), logs.append, gadget=gadget)
        dev.start()
        host_local = LocalSide(FunKeyContent(os.path.join(tmp.name, "host")), "RG Nano")
        drivers = FakeDrivers()
        host = link.HostManager(Server(host_local.content, hello_info("RG Nano", "funkey")),
                                hello_info("RG Nano", "funkey"), logs.append, port=None, net=drivers)
        usb = {"serial": "gba", "sysfs": "/nowhere"}
        try:
            time.sleep(0.3)
            first = adb.Link(BridgeAdbd())
            first.connect()
            host._session(first, usb)               # asks for the network link
            self.assertTrue(any("asked" in m for m in logs), logs)
            for _ in range(50):
                if dev.server.info["net"].get("ip"):
                    break
                time.sleep(0.1)
            self.assertEqual(dev.server.info["net"].get("ip"), "127.0.0.2")
            self.assertTrue(gadget.added)
            second = adb.Link(BridgeAdbd())
            second.connect()
            t = threading.Thread(target=host._session, args=(second, usb), daemon=True)
            t.start()
            for _ in range(100):
                if host.state.status == link.LINKED and dev.state.status == link.LINKED:
                    break
                time.sleep(0.05)
            self.assertEqual(host.state.status, link.LINKED)
            self.assertTrue(drivers.loaded)
            self.assertTrue(any("over the USB network" in m for m in logs), logs)
            r = copy_items(host_local, host.state.peer, "roms", "nes", [{"id": "Big.nes", "name": "b"}], Answers())
            self.assertEqual(r["copied"], 1, r)
            self.assertEqual(read(os.path.join(tmp.name, "dev"), "NES/Big.nes"), big)
            # and the other way, on the device's own connection
            self.assertEqual(sorted(i["id"] for i in dev.state.peer.items("roms", "nes")), ["Big.nes"])
        finally:
            host.stop()
            dev.stop()
            dev.join(3)
            usbnet.DEV_IP, usbnet.HOST_IP, usbnet.address, link.REPLUG_WAIT = saved
            tmp.cleanup()
        self.assertFalse(gadget.added)              # taken out again when the app stops


class Writes(bytearray):
    """A stand-in framebuffer that counts the rows written to it."""

    def __init__(self, n, line):
        super().__init__(n)
        self.line = line
        self.rows = 0

    def __setitem__(self, key, value):
        if isinstance(key, slice):
            self.rows += (key.stop - key.start) // self.line
        super().__setitem__(key, value)


class ScreenCostTest(unittest.TestCase):
    """The screen is only redrawn and sent to the panel where it changed."""

    def test_only_changed_rows_go_out(self):
        os.environ["HLINK_HEADLESS"] = "240x160"
        from hlink.screen import Screen
        from hlink import app
        s = Screen()
        s.mm = Writes(s.line * s.h, s.line)
        tmp = tempfile.TemporaryDirectory()
        names = {"Game Boy Advance/%s %02d (USA, Europe) (En,Fr,De,Es,It).gba" % ("Pokemon Mystery Dungeon", i): b""
                 for i in range(20)}
        make_card(tmp.name, names)

        class St:
            status, text, activity, activity_at, version = "linked", "Linked", "", 0.0, 0
            peer_info = {"name": "GBA Mini"}

        class M:
            port = None
            state = St()

        local = LocalSide(FunKeyContent(tmp.name), "RG Nano")
        M.state.peer = local
        ui = app.Ui(s, None, local, M(), "RG Nano")
        ui.side, ui.cat, ui.group = 0, "roms", {"id": "gba", "name": "Game Boy Advance"}
        ui.draw()                                         # (first "Loading", then the list)
        s.mm.rows = 0
        ui.draw()
        self.assertEqual(s.mm.rows, s.h)                  # a full redraw sends it all
        self.assertIsNotNone(ui.mq)                       # the selected name is too long
        s.mm.rows = 0
        ui.step_marquee()                                 # before a second: nothing moves
        ui.redraw_footer()                                # unchanged: nothing goes out
        self.assertEqual(s.mm.rows, 0)
        ui.sel_since -= 1.5
        ui.step_marquee()                                 # scrolling: only that row
        self.assertTrue(0 < s.mm.rows <= ui.row_h, s.mm.rows)
        ui.sel_since -= 60
        ui.step_marquee()                                 # one pass, then it stops
        self.assertIsNone(ui.mq)
        M.state.activity, M.state.activity_at = "Receiving x.gba", __import__("time").monotonic()
        s.mm.rows = 0
        ui.redraw_footer()                                # a notice: only the bottom line
        self.assertTrue(0 < s.mm.rows <= ui.foot_h, s.mm.rows)
        os.environ.pop("HLINK_HEADLESS")
        tmp.cleanup()


class AdbTest(unittest.TestCase):
    def test_bulk_ioctl_matches_the_struct(self):
        import ctypes
        size = 16 if ctypes.sizeof(ctypes.c_void_p) == 4 else 24
        self.assertEqual(adb.USBDEVFS_BULK, 0xC0005502 | (size << 16))
        self.assertEqual(adb.USBDEVFS_CLAIMINTERFACE, 0x8004550F)

    def test_streams_over_a_fake_device(self):
        """A Link against a fake adbd that echoes what a tcp: stream gets."""
        dev = FakeAdbd()
        link = adb.Link(dev)
        link.connect()
        self.assertEqual(link.banner, "device::fake")
        s = link.open("tcp:55441")
        s.settimeout(5)
        data = os.urandom(20000)
        s.sendall(data)
        got = b""
        while len(got) < len(data):
            got += s.recv(65536)
        self.assertEqual(got, data)
        with self.assertRaises(ConnectionRefusedError):
            link.open("tcp:1")
        s.close()
        dev.unplug()
        with self.assertRaises(Exception):
            link.open("tcp:55441", timeout=1)


class FakeAdbd:
    """The device end of ADB, in memory: tcp:55441 echoes, others refuse."""

    def __init__(self):
        import queue
        self.q = queue.Queue()
        self.peer_ids = {}
        self.next = 100
        self.gone = False

    def send(self, cmd, a0, a1, data=b""):
        if self.gone:
            raise adb.LinkDown("unplugged")
        if cmd == adb.CNXN:
            self.q.put((adb.CNXN, adb.VERSION, 4096, b"device::fake\0"))
        elif cmd == adb.OPEN:
            if data.rstrip(b"\0") == b"tcp:55441":
                self.next += 1
                self.peer_ids[self.next] = a0
                self.q.put((adb.OKAY, self.next, a0, b""))
            else:
                self.q.put((adb.CLSE, 0, a0, b""))
        elif cmd == adb.WRTE:
            assert len(data) <= 4096
            self.q.put((adb.OKAY, a1, a0, b""))
            self.q.put((adb.WRTE, a1, a0, data))

    def recv(self):
        item = self.q.get()
        if item is None:
            raise adb.LinkDown("unplugged")
        return item

    def unplug(self):
        self.gone = True
        self.q.put(None)

    def close(self):
        pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
