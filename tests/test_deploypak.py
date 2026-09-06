"""deploypak.txt — the extras manifest an itch.io package ships next to its
build (zxnu_config.read_deploypak) and its hand-kept twin in the standalone
console server (nextsync5._read_deploypak).

The parser turns the manifest into the ordered mkdir/put plan the remote
self-update macro runs before swapping the .nex in. Covered here: files and
folders (recursive, top-down, names sorted), comment/blank/BOM/whitespace
tolerance, both slash styles, the top-level skip of the file being swapped
and of the manifest itself, duplicate collapsing, every refused shape
(absolute, drive-anchored, UNC, '..', NUL, missing, non-UTF-8, a symlink
escape where the platform lets a test plant one), the file-count bound, the
"no manifest" answer, deploypak_counts — and the tripwire that the two
parsers agree on every one of those trees.

Run with: python tests/test_deploypak.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

import zxnu_config                                   # noqa: E402
import nextsync5                                     # noqa: E402
from zxnu_config import read_deploypak, deploypak_counts, DEPLOYPAK_FILENAME  # noqa: E402

FAIL = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


def _write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def _manifest(pkg, text, encoding="utf-8"):
    with open(os.path.join(pkg, DEPLOYPAK_FILENAME), "wb") as fh:
        fh.write(text.encode(encoding) if isinstance(text, str) else text)


def _rel(plan):
    """The plan with local paths dropped: ('mkdir', rel) / ('put', rel)."""
    return [(s[0], s[-1]) for s in plan]


def _twin_agrees(label, pkg, skip):
    """The console server's parser must answer exactly like the app's."""
    a = read_deploypak(pkg, skip)
    b = nextsync5._read_deploypak(pkg, skip)
    check(f"twin: nextsync5._read_deploypak agrees ({label})", a == b,
          f"{a} != {b}")


def make_package(tmp):
    pkg = os.path.join(tmp, "zxnextremote-9.9.9")
    _write(os.path.join(pkg, "zxnextremote-n2n.nex"), b"NEX-N2N")
    _write(os.path.join(pkg, "zxnextremote-httpbridge.nex"), b"NEX-HB")
    _write(os.path.join(pkg, "zxnrmenu0b.nxi"), b"MENU")
    _write(os.path.join(pkg, "zxnrmonkeys0.spr"), b"SPR")
    _write(os.path.join(pkg, "Manual.pdf"), b"PDF")
    _write(os.path.join(pkg, "data", "b.bin"), b"B")
    _write(os.path.join(pkg, "data", "a.bin"), b"A")
    _write(os.path.join(pkg, "data", "sub", "deep.bin"), b"D")
    os.makedirs(os.path.join(pkg, "data", "empty"), exist_ok=True)
    _write(os.path.join(tmp, "outside.txt"), b"OUT")
    return pkg


def main():
    tmp = tempfile.mkdtemp(prefix="deploypak_")
    try:
        pkg = make_package(tmp)
        skip = ("zxnextremote-n2n.nex",)

        # No manifest at all: nothing to send, nothing wrong.
        check("no manifest -> ([], [])", read_deploypak(pkg, skip) == ([], []))
        _twin_agrees("no manifest", pkg, skip)

        # Files and a folder, recursive: mkdir of the folder first, then its
        # entries top-down with names sorted (a.bin before b.bin, the empty
        # sub-folder still gets its mkdir, sub/ after the files beside it).
        _manifest(pkg, "zxnrmenu0b.nxi\nzxnrmonkeys0.spr\ndata\n")
        plan, problems = read_deploypak(pkg, skip)
        check("files + folder -> ordered mkdir/put plan, no problems",
              problems == [] and _rel(plan) == [
                  ("put", "zxnrmenu0b.nxi"), ("put", "zxnrmonkeys0.spr"),
                  ("mkdir", "data"), ("put", "data/a.bin"), ("put", "data/b.bin"),
                  ("mkdir", "data/empty"), ("mkdir", "data/sub"),
                  ("put", "data/sub/deep.bin")],
              f"{_rel(plan)} / {problems}")
        check("...put steps carry absolute local paths that exist",
              all(os.path.isfile(s[1]) and os.path.isabs(s[1])
                  for s in plan if s[0] == "put"))
        check("deploypak_counts -> (files, folders)",
              deploypak_counts(plan) == (5, 3), str(deploypak_counts(plan)))
        _twin_agrees("files + folder", pkg, skip)

        # Comments, blanks, a BOM, whitespace, backslashes, trailing slashes,
        # './' prefixes and a Windows-style nested path all read the same.
        _manifest(pkg, "﻿# the screens\n\n  zxnrmenu0b.nxi  \n"
                       ".\\zxnrmonkeys0.spr\ndata/sub/\n./data\\empty\\\n")
        plan, problems = read_deploypak(pkg, skip)
        check("comments/blanks/BOM/whitespace/backslashes/trailing slash tolerated",
              problems == [] and _rel(plan) == [
                  ("put", "zxnrmenu0b.nxi"), ("put", "zxnrmonkeys0.spr"),
                  ("mkdir", "data/sub"), ("put", "data/sub/deep.bin"),
                  ("mkdir", "data/empty")],
              f"{_rel(plan)} / {problems}")
        _twin_agrees("tolerance", pkg, skip)

        # The file the macro swaps itself and the manifest are left out when
        # listed (case-insensitively) - the OTHER flavor's .nex is just a
        # file the author wants sent. Duplicates collapse to one step.
        _manifest(pkg, "ZXNEXTREMOTE-N2N.NEX\nDeployPak.txt\n"
                       "zxnextremote-httpbridge.nex\nzxnrmenu0b.nxi\n"
                       "ZXNRMENU0B.NXI\ndata\ndata/a.bin\n")
        plan, problems = read_deploypak(pkg, skip)
        check("swapped .nex + manifest skipped, other .nex sent, duplicates collapse",
              problems == [] and _rel(plan) == [
                  ("put", "zxnextremote-httpbridge.nex"),
                  ("put", "zxnrmenu0b.nxi"),
                  ("mkdir", "data"), ("put", "data/a.bin"), ("put", "data/b.bin"),
                  ("mkdir", "data/empty"), ("mkdir", "data/sub"),
                  ("put", "data/sub/deep.bin")],
              f"{_rel(plan)} / {problems}")
        _twin_agrees("skips + duplicates", pkg, skip)

        # Letter case: an entry is matched to the package case-blind (the
        # Next's FAT is; a Linux checkout is not) and sent under its
        # ON-DISK spelling - the same plan on every PC. Two package names
        # differing only by case (possible on a case-sensitive disk) make
        # such an entry ambiguous: a problem.
        _manifest(pkg, "ZXNRMONKEYS0.SPR\nDATA/Sub/DEEP.bin\n")
        plan, problems = read_deploypak(pkg, skip)
        check("case-blind match, sent under the on-disk spelling",
              problems == [] and _rel(plan) == [
                  ("put", "zxnrmonkeys0.spr"), ("put", "data/sub/deep.bin")],
              f"{_rel(plan)} / {problems}")
        _twin_agrees("case-blind", pkg, skip)
        _write(os.path.join(pkg, "dup.bin"), b"1")
        case_sensitive = not os.path.exists(os.path.join(pkg, "DUP.BIN"))
        if case_sensitive:
            _write(os.path.join(pkg, "Dup.bin"), b"2")
            _manifest(pkg, "DUP.BIN\ndup.bin\n")
            plan, problems = read_deploypak(pkg, skip)
            check("two names differing only by case: the blind entry is a problem, "
                  "the exact one resolves",
                  len(problems) == 1 and "more than one name" in problems[0]
                  and _rel(plan) == [("put", "dup.bin")],
                  f"{_rel(plan)} / {problems}")
            _twin_agrees("case ambiguity", pkg, skip)
            os.remove(os.path.join(pkg, "Dup.bin"))
        else:
            print("SKIP  case-ambiguity case (case-insensitive disk)")
        os.remove(os.path.join(pkg, "dup.bin"))

        # A '.' line means the whole package - minus the skips.
        _manifest(pkg, ".\n")
        plan, problems = read_deploypak(pkg, skip)
        names = [s[-1] for s in plan]
        check("'.' sends the whole package minus the swapped .nex and the manifest",
              problems == [] and "zxnextremote-n2n.nex" not in names
              and DEPLOYPAK_FILENAME not in names
              and "zxnextremote-httpbridge.nex" in names and "Manual.pdf" in names
              and "data/sub/deep.bin" in names and ("mkdir", "data") in _rel(plan),
              f"{_rel(plan)} / {problems}")
        _twin_agrees("dot", pkg, skip)

        # Every refused shape is a PROBLEM naming its line - and a problem
        # never silently drops the line while the rest goes through: the
        # caller refuses the whole update.
        _manifest(pkg, "../outside.txt\n/etc/passwd\nC:\\Windows\\win.ini\n"
                       "c:foo\n\\\\srv\\share\\x\ndata/../../outside.txt\n"
                       "missing.bin\ndata/nope/\nzxnrmenu0b.nxi\n")
        plan, problems = read_deploypak(pkg, skip)
        check("'..', absolute, drive, UNC, missing -> one problem each, naming the line",
              len(problems) == 8
              and all(f"line {n} " in problems[i]
                      for i, n in enumerate((1, 2, 3, 4, 5, 6, 7, 8)))
              and "'..'" in problems[0] and "absolute" in problems[1]
              and "absolute" in problems[2] and "absolute" in problems[3]
              and "absolute" in problems[4] and "'..'" in problems[5]
              and "not found" in problems[6] and "not found" in problems[7],
              str(problems))
        check("...while the good line still parses (the caller decides to refuse)",
              _rel(plan) == [("put", "zxnrmenu0b.nxi")], str(_rel(plan)))
        _twin_agrees("refusals", pkg, skip)

        # A NUL byte in a line is a problem, not an OSError out of os.stat.
        _manifest(pkg, "zxnr\x00menu0b.nxi\n")
        plan, problems = read_deploypak(pkg, skip)
        check("NUL byte -> problem", plan == [] and len(problems) == 1
              and "NUL" in problems[0], str(problems))
        _twin_agrees("NUL", pkg, skip)

        # A name ending in a dot or a space: illegal on FAT, and on Windows
        # "build.nex." is an ALIAS of build.nex that would slip the swapped
        # .nex past the skip - a problem, in both parsers.
        _manifest(pkg, "zxnextremote-n2n.nex.\ndata. /a.bin\n")
        plan, problems = read_deploypak(pkg, skip)
        check("a name ending in a dot or a space -> problem",
              plan == [] and len(problems) == 2
              and all("dot or a space" in p for p in problems), str(problems))
        _twin_agrees("trailing dot", pkg, skip)

        # Not UTF-8: refused outright (a manifest is text or it is nothing).
        _manifest(pkg, b"\xff\xfe\x00bad\n")
        plan, problems = read_deploypak(pkg, skip)
        check("non-UTF-8 manifest -> problem, empty plan",
              plan == [] and problems == [f"{DEPLOYPAK_FILENAME} is not UTF-8 text"],
              str(problems))
        _twin_agrees("non-UTF-8", pkg, skip)

        # An empty / comment-only manifest sends nothing and is not an error.
        _manifest(pkg, "# nothing here\n\n")
        check("comment-only manifest -> ([], [])", read_deploypak(pkg, skip) == ([], []))
        _twin_agrees("comment-only", pkg, skip)

        # A symlink planted in the package that points OUTSIDE it: the entry
        # resolves elsewhere -> problem; a symlinked FOLDER inside a listed
        # folder is not followed. Only where the platform lets a test plant
        # one (Windows needs a privilege for symlinks - skip cleanly).
        link = os.path.join(pkg, "escape.txt")
        dlink = os.path.join(pkg, "data", "elsewhere")
        try:
            os.symlink(os.path.join(tmp, "outside.txt"), link)
            os.symlink(tmp, dlink, target_is_directory=True)
            can_link = True
        except (OSError, NotImplementedError, AttributeError):
            can_link = False
        if can_link:
            _manifest(pkg, "escape.txt\ndata\n")
            plan, problems = read_deploypak(pkg, skip)
            check("symlink out of the package -> problem; symlinked folder not followed",
                  len(problems) == 1 and "outside" in problems[0]
                  and all(not s[-1].startswith("data/elsewhere") for s in plan)
                  and ("put", "data/sub/deep.bin") in _rel(plan),
                  f"{_rel(plan)} / {problems}")
            _twin_agrees("symlinks", pkg, skip)
            for p in (link, dlink):
                try:
                    os.unlink(p)
                except OSError:
                    try:
                        os.rmdir(p)
                    except OSError:
                        pass
        else:
            print("SKIP  symlink escape (this platform/user cannot create symlinks)")

        # A directory JUNCTION planted inside a listed folder (Windows; no
        # privilege needed - what os.path.islink cannot see): the walk must
        # not follow it out of the package, and naming it directly is a
        # problem. Skipped where mklink is unavailable.
        jn = os.path.join(pkg, "data", "jn")
        made = False
        if os.name == "nt":
            try:
                made = subprocess.run(["cmd", "/c", "mklink", "/J", jn, tmp],
                                      capture_output=True, timeout=20).returncode == 0
            except (OSError, subprocess.SubprocessError):
                made = False
        if made:
            _manifest(pkg, "data\n")
            plan, problems = read_deploypak(pkg, skip)
            check("a junction inside a listed folder is not followed out of the package",
                  problems == [] and not any("jn" in s[-1] for s in plan)
                  and ("put", "data/sub/deep.bin") in _rel(plan),
                  f"{_rel(plan)} / {problems}")
            _twin_agrees("junction walk", pkg, skip)
            _manifest(pkg, "data/jn\n")
            plan, problems = read_deploypak(pkg, skip)
            check("a listed junction that resolves outside the package -> problem",
                  plan == [] and len(problems) == 1 and "outside" in problems[0],
                  f"{_rel(plan)} / {problems}")
            _twin_agrees("junction entry", pkg, skip)
            try:
                os.rmdir(jn)
            except OSError:
                pass
        elif os.name == "nt":
            print("SKIP  junction cases (mklink /J unavailable)")

        # A folder the walk cannot list is a problem, never a silently empty
        # one (POSIX, non-root: chmod 000 refuses the listing).
        if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() != 0:
            locked = os.path.join(pkg, "data", "locked")
            os.makedirs(locked, exist_ok=True)
            _write(os.path.join(locked, "x.bin"), b"X")
            os.chmod(locked, 0)
            try:
                _manifest(pkg, "data\n")
                plan, problems = read_deploypak(pkg, skip)
                check("an unlistable folder inside a listed one -> problem",
                      len(problems) == 1 and "could not be listed" in problems[0],
                      f"{_rel(plan)} / {problems}")
                _twin_agrees("unlistable", pkg, skip)
            finally:
                os.chmod(locked, 0o700)
                shutil.rmtree(locked, ignore_errors=True)

        # The file-count bound: a manifest naming more files than an update
        # sends is a problem (the constant is read at call time).
        _manifest(pkg, "data\n")
        saved = zxnu_config.DEPLOYPAK_MAX_FILES, nextsync5.DEPLOYPAK_MAX_FILES
        zxnu_config.DEPLOYPAK_MAX_FILES = nextsync5.DEPLOYPAK_MAX_FILES = 2
        try:
            plan, problems = read_deploypak(pkg, skip)
            check("more files than DEPLOYPAK_MAX_FILES -> problem",
                  len(problems) == 1 and "more than the 2" in problems[0],
                  str(problems))
            _twin_agrees("bound", pkg, skip)
        finally:
            zxnu_config.DEPLOYPAK_MAX_FILES, nextsync5.DEPLOYPAK_MAX_FILES = saved

        # Skip names compare case-insensitively (the Next's FAT does).
        _manifest(pkg, "zxnrmenu0b.nxi\n")
        plan, problems = read_deploypak(pkg, ("ZXNRMENU0B.NXI",))
        check("skip names compare case-insensitively",
              plan == [] and problems == [], f"{plan} / {problems}")
        _twin_agrees("skip case", pkg, ("ZXNRMENU0B.NXI",))

        # The two constants are hand-kept twins too.
        check("DEPLOYPAK_FILENAME / DEPLOYPAK_MAX_FILES twins agree",
              nextsync5.DEPLOYPAK_FILENAME == zxnu_config.DEPLOYPAK_FILENAME
              and nextsync5.DEPLOYPAK_MAX_FILES == zxnu_config.DEPLOYPAK_MAX_FILES)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nRESULT:", "ALL PASS" if not FAIL else f"FAILURES: {FAIL}")
    sys.exit(0 if not FAIL else 1)


if __name__ == "__main__":
    main()
