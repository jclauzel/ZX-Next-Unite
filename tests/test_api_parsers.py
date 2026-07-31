"""Unit tests for zxnu_api — the pure online-catalogue API layer.

No Qt, no network: every function under test is a pure parser / URL builder,
which is exactly why the layer was extracted out of zx-next-unite.py.

Run with: python tests/test_api_parsers.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from zxnu_api import (  # noqa: E402
    _filter_download_urls,
    _http_fetch_bytes_with_retry,
    _zxdb_media_mirror_url,
    getit_parse_detail,
    getit_parse_file_list,
    getit_resolve_starter_pack,
    zxart_entry_website_url,
    zxart_parse_picture_list,
    zxart_parse_prod_list,
    zxart_safe_url,
    zxdb_entry_website_url,
    zxdb_parse_game_detail,
    zxdb_parse_search,
    zxdb_pick,
)

FAIL = []
def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)

# ---- GetIt caret format -----------------------------------------------------
entries, total, page, pages = getit_parse_file_list(
    "^R^2^id1^Title One^Author A^48K^Games^id2^Title Two^Author B^128K^Demos^END^")
check("getit list: two entries", len(entries) == 2, str(entries))
check("getit list: total", total == 2, str(total))
check("getit list: fields",
      entries[0] == {"id": "id1", "title": "Title One", "author": "Author A",
                     "size": "48K", "category": "Games"}, str(entries[:1]))
entries, total, _p, _tp = getit_parse_file_list("garbage without marker")
check("getit list: malformed -> empty", entries == [] and total == 0)

detail = getit_parse_detail(
    "^IDID^42^TITL^My Game^DESC^Line1\r\nLine2^URL^http://example.com/x.tap^")
check("getit detail: tags", detail.get("IDID") == "42" and detail.get("TITL") == "My Game",
      str(detail))
check("getit detail: DESC newlines flattened", detail.get("DESC") == "Line1 Line2",
      repr(detail.get("DESC")))
check("getit detail: URL", detail.get("URL") == "http://example.com/x.tap",
      str(detail.get("URL")))

# ---- GetIt curated starter pack ---------------------------------------------
from zxnu_config import GETIT_STARTER_PACK, GETIT_STARTER_PACK_IMAGE_DIR  # noqa: E402

check("starter pack: 20 curated titles", len(GETIT_STARTER_PACK) == 20,
      str(len(GETIT_STARTER_PACK)))
check("starter pack: ids unique",
      len({i for i, _t in GETIT_STARTER_PACK}) == len(GETIT_STARTER_PACK))
check("starter pack: titles unique (casefold)",
      len({t.casefold() for _i, t in GETIT_STARTER_PACK})
      == len(GETIT_STARTER_PACK))
check("starter pack: image dir is absolute",
      GETIT_STARTER_PACK_IMAGE_DIR.startswith("/")
      and not GETIT_STARTER_PACK_IMAGE_DIR.endswith("/"))

_cat = [
    {"id": "aa1", "title": "Sonic Spectrum Next"},
    {"id": "bb2", "title": "Wonderful Dizzy"},
    {"id": "cc3", "title": "Operation Jeff DX"},   # prefix-only match
    {"id": "dd4", "title": "Unrelated"},
]
_found, _missing = getit_resolve_starter_pack(
    _cat, pack=(("aa1", "Sonic Spectrum Next"),      # id hit
                ("zz9", "wonderful dizzy"),          # title hit, new id
                ("yy8", "Operation Jeff"),           # unique prefix hit
                ("xx7", "Retired Title")))           # gone
check("starter resolve: id match", _found and _found[0]["id"] == "aa1",
      str(_found[:1]))
check("starter resolve: title fallback survives a re-upload (new id)",
      len(_found) > 1 and _found[1]["id"] == "bb2", str(_found[1:2]))
check("starter resolve: unique title prefix matches",
      len(_found) > 2 and _found[2]["id"] == "cc3", str(_found[2:3]))
check("starter resolve: retired entry reported missing",
      _missing == [("xx7", "Retired Title")], str(_missing))
_found2, _missing2 = getit_resolve_starter_pack(
    [{"id": "aa1", "title": "Same Title"},
     {"id": "bb2", "title": "Same Title X"}],
    pack=(("aa1", "Same Title"), ("qq0", "Same Title")))
check("starter resolve: an entry is never taken twice",
      [e["id"] for e in _found2] == ["aa1", "bb2"] and _missing2 == [],
      f"{_found2} {_missing2}")
_found3, _missing3 = getit_resolve_starter_pack(
    [{"id": "a", "title": "Brickz! (Invaderz)"},
     {"id": "b", "title": "Brickz! (Geometrica)"}],
    pack=(("zz", "Brickz!"),))
check("starter resolve: ambiguous prefix is a miss, never a guess",
      _found3 == [] and _missing3 == [("zz", "Brickz!")],
      f"{_found3} {_missing3}")

# ---- ZXDB -------------------------------------------------------------------
check("zxdb_pick: first non-empty",
      zxdb_pick({"a": "", "b": None, "c": "hit"}, "a", "b", "c") == "hit")
check("zxdb_pick: default", zxdb_pick({}, "a", default="dflt") == "dflt")

es_payload = {
    "hits": {
        "total": {"value": 2},
        "hits": [
            {"_id": "0001234", "_score": 1.5,
             "_source": {"title": "Foo Fighter", "authors": [{"name": "Jane"}]}},
        ],
    },
}
entries, total, _page, _tp, _ps = zxdb_parse_search(es_payload)
check("zxdb search (ES): one entry", len(entries) == 1, str(entries))
check("zxdb search (ES): total", total == 2, str(total))
check("zxdb search (ES): id from _id", entries[0].get("id") == "0001234", str(entries[:1]))
check("zxdb search (ES): title", entries[0].get("title") == "Foo Fighter", str(entries[:1]))
check("zxdb search (ES): author resolved", "Jane" in str(entries[0].get("author")),
      str(entries[:1]))

flat_payload = {"items": [{"id": "9", "title": "Bar"}], "total": 1}
entries, total, _page, _tp, _ps = zxdb_parse_search(flat_payload)
check("zxdb search (flat): entry + total", len(entries) == 1 and total == 1,
      f"{entries} total={total}")
check("zxdb search: non-dict payload safe", zxdb_parse_search(None)[0] == [])

check("zxdb website url: zero-padded",
      zxdb_entry_website_url(1234) == "https://zxinfo.dk/details/0001234",
      zxdb_entry_website_url(1234))
check("zxdb website url: non-numeric passthrough",
      zxdb_entry_website_url("AB12").endswith("/AB12"), zxdb_entry_website_url("AB12"))
check("zxdb website url: empty", zxdb_entry_website_url("") == "")

# ---- ZXDB media-host routing ------------------------------------------------
# Root-relative asset paths from the ZXInfo API must all resolve against
# zxinfo.dk/media (the primary media host). Regression guard for the retro
# item viewer's "Loading text…" hang: /pub + /zxdb assets briefly routed to
# spectrumcomputing.co.uk, which later became unreachable.
_detail = zxdb_parse_game_detail({
    "_id": "0035805",
    "_source": {
        "title": "Willy's New Mansion - Special Edition",
        "screens": [
            {"url": "/zxscreens/0035805/0035805-load-1.png", "type": "Loading screen"},
        ],
        "additionalDownloads": [
            {"path": "/zxdb/sinclair/entries/0035805/0035805-run-1.scr",
             "type": "Running screen", "format": "Screen dump (SCR)"},
            {"path": "/pub/sinclair/games-info/w/WillysNewMansion-SpecialEdition.txt",
             "type": "Instructions", "format": "Document (TXT)"},
        ],
        "releases": [{"yearOfRelease": 2016, "files": [
            {"path": "/zxdb/sinclair/entries/0035805/WillysNewMansion-SpecialEdition.tap.zip",
             "type": "Tape image", "format": "Tape (TAP)"},
        ]}],
    },
})
_shot_urls = [s["url"] for s in _detail.get("screenshots", [])]
check("zxdb media routing: all screenshots on zxinfo.dk/media",
      _shot_urls and all(u.startswith("https://zxinfo.dk/media/") for u in _shot_urls),
      str(_shot_urls))
_txt_urls = [t["url"] for t in _detail.get("text_files", [])]
check("zxdb media routing: instructions .txt on zxinfo.dk/media",
      _txt_urls == ["https://zxinfo.dk/media/pub/sinclair/games-info/w/"
                    "WillysNewMansion-SpecialEdition.txt"], str(_txt_urls))
_dl_urls = [d["url"] for d in _detail.get("downloads", [])]
check("zxdb media routing: downloads on zxinfo.dk/media",
      _dl_urls and all(u.startswith("https://zxinfo.dk/media/") for u in _dl_urls),
      str(_dl_urls))

check("zxdb mirror map: primary -> mirror (/zxdb)",
      _zxdb_media_mirror_url("https://zxinfo.dk/media/zxdb/sinclair/entries/1/a.txt")
      == "https://spectrumcomputing.co.uk/zxdb/sinclair/entries/1/a.txt")
check("zxdb mirror map: mirror -> primary (/pub)",
      _zxdb_media_mirror_url("https://spectrumcomputing.co.uk/pub/sinclair/g/a.scr")
      == "https://zxinfo.dk/media/pub/sinclair/g/a.scr")
check("zxdb mirror map: /zxscreens is single-host",
      _zxdb_media_mirror_url("https://zxinfo.dk/media/zxscreens/1/a.png") is None)
check("zxdb mirror map: unrelated host untouched",
      _zxdb_media_mirror_url("https://zxart.ee/files/pub/x.tap") is None)
check("zxdb mirror map: empty/None safe",
      _zxdb_media_mirror_url("") is None and _zxdb_media_mirror_url(None) is None)

# Fetch fallback: a permanent 404 on the primary host must transparently
# retry the same asset on the mirror (once — no ping-pong). No network:
# urllib.request.urlopen is monkeypatched.
import io
import urllib.error
import urllib.request

_urlopen_calls = []
_real_urlopen = urllib.request.urlopen

def _fake_urlopen(req, timeout=None):
    _urlopen_calls.append(req.full_url)
    if req.full_url.startswith("https://zxinfo.dk/media/"):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None, io.BytesIO(b""))
    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"mirror-bytes"
    return _Resp()

urllib.request.urlopen = _fake_urlopen
try:
    _data = _http_fetch_bytes_with_retry(
        "https://zxinfo.dk/media/pub/sinclair/games-info/w/W.txt", _retries=1)
    check("zxdb fetch fallback: 404 on primary served from mirror",
          _data == b"mirror-bytes", repr(_data))
    check("zxdb fetch fallback: exactly one attempt per host",
          _urlopen_calls == [
              "https://zxinfo.dk/media/pub/sinclair/games-info/w/W.txt",
              "https://spectrumcomputing.co.uk/pub/sinclair/games-info/w/W.txt",
          ], str(_urlopen_calls))
    _urlopen_calls.clear()
    try:
        _http_fetch_bytes_with_retry("https://zxinfo.dk/media/zxscreens/1/a.png",
                                     _retries=1)
        _raised = None
    except urllib.error.HTTPError as exc:
        _raised = exc
    check("zxdb fetch fallback: single-host asset fails without mirror attempt",
          _raised is not None and _raised.code == 404
          and _urlopen_calls == ["https://zxinfo.dk/media/zxscreens/1/a.png"],
          f"raised={_raised} calls={_urlopen_calls}")
finally:
    urllib.request.urlopen = _real_urlopen

# ---- zxArt ------------------------------------------------------------------
check("zxart website url: direct url from _source",
      zxart_entry_website_url({"_source": {"url": " https://zxart.ee/eng/x/ "}})
      == "https://zxart.ee/eng/x/")
check("zxart website url: non-dict entry", zxart_entry_website_url("nope") == "")

filtered = _filter_download_urls([
    {"url": "https://zxart.ee/file/id:12345/"},     # browse URL — dropped
    {"url": "https://zxart.ee/files/game.tap"},     # real file — kept
    {"url": ""},                                    # empty — dropped
])
check("download url filter", [d["url"] for d in filtered] == ["https://zxart.ee/files/game.tap"],
      str(filtered))

ascii_url = "https://zxart.ee/files/plain.tap"
check("zxart_safe_url: ascii unchanged", zxart_safe_url(ascii_url) == ascii_url)
enc = zxart_safe_url("https://zxart.ee/files/привет.tap")
try:
    enc.encode("ascii")
    ascii_ok = True
except UnicodeEncodeError:
    ascii_ok = False
check("zxart_safe_url: cyrillic percent-encoded", ascii_ok and "%" in enc, enc)

prods = {
    "totalAmount": "7",
    "responseData": {"zxProd": [{
        "id": 5, "title": "Prod", "year": 1999, "groupsIds": [1, 2],
        "hardwareRequired": ["ZX Spectrum 128"], "compo": "demo", "partyPlace": 3,
    }]},
}
entries, total = zxart_parse_prod_list(prods)
check("zxart prods: entry + total", len(entries) == 1 and total == 7,
      f"{entries} total={total}")
e = entries[0]
check("zxart prods: fields",
      e["id"] == "5" and e["author"] == "2 group(s)" and e["genre"] == "demo (#3)"
      and e["machine"] == "ZX Spectrum 128" and e["_kind"] == "zxart_prod", str(e))
check("zxart prods: non-dict safe", zxart_parse_prod_list(None) == ([], 0))

pics = {
    "totalAmount": 3,
    "responseData": {"zxPicture": [{
        "id": 11, "title": "Pic", "year": 2001, "rating": "4.5",
        "tags": ["pixel", "border", "third", "fourth"], "type": "standard",
    }]},
}
entries, total = zxart_parse_picture_list(pics)
check("zxart pictures: entry + total", len(entries) == 1 and total == 3,
      f"{entries} total={total}")
e = entries[0]
check("zxart pictures: fields",
      e["id"] == "11" and e["machine"] == "standard"
      and e["genre"] == "pixel, border, third" and e["_kind"] == "zxart_picture", str(e))

# ---- download-safety helpers (zxnu_config) ---------------------------------
import hashlib
import tempfile

from zxnu_config import (  # noqa: E402
    HDF_MONKEY_JJJS_SHA256,
    select_mame_release_asset,
    select_mame_release_assets,
    sha256_of_file,
)

with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as _tf:
    _tf.write(b"zx-next-unite hash check\x00\xff" * 1000)
    _tmp_hash_file = _tf.name
_expected = hashlib.sha256(open(_tmp_hash_file, "rb").read()).hexdigest()
check("sha256_of_file matches hashlib", sha256_of_file(_tmp_hash_file) == _expected)
os.unlink(_tmp_hash_file)
check("jjjs pin is a well-formed sha256 hex",
      len(HDF_MONKEY_JJJS_SHA256) == 64
      and all(c in "0123456789abcdef" for c in HDF_MONKEY_JJJS_SHA256),
      HDF_MONKEY_JJJS_SHA256)

_release = {"tag_name": "mame0278", "assets": [
    {"name": "mame0278b_x64.exe", "browser_download_url": "https://x/dl",
     "size": "123", "digest": "sha256:ABCDEF0123"},
]}
picked = select_mame_release_asset(_release, "x64")
check("mame asset picker returns the digest",
      picked == ("mame0278", "mame0278b_x64.exe", "https://x/dl", 123, "abcdef0123"),
      str(picked))
_release["assets"][0].pop("digest")
picked = select_mame_release_asset(_release, "x64")
check("mame asset picker: missing digest -> None sha",
      picked[4] is None, str(picked))
check("mame asset picker: no matching arch -> None",
      select_mame_release_asset(_release, "arm64") is None)

# Up to 0.280 the sole 64-bit Windows build was mame<ver>b_64bit.exe; the
# x64/arm64 split (and arm64 builds at all) only arrived with 0.281.
_legacy = {"tag_name": "mame0280", "assets": [
    {"name": "mame0280b_64bit.exe", "browser_download_url": "https://x/old",
     "size": 99},
]}
check("mame asset picker: pre-0.281 b_64bit.exe counts as the x64 build",
      select_mame_release_asset(_legacy, "x64")
      == ("mame0280", "mame0280b_64bit.exe", "https://x/old", 99, None),
      str(select_mame_release_asset(_legacy, "x64")))
check("mame asset picker: b_64bit.exe is never offered as arm64",
      select_mame_release_asset(_legacy, "arm64") is None)

def _rel(tag, arch_name=None, **extra):
    body = {"tag_name": tag, "body": f"notes for {tag}"}
    body.update(extra)
    body["assets"] = ([] if arch_name is None else
                      [{"name": arch_name, "browser_download_url": f"https://x/{tag}",
                        "size": 1048576}])
    return body

# Deliberately out of order, with entries the picker must drop.
_releases = [
    _rel("mame0284", "mame0284b_x64.exe"),
    _rel("mame0287", "mame0287b_x64.exe"),
    _rel("mame0286", "mame0286b_arm64.exe"),          # no x64 build
    _rel("mame0285", "mame0285b_x64.exe", draft=True),
    _rel("mame0288", "mame0288b_x64.exe", prerelease=True),
    _rel("mame0283", None),                            # source-only release
    _rel("mame0282", "mame0282b_64bit.exe"),           # legacy spelling
    "not a release dict",
]
_choices = select_mame_release_assets(_releases, "x64")
check("mame release list: newest first, drafts/prereleases/non-x64 dropped",
      [c["tag"] for c in _choices] == ["mame0287", "mame0284", "mame0282"],
      str([c["tag"] for c in _choices]))
check("mame release list: entries carry what the installer needs",
      _choices[0]["asset_name"] == "mame0287b_x64.exe"
      and _choices[0]["url"] == "https://x/mame0287"
      and _choices[0]["version"] == 287
      and _choices[0]["notes"] == "notes for mame0287",
      str(_choices[0]))
check("mame release list: limit caps the choices",
      [c["tag"] for c in select_mame_release_assets(_releases, "x64", limit=2)]
      == ["mame0287", "mame0284"])
check("mame release list: limit=0 means every match",
      len(select_mame_release_assets(_releases, "x64", limit=0)) == 3)
check("mame release list: an unparseable tag sorts last instead of raising",
      [c["tag"] for c in select_mame_release_assets(
          [_rel("nightly", "namelessb_x64.exe"), _rel("mame0281", "mame0281b_x64.exe")],
          "x64")] == ["mame0281", "nightly"])
check("mame release list: nothing for this arch -> empty list",
      select_mame_release_assets(_releases, "riscv") == [])

# ---- app self-update asset picker + archive unpacker (zxnu_config) ---------
import shutil
import tarfile
import zipfile

from zxnu_config import (  # noqa: E402
    extract_zxnu_update_archive,
    select_zxnu_release_asset,
)

_zxnu_release = {"tag_name": "v9.2.0", "assets": [
    {"name": "sync5", "browser_download_url": "https://x/sync5", "size": 24576},
    {"name": "zx-next-unite-v9.2.0.exe", "browser_download_url": "https://x/exe",
     "size": "1000", "digest": "sha256:AA11"},
    {"name": "zx-next-unite-v9.2.0-linux-x86_64.tar.gz",
     "browser_download_url": "https://x/lin", "size": 2000},
    {"name": "zx-next-unite-v9.2.0-macos-x86_64.zip",
     "browser_download_url": "https://x/mac-intel", "size": 3000},
    {"name": "zx-next-unite-v9.2.0-macos-arm64.zip",
     "browser_download_url": "https://x/mac-arm", "size": 3001},
]}
picked = select_zxnu_release_asset(_zxnu_release, "win32", "AMD64")
check("zxnu picker: win32 -> the exe (not sync5), digest parsed",
      picked == ("zx-next-unite-v9.2.0.exe", "https://x/exe", 1000, "aa11"),
      str(picked))
picked = select_zxnu_release_asset(_zxnu_release, "linux", "x86_64")
check("zxnu picker: linux -> the tar.gz",
      picked[0] == "zx-next-unite-v9.2.0-linux-x86_64.tar.gz", str(picked))
picked = select_zxnu_release_asset(_zxnu_release, "darwin", "arm64")
check("zxnu picker: darwin prefers the machine arch",
      picked[0] == "zx-next-unite-v9.2.0-macos-arm64.zip", str(picked))
picked = select_zxnu_release_asset(_zxnu_release, "darwin", "weird-arch")
check("zxnu picker: unknown arch falls back to the first match",
      picked[0] == "zx-next-unite-v9.2.0-macos-x86_64.zip", str(picked))
check("zxnu picker: exe-only release has no linux package",
      select_zxnu_release_asset(
          {"assets": [{"name": "zx-next-unite-v9.2.0.exe",
                       "browser_download_url": "https://x/exe"}]},
          "linux", "x86_64") is None)
check("zxnu picker: junk input -> None",
      select_zxnu_release_asset(None, "win32", "AMD64") is None
      and select_zxnu_release_asset({}, "win32", "AMD64") is None)

# tar.gz package: one version-stamped binary inside, exec bit restored.
_pkg_dir = tempfile.mkdtemp(prefix="zxnu-pkg-")
_bin_src = os.path.join(_pkg_dir, "zx-next-unite-v9.2.0")
with open(_bin_src, "wb") as _f:
    _f.write(b"\x7fELF fake binary")
_tar_path = os.path.join(_pkg_dir, "zx-next-unite-v9.2.0-linux-x86_64.tar.gz")
with tarfile.open(_tar_path, "w:gz") as _tf:
    _tf.add(_bin_src, arcname="zx-next-unite-v9.2.0")
_out_dir = os.path.join(_pkg_dir, "out")
_runnable = extract_zxnu_update_archive(_tar_path, _out_dir)
check("zxnu unpack: tar.gz -> the binary inside",
      os.path.basename(_runnable) == "zx-next-unite-v9.2.0"
      and os.path.isfile(_runnable), _runnable)
if os.name == "posix":
    check("zxnu unpack: tar.gz binary is executable",
          os.access(_runnable, os.X_OK), oct(os.stat(_runnable).st_mode))

# zip package: a .app bundle inside (zipfile fallback path everywhere but
# macOS, where ditto is used instead).
_zip_path = os.path.join(_pkg_dir, "zx-next-unite-v9.2.0-macos-arm64.zip")
with zipfile.ZipFile(_zip_path, "w") as _zf:
    _info = zipfile.ZipInfo("zx-next-unite-v9.2.0.app/Contents/MacOS/zx-next-unite")
    _info.external_attr = 0o100755 << 16
    _zf.writestr(_info, b"\xcf\xfa\xed\xfe fake mach-o")
    _zf.writestr("zx-next-unite-v9.2.0.app/Contents/Info.plist", b"<plist/>")
_out_dir2 = os.path.join(_pkg_dir, "out2")
_runnable2 = extract_zxnu_update_archive(_zip_path, _out_dir2)
check("zxnu unpack: zip -> the .app bundle inside",
      os.path.basename(_runnable2) == "zx-next-unite-v9.2.0.app"
      and os.path.isdir(_runnable2)
      and os.path.isfile(os.path.join(
          _runnable2, "Contents", "MacOS", "zx-next-unite")), _runnable2)

_bad = os.path.join(_pkg_dir, "nothing-useful.zip")
with zipfile.ZipFile(_bad, "w") as _zf:
    _zf.writestr("a.txt", b"x")
    _zf.writestr("b.txt", b"y")
try:
    extract_zxnu_update_archive(_bad, os.path.join(_pkg_dir, "out3"))
    check("zxnu unpack: zip without .app raises ValueError", False)
except ValueError:
    check("zxnu unpack: zip without .app raises ValueError", True)
try:
    extract_zxnu_update_archive(os.path.join(_pkg_dir, "u.rar"), _pkg_dir)
    check("zxnu unpack: unknown package type raises ValueError", False)
except ValueError:
    check("zxnu unpack: unknown package type raises ValueError", True)
shutil.rmtree(_pkg_dir, ignore_errors=True)

# ---- star-import tripwire ---------------------------------------------------
# `from zxnu_api import *` skips underscore-prefixed names, so every private
# zxnu_api helper the monolith still references must appear in its EXPLICIT
# import list. Missing one breaks features silently at runtime (this exact
# gap once killed every gallery's image loading — the shared fetcher calls
# _http_fetch_bytes_with_retry from a worker whose errors are swallowed).
import ast
import re

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_api_tree = ast.parse(open(os.path.join(REPO, "zxnu_api.py"), encoding="utf-8").read())
_api_private = set()
for _n in _api_tree.body:
    if isinstance(_n, (ast.FunctionDef, ast.ClassDef)) and _n.name.startswith("_"):
        _api_private.add(_n.name)
    elif isinstance(_n, ast.Assign):
        for _t in _n.targets:
            if isinstance(_t, ast.Name) and _t.id.startswith("_"):
                _api_private.add(_t.id)

_mono_src = open(os.path.join(REPO, "zx-next-unite.py"), encoding="utf-8").read()
_mono_tree = ast.parse(_mono_src)
_explicit = set()
for _n in _mono_tree.body:
    if isinstance(_n, ast.ImportFrom) and _n.module == "zxnu_api":
        for _a in _n.names:
            _explicit.add(_a.name)

# A private name is "used" when it appears outside the import statement and
# is not re-defined by the monolith itself.
_mono_defs = {n.name for n in ast.walk(_mono_tree)
              if isinstance(n, ast.FunctionDef)}
_missing = []
for _name in sorted(_api_private - _explicit - _mono_defs):
    _uses = len(re.findall(rf"(?<![\w.]){re.escape(_name)}\b", _mono_src))
    if _uses > 0:
        _missing.append(f"{_name} (x{_uses})")
check("all private zxnu_api names used by the monolith are explicitly imported",
      not _missing, "; ".join(_missing))

# Structural guard for the same trap: every module the monolith star-imports
# must carry the house catch-all __all__ (which exports underscore names too).
_CATCH_ALL = "__all__ = [_n for _n in dir() if not _n.startswith('__')]"
for _mod in ("zxnu_config.py", "zxnu_workers.py", "zxnu_api.py"):
    _src = open(os.path.join(REPO, _mod), encoding="utf-8").read()
    check(f"{_mod} carries the catch-all __all__", _CATCH_ALL in _src)

print()
if FAIL:
    print(f"RESULT: {len(FAIL)} FAILURE(S)")
    sys.exit(1)
print("RESULT: ALL API PARSER CHECKS PASSED")
