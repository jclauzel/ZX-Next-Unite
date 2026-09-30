"""How full is the FAT volume inside a disk image? (zxnu_config.read_image_fat_usage)

The SD Card tab's usage gauge, its "nearly full" warning and the "volume is
full" pre-flight of every transfer read the same thing. Each used to carry its
own copy of a parser that read the partition table at byte 446 of the FILE.
A .hdf is not a bare disk: it opens with an RS-IDE header (0x216 bytes in
v1.1), so that parser read the header as the MBR, found garbage and gave up.
Every .hdf showed "No image loaded" directly under its own listing (it is in
every README tour GIF), and the full-volume pre-flight never ran for one.

Covered here, headless and without hdfmonkey (CI has none):
  * every layout the reader accepts - RS-IDE v1.1 / v1.0 headers, a real MBR,
    an unpartitioned "superfloppy" volume - in FAT12, FAT16 and FAT32;
  * that it measures THE VOLUME HDFMONKEY MOUNTS: sector 0 first when it is
    a boot sector (FatFs's order), then the partition entries; FAT32 known
    by its boot-sector shape even below 65525 clusters (mkfs.fat -F 32 and
    BusyBox write those, and reading one as FAT16 pinned the gauge at ~50%
    on a full volume while hdfmonkey truncated the file being put);
  * the refusals that keep a stray byte pattern from being measured, and a
    truncated FAT refused rather than half-counted;
  * the gauge's two empty states - "No image loaded" for nothing loaded,
    "Usage unavailable" for a loaded image it cannot measure - and the two
    other readers, the nearly-full warning and the transfer pre-flight;
  * a real `hdfmonkey create` image, when hdfmonkey can be found.

Run with: python tests/test_image_usage.py
"""
import glob
import os
import shutil
import struct
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

from zxnu_config import FatUsage, read_image_fat_usage  # noqa: E402

FAIL = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + ("" if cond else f"   [{detail}]"))
    if not cond:
        FAIL.append(label)


# ── synthetic volumes ────────────────────────────────────────────────────────
EOC = {12: 0xFFF, 16: 0xFFFF, 32: 0x0FFFFFFF}


def _fat_bytes(bits, clusters):
    if bits == 12:
        return ((clusters + 1) * 3) // 2 + 2
    return (clusters + 2) * (bits // 8)


def _set_entry(fat, bits, c, value):
    if bits == 12:
        off = c * 3 // 2
        v = fat[off] | (fat[off + 1] << 8)
        if c & 1:
            v = (v & 0x000F) | ((value & 0xFFF) << 4)
        else:
            v = (v & 0xF000) | (value & 0xFFF)
        fat[off] = v & 0xFF
        fat[off + 1] = v >> 8
    elif bits == 16:
        struct.pack_into("<H", fat, c * 2, value)
    else:
        struct.pack_into("<I", fat, c * 4, value)


def volume(bits, clusters, used, *, spc=1, bps=512, media=0xF8, nfats=2,
           free_marks=None):
    """A FAT boot sector + its reserved sectors + the FATs, no data area
    (the reader never touches it). *used* clusters from 2 up are allocated;
    free_marks maps cluster -> a raw value to store in a FREE FAT32 entry."""
    rsvd = 32 if bits == 32 else 1
    root_entries = 0 if bits == 32 else 512
    fat_sz = -(-_fat_bytes(bits, clusters) // bps)
    root_sectors = (root_entries * 32 + bps - 1) // bps
    total = rsvd + nfats * fat_sz + root_sectors + clusters * spc
    b = bytearray(bps)
    b[0:3] = b"\xEB\x58\x90" if bits == 32 else b"\xEB\x3C\x90"
    b[3:11] = b"MSWIN4.1"
    struct.pack_into("<HBHBH", b, 11, bps, spc, rsvd, nfats, root_entries)
    b[21] = media
    if bits == 32:
        struct.pack_into("<I", b, 32, total)
        struct.pack_into("<I", b, 36, fat_sz)
        b[82:90] = b"FAT32   "
    else:
        if total < 0x10000:
            struct.pack_into("<H", b, 19, total)
        else:
            struct.pack_into("<I", b, 32, total)
        struct.pack_into("<H", b, 22, fat_sz)
        b[54:62] = b"FAT12   " if bits == 12 else b"FAT16   "
    b[510:512] = b"\x55\xaa"
    fat = bytearray(fat_sz * bps)
    _set_entry(fat, bits, 0, (0xFFFFF00 | media) & EOC[bits])
    _set_entry(fat, bits, 1, EOC[bits])
    for c in range(2, used + 2):
        _set_entry(fat, bits, c, EOC[bits])
    for c, raw in (free_marks or {}).items():
        _set_entry(fat, bits, c, raw)
    reserved = bytes(b) + bytes((rsvd - 1) * bps)
    return reserved + bytes(fat) * nfats


def mbr(entries, *, boot_code=b"", status=0x80):
    """A sector-0 partition table; entries = [(type, lba), ...]."""
    s = bytearray(512)
    s[0:len(boot_code)] = boot_code
    for i, (ptype, lba) in enumerate(entries):
        e = 446 + 16 * i
        s[e] = status if i == 0 else 0x00
        s[e + 4] = ptype
        struct.pack_into("<II", s, e + 8, lba, 1 << 20)
    s[510:512] = b"\x55\xaa"
    return bytes(s)


def partitioned(vol, lba=63, ptype=0x06, boot_code=b""):
    return mbr([(ptype, lba)], boot_code=boot_code) + bytes((lba - 1) * 512) + vol


def rs_ide(disk, *, offset=0x216, flags=0x00):
    h = bytearray(offset)
    h[0:7] = b"RS-IDE\x1a"
    h[7] = 0x11 if offset == 0x216 else 0x10
    h[8] = flags
    struct.pack_into("<H", h, 9, offset)
    return bytes(h) + disk


TMP = tempfile.mkdtemp(prefix="zxnu-image-usage-")


def image(name, data):
    p = os.path.join(TMP, name)
    with open(p, "wb") as f:
        f.write(data)
    return p


def expect(label, path, bits, clusters, used, cluster_bytes=512):
    got = read_image_fat_usage(path)
    want = FatUsage(clusters - used, clusters, cluster_bytes, bits)
    check(label, got == want, f"got {got}, want {want}")


# ── the layouts ──────────────────────────────────────────────────────────────
def test_layouts():
    v16 = volume(16, 5000, 1200, spc=4)
    # The demo image of the README tour: `hdfmonkey create x.hdf 64M` writes
    # exactly this - a v1.1 RS-IDE header, an MBR, a FAT16 partition at 63.
    expect("hdf v1.1 + MBR + FAT16 (hdfmonkey's own .hdf layout)",
           image("a.hdf", rs_ide(partitioned(v16))), 16, 5000, 1200, 2048)
    expect("raw .img + MBR + FAT16", image("b.img", partitioned(v16)),
           16, 5000, 1200, 2048)
    expect("raw superfloppy FAT16 (no partition table)",
           image("c.img", v16), 16, 5000, 1200, 2048)
    expect("hdf v1.0 (0x80 header) + superfloppy",
           image("d.hdf", rs_ide(v16, offset=0x80)), 16, 5000, 1200, 2048)

    v32 = volume(32, 70000, 12345, free_marks={70001: 0xF0000000})
    expect("FAT32 (70000 clusters), partition type 0x0C",
           image("e.img", partitioned(v32, lba=2048, ptype=0x0C)),
           32, 70000, 12345)
    got = read_image_fat_usage(os.path.join(TMP, "e.img"))
    check("a FAT32 entry is free on its low 28 bits alone",
          got is not None and got.free_clusters == 70000 - 12345, got)

    # FAT32 below 65525 clusters: the 16-bit FAT size of zero says FAT32, as
    # it does to Linux and to hdfmonkey. Counted as FAT16, every used entry's
    # zero upper half read as a free cluster.
    expect("a FAT32 volume below 65525 clusters is still FAT32, filled past half",
           image("e2.img", partitioned(volume(32, 60000, 45000), ptype=0x0C)),
           32, 60000, 45000)
    expect("a tiny FAT32 volume (4026 clusters, as BusyBox formats 2 MB)",
           image("e3.img", volume(32, 4026, 300)), 32, 4026, 300)
    expect("a FAT32 volume that is full reads as full, not half",
           image("e4.img", partitioned(volume(32, 60000, 60000), ptype=0x0C)),
           32, 60000, 60000)

    v12 = volume(12, 3000, 777)
    expect("FAT12 entries are 12 bits, not 16",
           image("f.img", partitioned(v12, ptype=0x01)), 12, 3000, 777)
    v12_odd = volume(12, 2999, 1)
    expect("FAT12 with an odd cluster count (last entry in a high nibble)",
           image("g.img", v12_odd), 12, 2999, 1)

    # The FAT12/16 limits are hdfmonkey's FatFs's, inclusive: 4085 is still
    # FAT12 (it writes 12-bit entries there) and 65525 still FAT16 - the
    # size a plain `hdfmonkey create next.img 2147450000B` produces.
    expect("4085 clusters is FAT12, as hdfmonkey writes it",
           image("g2.img", volume(12, 4085, 100)), 12, 4085, 100)
    expect("65525 clusters is FAT16, as hdfmonkey writes it",
           image("g3.img", partitioned(volume(16, 65525, 1000))),
           16, 65525, 1000)

    expect("the full volume reads as zero free",
           image("h.img", partitioned(volume(16, 5000, 5000))), 16, 5000, 5000)
    expect("the empty volume reads as all free",
           image("i.img", partitioned(volume(16, 5000, 0))), 16, 5000, 0)
    expect("a second partition entry is used when the first is not FAT",
           image("j.img", mbr([(0x83, 1), (0x06, 63)]) + bytes(62 * 512) + v16),
           16, 5000, 1200, 2048)
    expect("an odd boot-indicator byte does not hide the partition (FatFs "
           "ignores it)",
           image("j2.img", mbr([(0x06, 63)], status=0x01) + bytes(62 * 512) + v16),
           16, 5000, 1200, 2048)
    check("a quoted path is accepted (older cfgs stored it that way)",
          read_image_fat_usage('"' + os.path.join(TMP, "b.img") + '"')
          is not None)


def test_disambiguation():
    v16 = volume(16, 5000, 1200, spc=4)
    other = volume(16, 9000, 10, spc=2)
    # A sector 0 that is BOTH a boot sector (a jump and a whole valid BPB)
    # and a partition table: hdfmonkey's FatFs mounts sector 0 and never
    # reads the table - measured, it wrote its chain into sector 1's FAT -
    # so the gauge must measure that volume too, not the partition. (Its FAT
    # is the zero padding here, i.e. all 9000 clusters free.)
    tricky = bytearray(mbr([(0x06, 63)]))
    tricky[0:0x40] = other[0:0x40]
    expect("a sector 0 that is also a boot sector is the volume, as hdfmonkey "
           "mounts it",
           image("k.img", bytes(tricky) + bytes(62 * 512) + v16),
           16, 9000, 0, 1024)
    # The same rule from the other side: a real superfloppy boot sector whose
    # table area happens to hold a valid entry pointing at ANOTHER real
    # volume further in. Sector 0 is a boot sector, so it is the volume and
    # the entry is never consulted - a partition-first reader measured the
    # other one.
    floppy = bytearray(v16)
    floppy[446:462] = mbr([(0x06, 64)])[446:462]
    floppy += bytes(64 * 512 - len(floppy))
    expect("a boot sector's table-shaped tail is never consulted",
           image("l.img", bytes(floppy) + other), 16, 5000, 1200, 2048)

    # ...and the converse: a sector 0 that LOOKS like a boot sector but is
    # one hdfmonkey passes over sends it to the partition table, so the
    # reader must pass over it too, or it measures a phantom volume while
    # the explorer lists the partition. Three such sectors, each in front of
    # a real FAT16 partition at sector 200. (All three measured: hdfmonkey
    # refuses each as a superfloppy and mounts the partition behind it.)
    def ahead_of_partition(sector0):
        s = bytearray(sector0[:512])
        s[446:462] = mbr([(0x06, 200)])[446:462]
        s[510:512] = b"\x55\xaa"
        return bytes(s) + bytes(199 * 512) + v16

    unlabelled32 = bytearray(volume(32, 4026, 0)[:512])
    unlabelled32[82:90] = bytes(8)
    rootless12 = bytearray(volume(12, 3000, 0)[:512])
    struct.pack_into("<H", rootless12, 17, 0)
    big_sectors = bytearray(volume(16, 5000, 0)[:512])
    struct.pack_into("<H", big_sectors, 11, 1024)
    for name, s0, why in (("m1", unlabelled32, "a FAT32 BPB without its label"),
                          ("m2", rootless12, "a FAT12 BPB with no root entries"),
                          ("m3", big_sectors, "1024-byte sectors")):
        expect(f"sector 0 with {why} is passed over, as hdfmonkey does",
               image(f"{name}.img", ahead_of_partition(s0)),
               16, 5000, 1200, 2048)


def test_refusals():
    v16 = volume(16, 5000, 1200)
    none = [
        ("a halved .hdf (one byte per word) is refused, not misread",
         rs_ide(partitioned(v16), flags=0x01)),
        # The disk really does start at the offset this header names (0x10),
        # so without the guard the volume WOULD be measured: the guard is
        # what refuses a header whose data overlaps its own fields.
        ("an RS-IDE data offset inside the header itself is refused",
         rs_ide(b"", offset=0x16)[:9] + b"\x10\x00" + bytes(5)
         + partitioned(v16)),
        ("random bytes", bytes((i * 37 + 11) & 0xFF for i in range(64 * 1024))),
        ("an all-zero file", bytes(64 * 1024)),
        ("an empty file", b""),
        ("a partition pointing past the end of the file",
         mbr([(0x06, 100000)]) + bytes(4096)),
        ("a truncated FAT is refused, not half-counted",
         partitioned(v16)[:63 * 512 + 512 + 2000]),
    ]
    exfat = bytearray(512)
    exfat[0:11] = b"\xEB\x76\x90EXFAT   "
    exfat[510:512] = b"\x55\xaa"
    none.append(("exFAT (no FAT12/16/32 geometry)", bytes(exfat)))
    ntfs = bytearray(v16[:512])
    struct.pack_into("<H", ntfs, 14, 0)          # NTFS: zero reserved sectors
    none.append(("a boot sector with zero reserved sectors (NTFS)", bytes(ntfs)))
    no_jump = bytearray(v16)
    no_jump[0:3] = b"\x00\x00\x00"
    none.append(("a BPB without a jump instruction is no boot sector (FatFs)",
                 bytes(no_jump)))
    none.append(("a FAT16-shaped BPB one cluster past FatFs's limit",
                 volume(16, 65526, 0)))
    bad_bps = bytearray(v16)
    struct.pack_into("<H", bad_bps, 11, 500)
    none.append(("a sector size that is not a power of two", bytes(bad_bps)))
    tiny_fat = bytearray(v16)
    struct.pack_into("<H", tiny_fat, 22, 1)      # 1 FAT sector for 5000 clusters
    none.append(("a FAT too small for the clusters it claims", bytes(tiny_fat)))
    for i, (label, data) in enumerate(none):
        got = read_image_fat_usage(image(f"none{i}.img", data))
        check(label, got is None, got)
    check("a missing file", read_image_fat_usage(os.path.join(TMP, "nope.img")) is None)
    check("a blank path", read_image_fat_usage("") is None
          and read_image_fat_usage(None) is None)


# ── the gauge ────────────────────────────────────────────────────────────────
def test_gauge_states():
    from PySide6.QtWidgets import QApplication, QProgressBar
    import zxnu_sdcard_ops
    app = QApplication.instance() or QApplication([])  # noqa: F841

    class Host:
        pass
    host = Host()
    host.image_usage_gauge = QProgressBar()
    host.image_usage_gauge.setRange(0, 100)
    host.right_disk_image_path = ""
    import inspect
    params = inspect.signature(zxnu_sdcard_ops.build_sdcard_utils).parameters
    kwargs = {name: (lambda *a, **k: None) for name, p in params.items()
              if p.kind is inspect.Parameter.KEYWORD_ONLY}
    kwargs["configuration_dictionary"] = {}
    zxnu_sdcard_ops.build_sdcard_utils(host, **kwargs)
    gauge = host.image_usage_gauge
    update = host._update_image_usage_gauge

    good = image("gauge.hdf", rs_ide(partitioned(volume(16, 5000, 2500, spc=4))))
    host.right_disk_image_path = good
    update()                                     # None: the loaded image
    check("gauge measures the loaded .hdf", gauge.format() == "50.0 % used",
          gauge.format())
    check("gauge tooltip carries the sizes",
          "MB used / 9 MB total" in gauge.toolTip(), gauge.toolTip())
    update("")                                   # a failed load / an unload
    check('"" resets to "No image loaded", whatever path is still held',
          gauge.format() == "No image loaded", gauge.format())
    unreadable = image("gauge-unreadable.img", bytes(64 * 1024))
    update(unreadable)
    check('a loaded image it cannot measure says "Usage unavailable"',
          gauge.format() == "Usage unavailable", gauge.format())
    check("... and its tooltip says why",
          "FAT12, FAT16 or FAT32" in gauge.toolTip(), gauge.toolTip())
    host.right_disk_image_path = ""
    update()
    check("nothing loaded says so", gauge.format() == "No image loaded",
          gauge.format())

    # The two other readers of the same volume, both blind to .hdf before:
    # the transfer pre-flight, and the nearly-full warning after a load.
    full = image("full.hdf", rs_ide(partitioned(volume(16, 5000, 5000, spc=4))))
    msg = host._check_image_writable(full)
    check("the transfer pre-flight refuses a full .hdf",
          bool(msg) and msg.startswith("The image volume is full"), msg)
    check("... but not a delete, which is how space gets freed",
          host._check_image_writable(full, check_free_space=False) is None)
    check("... and lets a half-full .hdf through",
          host._check_image_writable(good) is None)
    check("... and an image it cannot measure is not refused for that",
          host._check_image_writable(unreadable) is None)

    shown = []

    class RecordingBox:
        Warning = 2
        Ok = 0x400

        def __init__(self, *_a):
            self.text = ""

        def setIcon(self, *_a):
            pass

        def setWindowTitle(self, *_a):
            pass

        def setText(self, text):
            self.text = text

        def setStandardButtons(self, *_a):
            pass

        def exec(self):
            shown.append(self.text)

    real_box = zxnu_sdcard_ops.QMessageBox
    zxnu_sdcard_ops.QMessageBox = RecordingBox
    try:
        host._warn_if_image_nearly_full(good)
        check("no nearly-full warning at 50 % used", not shown, shown)
        host._warn_if_image_nearly_full(full)
        check("the nearly-full warning fires for a full .hdf",
              len(shown) == 1 and "nearly full" in shown[0], shown)
    finally:
        zxnu_sdcard_ops.QMessageBox = real_box

    from zxnu_i18n import CATALOGS
    tip = ("The image is loaded, but how full it is could not be read: it is "
           "not a FAT12, FAT16 or FAT32 volume, or the file could not be read.")
    for lang, cat in sorted(CATALOGS.items()):
        check(f"{lang}: both new gauge strings are catalogued",
              "Usage unavailable" in cat and tip in cat)


# ── a real hdfmonkey image, where one can be made ────────────────────────────
def find_hdfmonkey():
    exe = "hdfmonkey.exe" if os.name == "nt" else "hdfmonkey"
    # Files only: on Linux/macOS the bare name also matches the
    # downloads/hdfmonkey/ FOLDER, which ** finds first.
    hits = [h for h in glob.glob(os.path.join(REPO, "downloads", "**", exe),
                                 recursive=True)
            if os.path.isfile(h) and os.access(h, os.X_OK)]
    return hits[0] if hits else shutil.which("hdfmonkey")


def test_real_hdfmonkey():
    hm = find_hdfmonkey()
    if not hm:
        print("SKIP  real hdfmonkey images (hdfmonkey not found)")
        return
    payload = os.path.join(TMP, "payload.bin")
    with open(payload, "wb") as f:
        f.write(os.urandom(3 * 1024 * 1024))
    for ext in ("hdf", "img"):
        path = os.path.join(TMP, f"real.{ext}")
        r = subprocess.run([hm, "create", path, "64M", "ZXNEXT"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            check(f"hdfmonkey create .{ext}", False, r.stdout + r.stderr)
            continue
        before = read_image_fat_usage(path)
        check(f"hdfmonkey .{ext}: the fresh volume is measured", before is not None
              and 56 * 1024 * 1024 <= before.total_clusters * before.cluster_bytes
              <= 64 * 1024 * 1024, before)
        subprocess.run([hm, "put", path, payload, "/payload.bin"],
                       capture_output=True, check=False)
        after = read_image_fat_usage(path)
        grew = (before is not None and after is not None
                and (before.free_clusters - after.free_clusters)
                * after.cluster_bytes >= 3 * 1024 * 1024)
        check(f"hdfmonkey .{ext}: a 3 MB put shows up as 3 MB less free",
              grew, (before, after))


if __name__ == "__main__":
    try:
        test_layouts()
        test_disambiguation()
        test_refusals()
        test_gauge_states()
        test_real_hdfmonkey()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print()
    print("FAILED: " + ", ".join(FAIL) if FAIL else "ALL PASSED")
    sys.exit(1 if FAIL else 0)
