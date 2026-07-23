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
    getit_parse_detail,
    getit_parse_file_list,
    zxart_entry_website_url,
    zxart_parse_picture_list,
    zxart_parse_prod_list,
    zxart_safe_url,
    zxdb_entry_website_url,
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
