# Tests

Run everything:

```
python tests/run_all.py
```

Each file also runs standalone (`python tests/<file>.py`); they are plain
scripts printing `PASS`/`FAIL` lines and exiting non-zero on failure — no
pytest dependency. Every suite runs in its own process (they monkey-patch Qt
and the import system, so isolation matters).

| File | What it covers | Needs |
|---|---|---|
| `test_api_parsers.py` | Unit tests for `zxnu_api` — the pure GetIt/ZXDB/zxArt parsers, URL builders and download-URL filters. No network, no QApplication. | PySide6 importable (via `zxnu_config`) |
| `test_classic_sync.py` | The classic (Sync3/Sync4) NextSync server loop (`zxnu_workers.run_classic_sync_server`) over localhost against a mock dot: Sync4 handshake, chunked PC→Next pull honouring `max_payload`, Next→PC framed upload, conflict policies (overwrite/ignore), syncpoint bookkeeping and hostile-path sanitation. | PySide6 importable |
| `test_listen.py` | The Sync4 `-listen` wire protocol of the standalone server (`nextsync5.listen_session`) over a socketpair against a mock Next implementing the dot's half: ls/get/put/mkdir/rmdir/rm framing, checksums, retries. | PySide6-free (pure stdlib) |
| `test_remote_listen.py` | The app-side `-listen` worker (`zxnu_workers.run_remote_listen_server`) over a real localhost socket against the same mock Next: command queue in, Qt signals out, incl. rmtree walks, drives/free/rcpy/rfsize, failure paths. | PySide6 |
| `test_http_bridge.py` | End-to-end HTTP bridge (`zxnu_http_bridge`) against the mock Next, over both hosts: real HTTP → bridge → app worker, and real HTTP → bridge → `nextsync5.listen_session`. | PySide6, Flask (skipped by `run_all.py` when Flask is missing) |
| `test_retro_log_widget.py` | `RetroLogWidget.set_text_color` unit checks (hex/tuple parsing, clamping, fallback to phosphor green, per-instance tint). Uses the REAL Qt platform — pygame-adjacent widgets crash under offscreen Qt — but never shows a window. | PySide6 |
| `test_ui_offscreen.py` | Offscreen end-to-end UI suite: launches a COPY of `zx-next-unite.py` (own scratch dir + `hdfg.cfg` under the OS temp folder — the real config is never touched) under `QT_QPA_PLATFORM=offscreen` and drives the real widgets. Seven phases: (1) SD Card tab path rows, Up/Refresh, local + in-image path navigation on a generated test HDF, persistence to `hdfg.cfg`, Settings color picker; (2) startup restore of the saved in-image path + retro-log color; (3) startup fallback for a missing saved path; (4) NextSync classic explorer drag & drop; (5) watched-folder delete regression (full deletion, zero `QFileSystemWatcher` access-denied warnings); (6) self-update Settings toggle + the ".sync5 dot updated" advisory popup; (7) dotN advisory first-run silent persist and the toggle's default-ON. Every phase disables the GitHub release check (or quits before it fires), so the suite never touches the network. | PySide6; phases 1–3 additionally need **hdfmonkey** (PATH or a populated `downloads/` folder — gitignored) and SKIP cleanly without it |

Notes:

- `test_ui_offscreen.py` without arguments runs all five phases in separate
  subprocesses (a fresh `QApplication` per phase); pass a phase number to run
  just one, e.g. `python tests/test_ui_offscreen.py 5`. Phase 1 builds the
  scratch state (including the test HDF) that phases 2–3 reuse.
- The UI suite import-blocks pygame: pygame crashes natively under offscreen
  Qt, and the app degrades gracefully when it is "not installed".
- On Windows the UI suite creates a directory junction from its scratch dir to
  the repo's `downloads/` folder so the app copy discovers hdfmonkey the same
  way the real app does.
