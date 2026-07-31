"""Run the whole ZX-Next-Unite test suite and print one summary.

Usage:  python tests/run_all.py [--coverage]

Each test file is run in its own process (they patch/monkey with Qt and
sys.meta_path, so isolation matters). Suites whose optional dependency is
missing are reported as SKIPPED rather than failed:
  - test_http_bridge.py needs Flask (the bridge itself is optional in-app)
  - test_ui_offscreen.py phases 1-3 skip internally without hdfmonkey

--coverage measures line coverage of the application modules
(zx-next-unite.py, zxnu_*.py, nextsync5.py) across every suite INCLUDING
their child processes: the offscreen UI phases run a scratch copy of
zx-next-unite.py, which tests/.coveragerc folds back onto the repo file.
Prints a per-file table (worst first) and writes an annotated-source HTML
report to tests/htmlcov/. Needs coverage.py: python -m pip install coverage

Exit code: 0 when nothing failed (skips allowed), 1 otherwise. Coverage is
reporting only — it never changes the exit code.
"""
import glob
import importlib.util
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

SUITES = [
    # (file, timeout seconds, required import or None)
    ("test_api_parsers.py",     120, None),
    ("test_data_root.py",       240, None),
    ("test_hdfmonkey_discovery.py", 120, None),
    ("test_mame_install.py",     120, None),
    ("test_startup_tab_activation.py", 120, None),
    ("test_pane_imports.py",    120, None),
    ("test_hdf_workers.py",     120, None),
    ("test_classic_sync.py",    180, None),
    ("test_listen.py",          120, None),
    ("test_remote_listen.py",   120, None),
    ("test_remote_explorer_widget.py", 180, None),
    ("test_http_bridge.py",     240, "flask"),
    ("test_retro_log_widget.py", 120, None),
    ("test_i18n.py",            120, None),
    ("test_wizard.py",          120, None),
    ("test_network.py",         120, None),
    ("test_ui_offscreen.py",    3600, None),   # runs its 10 phases itself
]

WITH_COVERAGE = "--coverage" in sys.argv[1:]
unknown = [a for a in sys.argv[1:] if a != "--coverage"]
if unknown:
    sys.exit(f"unknown argument(s): {' '.join(unknown)} — only --coverage is supported")

child_env = None
if WITH_COVERAGE:
    if importlib.util.find_spec("coverage") is None:
        sys.exit("--coverage needs coverage.py:  python -m pip install coverage")
    # Stale data files from an aborted earlier run would pollute the combine.
    # (.coverage / .coverage.* only — never the .coveragerc config next door.)
    for stale in ([os.path.join(HERE, ".coverage")]
                  + glob.glob(os.path.join(HERE, ".coverage.*"))):
        try:
            os.remove(stale)
        except OSError:
            pass
    child_env = dict(os.environ)
    child_env["COVERAGE_PROCESS_START"] = os.path.join(HERE, ".coveragerc")
    child_env["COVERAGE_FILE"] = os.path.join(HERE, ".coverage")
    # sys.monitoring measurement core: near-zero overhead on Python 3.12+.
    # coverage falls back (with a warning) where it is unsupported.
    child_env.setdefault("COVERAGE_CORE", "sysmon")
    child_env["PYTHONPATH"] = (os.path.join(HERE, "covhook") + os.pathsep
                               + child_env.get("PYTHONPATH", ""))

results = []
for name, timeout, needs in SUITES:
    print(f"\n======== {name} ========", flush=True)
    if needs is not None and importlib.util.find_spec(needs) is None:
        print(f"SKIPPED: optional dependency '{needs}' is not installed")
        results.append((name, "SKIP", 0.0))
        continue
    t0 = time.monotonic()
    try:
        rc = subprocess.call([sys.executable, os.path.join(HERE, name)],
                             timeout=timeout, env=child_env)
    except subprocess.TimeoutExpired:
        print(f"TIMED OUT after {timeout}s")
        rc = 1
    results.append((name, "PASS" if rc == 0 else "FAIL",
                    time.monotonic() - t0))

print("\n======== summary ========")
width = max(len(n) for n, _s, _t in results)
failed = False
for name, status, secs in results:
    print(f"{name.ljust(width)}  {status}  ({secs:5.1f}s)")
    failed = failed or status == "FAIL"

if WITH_COVERAGE:
    print("\n======== coverage ========", flush=True)
    cov = [sys.executable, "-m", "coverage"]
    rcfile = "--rcfile=" + os.path.join(HERE, ".coveragerc")
    # cwd=REPO: the [paths] remap in .coveragerc resolves "." to the repo root.
    if subprocess.call(cov + ["combine", rcfile], cwd=REPO, env=child_env) == 0:
        subprocess.call(cov + ["report", rcfile], cwd=REPO, env=child_env)
        subprocess.call(cov + ["html", rcfile], cwd=REPO, env=child_env)
        # App modules no suite ever imported produce no data at all, so they
        # are invisible in the table above — name them rather than hide them.
        from coverage import CoverageData
        data = CoverageData(os.path.join(HERE, ".coverage"))
        data.read()
        measured = {os.path.basename(f) for f in data.measured_files()}
        app_files = sorted(["zx-next-unite.py", "nextsync5.py"]
                           + [os.path.basename(p)
                              for p in glob.glob(os.path.join(REPO, "zxnu_*.py"))])
        unmeasured = [f for f in app_files if f not in measured]
        if unmeasured:
            print("\nNever imported by any suite (0%, absent from the table above):")
            for f in unmeasured:
                print(f"  {f}")

sys.exit(1 if failed else 0)
