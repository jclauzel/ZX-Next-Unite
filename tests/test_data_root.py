"""zxnu_config data-root resolution + legacy-state migration tests.

ZXNU_DATA_ROOT is resolved ONCE at zxnu_config import (from sys.argv[0] and
the ZX_NEXT_UNITE_HOME / ZX_NEXT_UNITE_MODE / APPDATA / XDG_DATA_HOME env
vars), so every scenario runs in a SUBPROCESS launching a tiny script from a
scratch dir with a controlled environment.

Run with: python tests/test_data_root.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

FAIL = []
def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)

LAUNCHER = (
    "import os, sys\n"
    f"sys.path.insert(0, {REPO!r})\n"
    "import zxnu_config\n"
    "print(zxnu_config.ZXNU_DATA_ROOT)\n"
    "print(zxnu_config.ZX_NEXT_UNITE_CONFIG_FILE_NAME)\n"
)


def run_case(app_dir, env_extra):
    """Run the launcher script from *app_dir* with *env_extra* applied on top
    of a cleaned environment; return (data_root, cfg_path, CompletedProcess)."""
    script = os.path.join(app_dir, "launch.py")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(LAUNCHER)
    env = os.environ.copy()
    for k in ("ZX_NEXT_UNITE_HOME", "ZX_NEXT_UNITE_MODE",
              "APPDATA", "XDG_DATA_HOME"):
        env.pop(k, None)
    if sys.platform == "win32" and "APPDATA" not in env_extra:
        env["APPDATA"] = os.environ.get("APPDATA", "")
    env.update(env_extra)
    proc = subprocess.run([sys.executable, script], capture_output=True,
                          text=True, env=env, timeout=180)
    lines = [ln.strip() for ln in proc.stdout.strip().splitlines()]
    if len(lines) < 2:
        return None, None, proc
    return lines[-2], lines[-1], proc


BASE = tempfile.mkdtemp(prefix="zxnu-root-test-")

# 1. Portable default: root is the launched script's directory.
app1 = os.path.join(BASE, "app1"); os.makedirs(app1)
root, cfg, proc = run_case(app1, {})
check("portable default: root is the script dir",
      root is not None and os.path.normcase(root) == os.path.normcase(app1),
      f"root={root!r} stderr={proc.stderr[-300:]!r}")
check("portable default: cfg lives in the root",
      cfg is not None and os.path.normcase(os.path.dirname(cfg)) == os.path.normcase(app1), str(cfg))

# 2. ZX_NEXT_UNITE_HOME override wins and is created.
app2 = os.path.join(BASE, "app2"); os.makedirs(app2)
home2 = os.path.join(BASE, "custom-home")
root, cfg, proc = run_case(app2, {"ZX_NEXT_UNITE_HOME": home2})
check("ZX_NEXT_UNITE_HOME override wins",
      root is not None and os.path.normcase(root) == os.path.normcase(os.path.abspath(home2)),
      f"root={root!r}")
check("ZX_NEXT_UNITE_HOME dir is created", os.path.isdir(home2))

# 3. Installed mode goes to the platform app-data dir (redirected into the
#    scratch via APPDATA / XDG_DATA_HOME; macOS hardcodes ~/Library, so the
#    redirect assertion is skipped there).
if sys.platform != "darwin":
    app3 = os.path.join(BASE, "app3"); os.makedirs(app3)
    plat3 = os.path.join(BASE, "plat3"); os.makedirs(plat3)
    env3 = {"ZX_NEXT_UNITE_MODE": "installed",
            ("APPDATA" if sys.platform == "win32" else "XDG_DATA_HOME"): plat3}
    root, cfg, proc = run_case(app3, env3)
    expected3 = os.path.join(plat3, "zx-next-unite")
    check("installed mode: platform app-data dir",
          root is not None and os.path.normcase(root) == os.path.normcase(expected3),
          f"root={root!r} stderr={proc.stderr[-300:]!r}")
    check("installed mode: root dir created", os.path.isdir(expected3))

    # 4. Migration: legacy cfg + downloads next to the script move into the
    #    (fresh) installed root; logs are left behind.
    app4 = os.path.join(BASE, "app4"); os.makedirs(app4)
    plat4 = os.path.join(BASE, "plat4"); os.makedirs(plat4)
    with open(os.path.join(app4, "hdfg.cfg"), "w", encoding="utf-8") as fh:
        fh.write("theme = dark\n")
    os.makedirs(os.path.join(app4, "downloads", "itchio"))
    with open(os.path.join(app4, "downloads", "itchio", "marker.txt"), "w") as fh:
        fh.write("x")
    with open(os.path.join(app4, "zx-next-unite.log"), "w") as fh:
        fh.write("old log")
    env4 = {"ZX_NEXT_UNITE_MODE": "installed",
            ("APPDATA" if sys.platform == "win32" else "XDG_DATA_HOME"): plat4}
    root, cfg, proc = run_case(app4, env4)
    new_root = os.path.join(plat4, "zx-next-unite")
    check("migration: cfg moved into the new root",
          os.path.isfile(os.path.join(new_root, "hdfg.cfg"))
          and not os.path.exists(os.path.join(app4, "hdfg.cfg")),
          f"stderr={proc.stderr[-300:]!r}")
    check("migration: downloads tree moved intact",
          os.path.isfile(os.path.join(new_root, "downloads", "itchio", "marker.txt"))
          and not os.path.exists(os.path.join(app4, "downloads")))
    check("migration: old log deliberately left behind",
          os.path.isfile(os.path.join(app4, "zx-next-unite.log")))

    # 5. A second run must NOT clobber the migrated state with new strays.
    with open(os.path.join(app4, "hdfg.cfg"), "w", encoding="utf-8") as fh:
        fh.write("theme = SHOULD-NOT-WIN\n")
    root, cfg, proc = run_case(app4, env4)
    with open(os.path.join(new_root, "hdfg.cfg"), encoding="utf-8") as fh:
        content = fh.read()
    check("migration: existing state never overwritten",
          "dark" in content and "SHOULD-NOT-WIN" not in content, content)

# 6. ZX_NEXT_UNITE_HOME beats installed mode.
app6 = os.path.join(BASE, "app6"); os.makedirs(app6)
home6 = os.path.join(BASE, "home6")
root, cfg, proc = run_case(app6, {"ZX_NEXT_UNITE_MODE": "installed",
                                  "ZX_NEXT_UNITE_HOME": home6})
check("ZX_NEXT_UNITE_HOME beats installed mode",
      root is not None and os.path.normcase(root) == os.path.normcase(os.path.abspath(home6)),
      f"root={root!r}")

shutil.rmtree(BASE, ignore_errors=True)

print()
if FAIL:
    print(f"RESULT: {len(FAIL)} FAILURE(S)")
    sys.exit(1)
print("RESULT: ALL DATA-ROOT CHECKS PASSED")
