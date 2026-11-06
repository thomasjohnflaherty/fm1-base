#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
# Copyright (C) 2026 Charles Vestal
"""tools/fm1_backup.py against a simulated FM-1 (no hardware, no mido). Its stored set is the image the device's code
made in tests/backup_test.c (device/backup.c's format: "TSTB", version 3, 18 parts); it streams a backup in backup.c's
frames (128 bytes, then the end frame) and pulls a restore as restore_rx() does (the head, checked as the device
checks it, each record, its payload in 512s, the tail, then the question and the result). Checked: the backup written
byte for byte, asked with the app's magic (--magic, or KNOWN by the identity; another magic: no answer); a restore sent
whole and taken only on SEL; playing (STOP FIRST), HOME, a damaged file (nothing sent), another version's (the FM-1
refuses it), another firmware's on a KNOWN one (nothing sent), a firmware's own tool (a Kind: its identity, its
version), an FM-1 that stops asking. FakeFM1 and Link are what tests/backup_record.py and a firmware's own tests
import.
    python3 tests/backup_tool_test.py IMAGE"""
import io
import os
import queue
import shutil
import struct
import sys
import tempfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import fm1_install as I  # noqa: E402
import fm1_backup as B  # noqa: E402


class Link:
    def __init__(self, dev):
        self.dev, self.q, self.lost = dev, queue.Queue(), False

    def send(self, pkt):
        self.dev.rx(self, bytes(pkt))
        return True

    def read(self, timeout):
        try:
            return self.q.get(timeout=max(timeout, 0))
        except queue.Empty:
            return None

    def drain(self):
        while not self.q.empty():
            self.q.get_nowait()

    def close(self):
        pass


def dev_frame(cmd, body):
    """backup.c's bu_frame"""
    n = len(body)
    return b"\xF0" + I.pack7(bytes([0, 0x59, cmd, n & 0xFF, n >> 8, 0]) + body + bytes([~sum(body) & 0xFF])) + b"\xF7"


class FakeFM1:
    """an FM-1 whose app keeps image's objects: its magic, version and count are the image's (the app's own)"""

    def __init__(self, image, identity="FM-1_1100", playing=False, answer=True, stall_after=None, flags=0,
                 port="TEMPLATE FM-1"):
        self.image, self.identity, self.playing, self.answer = image, identity, playing, answer
        self.stall_after, self.flags, self.port = stall_after, flags, port
        self.magic, self.version, self.nobj = image[:4], *struct.unpack_from("<HH", image, 4)
        self.req, self.asked, self.pull, self.wanted, self.reads, self.backups = None, 0, None, None, 0, 0
        self.area = None

    def input_names(self):
        return [self.port]

    output_names = input_names

    def open(self, i, o):
        return Link(self)

    # ---- backup: the stored image in 128-byte frames, then the end frame (bu_step)
    def stream(self, link):
        img = bytearray(self.image)
        self.backups += 1
        if self.flags:                            # (playing: flagged in the head)
            img[8] = self.flags
            img[12:16] = struct.pack("<I", zlib.crc32(img[:12]))
            img[-4:] = struct.pack("<I", zlib.crc32(img[:-4]))
        for off in range(0, len(img), 128):
            link.q.put(dev_frame(B.BU_REQ, struct.pack("<I", off) + bytes(img[off:off + 128])))
        link.q.put(dev_frame(B.BU_END, struct.pack("<IIBB", len(img), zlib.crc32(img), 0, self.flags)))

    # ---- restore: restore_rx()'s reads
    def puller(self, n, crc):
        if self.playing:
            yield (B.FAIL_ADDR | B.PLAYING, 8)
            return
        got = bytearray((yield (0, 16)))
        magic, ver, cnt, _, hcrc = struct.unpack_from("<4sHHII", got)
        if magic != self.magic or ver != self.version or cnt != self.nobj or hcrc != zlib.crc32(got[:12]):
            yield (B.FAIL_ADDR | 3, 8)                # RS_E_HEAD
            return
        pos = 16
        for _ in range(self.nobj):
            r = yield (pos, 8)
            got += r
            ln = struct.unpack_from("<H", r, 2)[0]
            pos += 8
            for k in range(0, ln, 512):
                got += (yield (pos + k, min(512, ln - k)))
            pos += ln
        got += (yield (pos, 4))
        if len(got) != n or zlib.crc32(got) != crc:
            yield (B.FAIL_ADDR | 6, 8)
            return
        try:
            B.parse(bytes(got))
        except B.BackupError:
            yield (B.FAIL_ADDR | 5, 8)
            return
        yield (B.ASK_ADDR, 8)
        self.asked += 1
        if not self.answer:
            yield (B.FAIL_ADDR | B.DECLINED, 8)
            return
        self.image = bytes(got)
        yield (B.OK_ADDR, 8)

    def ask(self, link, addr, n):
        q = bytes([0]) + addr.to_bytes(4, "little") + bytes([n & 0xFF, n >> 8, 0])
        link.q.put(b"\xF0" + I.pack7(bytes([0, 0x59, 0x30, 8, 0, 0]) + q + bytes([~sum(q) & 0xFF])) + b"\xF7")
        self.wanted = (addr, n)

    def next(self, link, data):
        try:
            addr, n = self.pull.send(data)
        except StopIteration:
            self.pull = None
            return
        self.ask(link, addr, n)

    def rx(self, link, d):
        if d == I.HS_QUERY:
            t = self.identity.encode()
            link.q.put(b"\xF0" + I.pack7(bytes([0, 0x59, 0x11, 0, 0, 0]) + t + bytes(28 - len(t))) + b"\xF7")
            return
        f = B.parse_frame(d)
        if f and self.area and f[0] == self.area.cmd:
            self.area.rx(link, f[1])
            return
        if f and f[0] == B.BU_REQ and f[1] == self.magic:   # (main_fm1.c: only its app's magic)
            self.stream(link)
            return
        if f and f[0] == B.RS_REQ and len(f[1]) == 8:   # ota_take()'s checks passed; the request
            self.req = struct.unpack("<II", f[1])
            self.pull = self.puller(*self.req)
            self.next(link, None)
            return
        u = I.unpack7(d[1:-1])
        if self.pull and self.wanted and len(u) >= 15 and u[2] == 0x30:
            addr, n = self.wanted
            if int.from_bytes(u[7:11], "little") != addr or u[11] | u[12] << 8 != n:
                return
            self.reads += 1
            if self.stall_after is not None and self.reads > self.stall_after:
                return                            # (stops asking)
            self.next(link, bytes(u[14:14 + n]))


class FakeArea:
    """a firmware's flash area (fm1_backup.py class Area; Hapax port/hapax/pat.c): 19 sectors, the window after a yes, the mark"""
    N, S = 19, 4096

    def __init__(self, cmd, raw, state=0, window=True, saves=0):
        self.cmd, self.raw, self.state, self.window, self.saves = cmd, bytearray(raw), state, window, saves
        self.marked, self.ended = None, 0

    def send(self, link, body):
        link.q.put(dev_frame(self.cmd, bytes(body)))

    def rx(self, link, b):
        op = b[0]
        if op == 0:
            self.send(link, struct.pack("<BBbBII", 0, 0, self.state, self.window, len(self.raw), self.saves) + bytes(12))
            if self.saves:
                self.saves -= 1
        elif op == 1:
            off, n = struct.unpack_from("<IH", b, 1)
            self.send(link, struct.pack("<BBHII", 1, 0, n, 0, off) + bytes(self.raw[off:off + n]))
        elif op == 2:
            size, z = struct.unpack_from("<IB", b, 1)
            if not self.window:
                self.send(link, [2, 2])
                return
            self.marked = z
            self.raw[:] = b"\xff" * len(self.raw)
            self.raw[z * self.S:z * self.S + 4] = b"@LG1"
            self.send(link, [2, 0])
        elif op == 3:
            off = struct.unpack_from("<I", b, 1)[0]
            self.raw[off:off + len(b) - 5] = b[5:]
            self.send(link, [3, 0])
        elif op == 4:
            c = bytearray(self.raw)
            c[self.marked * self.S:(self.marked + 1) * self.S] = b"\xff" * self.S
            if zlib.crc32(c) != struct.unpack_from("<I", b, 1)[0]:
                self.send(link, [4, 5])
                return
            self.raw[:] = c
            self.marked, self.ended = None, self.ended + 1
            self.send(link, [4, 0])


def fake_log():
    """a log image: sectors 3, 4 and 5 hold a head with seq 22, 23, 24; the rest is blank"""
    raw = bytearray(b"\xff" * (FakeArea.N * FakeArea.S))
    for s, seq in ((3, 22), (4, 23), (5, 24)):
        head = b"PLG1" + struct.pack("<III", 7, seq, 0)
        raw[s * FakeArea.S:s * FakeArea.S + 20] = head + struct.pack("<I", zlib.crc32(head))
        raw[s * FakeArea.S + 40:s * FakeArea.S + 60] = bytes(range(s, s + 20))
    return bytes(raw)


def fast():
    I.DELAY.update(open=0, hs=0.1, retry=0.02)
    B.DELAY.update(poll=0.02, idle=0.3, ask=0.5, first=0.3)


def main(img_path):
    failed = 0

    def ok(cond, what):
        nonlocal failed
        print(f"{what:<66} {'ok' if cond else 'FAIL'}")
        failed += not cond

    fast()
    img = open(img_path, "rb").read()
    flags, objs = B.parse(img)
    ok(len(objs) == 18 and img[:4] == b"TSTB" and objs[0][0] == 0 and objs[1][0] == 2,
       "the device's image (backup_test) parses: TSTB, 18 parts")
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "fm1.bak")
    run = lambda argv, dev, **k: B.main(argv, backend=dev, out=k.pop("out", io.StringIO()), **k)  # noqa: E731

    dev = FakeFM1(img)
    out = io.StringIO()
    rc = run(["backup", path, "--magic", "TSTB"], dev, out=out)
    ok(rc == 0 and open(path, "rb").read() == img and "done" in out.getvalue(), "backup: exit 0, the file byte for byte")
    out = io.StringIO()
    ok(B.main(["info", path], out=out) == 0 and "TSTB, version 3" in out.getvalue() and "object 2" in out.getvalue(),
       "info: the file's parts")
    dev = FakeFM1(img)
    ok(run(["backup", path + "x"], dev) == 2 and not dev.backups and not os.path.exists(path + "x"),
       "backup without --magic, an FM-1 not KNOWN: exit 2, nothing asked")
    dev = FakeFM1(img)
    ok(run(["backup", path + "x", "--magic", "PLYB"], dev) == I.EXIT["stopped"] and not os.path.exists(path + "x"),
       "another magic: the FM-1 does not answer (exit 4), nothing written")
    polly = B.build(objs, magic=b"PLYB", version=2)
    dev = FakeFM1(polly, identity="FM-1_300", port="Polly FM-1")
    ok(run(["backup", path + "p"], dev) == 0 and open(path + "p", "rb").read() == polly,
       "a KNOWN firmware (Polly's identity): its magic, no --magic needed")
    out = io.StringIO()
    dev = FakeFM1(img, flags=1)
    rc = run(["backup", path + "2", "--magic", "TSTB"], dev, out=out)
    ok(rc == 0 and "playing" in out.getvalue(), "backup while playing: written, the note said")

    other = B.build([(o, d[::-1] if d else b"x" * 10) for o, d in objs], magic=b"TSTB", version=3)
    dev = FakeFM1(other)
    out = io.StringIO()
    rc = run(["restore", path, "--yes"], dev, out=out)
    ok(rc == 0 and dev.image == img and "done" in out.getvalue(), "restore: exit 0, the device holds the backup")
    ok(dev.req == (len(img), zlib.crc32(img)) and dev.asked == 1, "... the request: its size and CRC-32; asked once")
    ok("SEL yes" in out.getvalue(), "... the host says to answer on the FM-1")

    # a file with the firmware's own sections (the web page's EXTRAS) after the backup's CRC-32
    secs = [("LSMP", bytes(range(256)) * 9), ("ZZZZ", b"")]
    withsec = B.join(img, secs)
    ok(B.split(withsec) == (img, secs) and B.split(img) == (img, []), "split / join: the backup and its sections")
    open(path + ".sec", "wb").write(withsec)
    dev = FakeFM1(other)
    out = io.StringIO()
    rc = run(["restore", path + ".sec", "--yes"], dev, out=out)
    ok(rc == 0 and dev.image == img and dev.req == (len(img), zlib.crc32(img)) and "LSMP, ZZZZ" in out.getvalue(),
       "restore of a file with sections: the backup alone sent, the sections named")
    out = io.StringIO()
    ok(B.main(["info", path + ".sec"], out=out) == 0 and "section LSMP 2304 bytes" in out.getvalue(),
       "info: the sections listed")
    for cut, what in ((withsec[:-1], "cut short"), (withsec[:len(img) + 20] + bytes([withsec[len(img) + 20] ^ 1]) + withsec[len(img) + 21:], "damaged")):
        open(path + ".secbad", "wb").write(cut)
        dev = FakeFM1(other)
        ok(run(["restore", path + ".secbad", "--yes"], dev) == 2 and dev.req is None,
           f"a section {what}: refused (exit 2), nothing sent")

    dev = FakeFM1(other, playing=True)
    ok(run(["restore", path, "--yes"], dev) == B.EXIT["playing"] and dev.image == other,
       "playing: STOP FIRST (exit 5), nothing changed")
    dev = FakeFM1(other, answer=False)
    ok(run(["restore", path, "--yes"], dev) == 1 and dev.image == other and dev.asked == 1,
       "HOME on the FM-1: exit 1, nothing changed")
    bad = bytearray(img)
    bad[len(bad) // 2] ^= 1
    open(path + ".bad", "wb").write(bad)
    dev = FakeFM1(other)
    ok(run(["restore", path + ".bad", "--yes"], dev) == 2 and dev.req is None,
       "a damaged file: refused here (exit 2), nothing sent")
    old = bytearray(img)                          # version 2, its CRCs right: the FM-1 (version 3) refuses it
    struct.pack_into("<H", old, 4, 2)
    struct.pack_into("<I", old, 12, zlib.crc32(old[:12]))
    struct.pack_into("<I", old, len(old) - 4, zlib.crc32(old[:-4]))
    open(path + ".v2", "wb").write(old)
    dev = FakeFM1(other)
    ok(run(["restore", path + ".v2", "--yes"], dev) == 1 and dev.req and not dev.asked and dev.image == other,
       "another version's backup: the FM-1 refuses its head (exit 1), nothing changed")
    dev = FakeFM1(polly, identity="FM-1_3000400", port="Polly FM-1")
    ok(run(["restore", path, "--yes"], dev) == I.EXIT["model"] and dev.req is None and dev.image == polly,
       "another firmware's backup on a KNOWN one (Polly): refused (exit 6), nothing sent")
    dev = FakeFM1(other, stall_after=5)
    ok(run(["restore", path, "--yes"], dev) == I.EXIT["stopped"] and dev.image == other,
       "the FM-1 stops asking: exit 4, nothing changed")
    dev = FakeFM1(other)
    ok(run(["restore", path], dev, ask=lambda p: False) == 1 and dev.req is None,
       "not confirmed here: cancelled, nothing sent")

    # a firmware's own tool: its kind (magic, version, parts, identity)
    kind = B.Kind(b"TSTB", 3, [o for o, _ in objs], lambda o: f"part {o}", "FM-1_11", "Template")
    dev = FakeFM1(img)
    ok(run(["backup", path + "k"], dev, kind=kind) == 0 and open(path + "k", "rb").read() == img,
       "a Kind: backup with its magic, no --magic")
    dev = FakeFM1(other, identity="FM-1_906")
    ok(run(["restore", path, "--yes"], dev, kind=kind) == I.EXIT["model"] and dev.req is None,
       "a Kind: another identity (X0X's) refused (exit 6)")
    try:
        B.parse(bytes(old), kind)
        said = ""
    except B.BackupError as e:
        said = str(e)
    ok("another version of Template" in said, "a Kind: another version said so: " + said[:40])
    try:
        B.parse(polly, kind)
        said = ""
    except B.BackupError as e:
        said = str(e)
    ok(said == "not a Template backup", "a Kind: another magic: " + said)
    # a firmware's flash area: a section through the firmware's own command
    B.AREAS[b"TSTB"] = area = B.Area("PLOG", "the log", 0x60, FakeArea.N * FakeArea.S)
    area.WAIT.update(answer=0.1, erase=0.2)
    raw = fake_log()
    dev = FakeFM1(img)
    dev.area = FakeArea(0x60, raw)
    ok(run(["backup", path + "a", "--magic", "TSTB"], dev) == 0, "area: backup")
    data = open(path + "a", "rb").read()
    image, sections = B.split(data)
    ok(image == img and len(sections) == 1 and sections[0][0] == "PLOG" and sections[0][1][8:] == raw, "area: the file holds the raw area")
    ok(area.blank(raw) == 6, "area: the mark goes on the sector after the newest")
    dev = FakeFM1(other)
    dev.area = FakeArea(0x60, b"\x55" * len(raw))
    ok(run(["restore", path + "a", "--yes"], dev) == 0 and bytes(dev.area.raw) == raw and dev.area.ended == 1, "area: a restore writes it back")
    dev = FakeFM1(img)
    dev.area = FakeArea(0x60, raw, saves=1)
    ok(run(["backup", path + "b", "--magic", "TSTB"], dev) == 0 and dev.backups == 2, "area: a save during the backup takes it again")
    dev = FakeFM1(img)
    dev.area = FakeArea(0x60, raw, saves=9)
    ok(run(["backup", path + "b2", "--magic", "TSTB"], dev) != 0 and dev.backups == 3, "area: a firmware that keeps saving: no file")
    dev = FakeFM1(img)
    ok(run(["backup", path + "c", "--magic", "TSTB"], dev) == 0 and open(path + "c", "rb").read() == img, "area: a firmware with no area: no section")
    dev = FakeFM1(other)
    dev.area = FakeArea(0x60, raw)
    keep = bytes(dev.area.raw)
    ok(run(["restore", path + "c", "--yes"], dev) == 0 and dev.image == img and bytes(dev.area.raw) == keep, "area: an old backup restores, the area stays")
    dev = FakeFM1(other)
    dev.area = FakeArea(0x60, b"\x55" * len(raw), window=False)
    ok(run(["restore", path + "a", "--yes"], dev) != 0 and dev.area.raw == bytearray(b"\x55" * len(raw)), "area: the firmware refuses: an error, nothing written")
    foreign = B.join(img, [("PLOG", struct.pack("<BbHI", 1, -3, 0, len(raw)) + b"\x55" * len(raw))])
    with open(path + "f", "wb") as f:
        f.write(foreign)
    dev = FakeFM1(other)
    dev.area = FakeArea(0x60, raw)
    ok(run(["restore", path + "f", "--yes"], dev) == 0 and bytes(dev.area.raw) == raw, "area: foreign bytes in a file are not written back")
    # an area of an old size: refused with a message, and the objects restore
    area.old = (21 * FakeArea.S,)
    wide = b"\xff" * (21 * FakeArea.S)
    with open(path + "o", "wb") as f:
        f.write(B.join(img, [("PLOG", struct.pack("<BbHI", 1, 0, 0, len(wide)) + wide)]))
    dev = FakeFM1(other)
    dev.area = FakeArea(0x60, raw)
    ok(run(["restore", path + "o", "--yes"], dev) == 0, "area: an old size: the restore runs")
    ok(dev.image == img and bytes(dev.area.raw) == raw and dev.area.ended == 0, "area: an old size: the objects restore, the area stays")
    area.old = ()
    # a section that the firmware will refuse: found before any object goes
    full = bytearray(raw)
    for s in range(FakeArea.N):
        if s not in (3, 4, 5):
            full[s * FakeArea.S:(s + 1) * FakeArea.S] = raw[3 * FakeArea.S:4 * FakeArea.S]
    bad_head = bytearray(raw)
    bad_head[3 * FakeArea.S + 17] ^= 1
    two_logs = bytearray(raw)
    head = b"PLG1" + struct.pack("<III", 8, 25, 0)
    two_logs[4 * FakeArea.S:4 * FakeArea.S + 20] = head + struct.pack("<I", zlib.crc32(head))
    for what, body in (("another size", b"\xff" * 4096), ("no blank sector", bytes(full)), ("a bad sector head", bytes(bad_head)),
                       ("two logs", bytes(two_logs)), ("no log", b"\xff" * len(raw))):
        with open(path + "s", "wb") as f:
            f.write(B.join(img, [("PLOG", struct.pack("<BbHI", 1, 0, 0, len(body)) + body)]))
        dev = FakeFM1(other)
        dev.area = FakeArea(0x60, b"\x55" * len(raw))
        ok(run(["restore", path + "s", "--yes"], dev) != 0 and dev.req is None and dev.image == other and dev.area.raw == bytearray(b"\x55" * len(raw)),
           "area: " + what + ": refused before any object goes")
    del B.AREAS[b"TSTB"]
    shutil.rmtree(tmp)
    print("backup tool test %s" % ("FAILED" if failed else "passed"))
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
