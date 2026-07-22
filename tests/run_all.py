"""Run the whole ZX-Next-Unite test suite and print one summary.

Usage:  python tests/run_all.py

Each test file is run in its own process (they patch/monkey with Qt and
sys.meta_path, so isolation matters). Suites whose optional dependency is
missing are reported as SKIPPED rather than failed:
  - test_http_bridge.py needs Flask (the bridge itself is optional in-app)
  - test_ui_offscreen.py phases 1-3 skip internally without hdfmonkey

Exit code: 0 when nothing failed (skips allowed), 1 otherwise.
"""
import importlib.util
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

SUITES = [
    # (file, timeout seconds, required import or None)
    ("test_listen.py",          120, None),
    ("test_remote_listen.py",   120, None),
    ("test_http_bridge.py",     240, "flask"),
    ("test_retro_log_widget.py", 120, None),
    ("test_ui_offscreen.py",    3600, None),   # runs its 5 phases itself
]

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
                             timeout=timeout)
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
sys.exit(1 if failed else 0)
