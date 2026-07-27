# Tests

Run everything:

```
python tests/run_all.py
```

Each file also runs standalone (`python tests/<file>.py`); they are plain
scripts printing `PASS`/`FAIL` lines and exiting non-zero on failure — no
pytest dependency. Every suite runs in its own process (they monkey-patch Qt
and the import system, so isolation matters).

## Coverage

```
python tests/run_all.py --coverage
```

measures line coverage of the application modules (`zx-next-unite.py`,
`zxnu_*.py`, `nextsync5.py`) across the whole run and prints a per-file table
(worst-covered first), plus an annotated-source HTML report in
`tests/htmlcov/`. Needs [coverage.py](https://coverage.readthedocs.io/)
(`python -m pip install coverage`); config lives in `tests/.coveragerc`.

Child processes are measured too: `run_all.py` exports
`COVERAGE_PROCESS_START` and injects `tests/covhook/sitecustomize.py` via
`PYTHONPATH`, so every Python process the suites spawn self-measures — and the
`[paths]` section of `.coveragerc` folds the offscreen UI suite's scratch copy
of `zx-next-unite.py` back onto the repo file when the data is combined. App
modules never imported by any suite produce no data at all (they can't appear
as 0% rows), so the runner names them explicitly after the table. Coverage is
reporting only; it never changes the exit code.

| File | What it covers | Needs |
|---|---|---|
| `test_api_parsers.py` | Unit tests for `zxnu_api` — the pure GetIt/ZXDB/zxArt parsers, URL builders and download-URL filters. No network, no QApplication. | PySide6 importable (via `zxnu_config`) |
| `test_pane_imports.py` | Free-variable tripwire for the extracted pane builders (`zxnu_zxdb_pane`, `zxnu_zxart_pane`, `zxnu_unite_pane`, …): asserts every name each builder uses resolves via its params, imports or star exports — catching the star-import-skips-underscores trap. | PySide6 |
| `test_hdf_workers.py` | Headless unit tests for the SD-Card tab's operation layer in `zxnu_workers` — the hdfmonkey transfer/delete worker bodies (`_run_get_task`, `_run_put_task`, `_run_put_external_task`, `_run_delete_task`), their recursive scan helpers, `is_directory` and the path/zip-name helpers. hdfmonkey is replaced by an in-memory fake image, so the full-disk detection, mkdir-exists ls fallback, cancel and error-continuation branches all run deterministically with no external tool, HDF file, or display. | PySide6 |
| `test_classic_sync.py` | The classic (Sync3/Sync4) NextSync server loop (`zxnu_workers.run_classic_sync_server`) over localhost against a mock dot: Sync4 handshake, chunked PC→Next pull honouring `max_payload`, Next→PC framed upload, conflict policies (overwrite/ignore), syncpoint bookkeeping and hostile-path sanitation. | PySide6 importable |
| `test_listen.py` | The Sync4 `-listen` wire protocol of the standalone server (`nextsync5.listen_session`) over a socketpair against a mock Next implementing the dot's half: ls/get/put/mkdir/rmdir/rm framing, checksums, retries. | PySide6-free (pure stdlib) |
| `test_remote_listen.py` | The app-side `-listen` worker (`zxnu_workers.run_remote_listen_server`) over a real localhost socket against the same mock Next: command queue in, Qt signals out, incl. rmtree walks, drives/free/rcpy/rfsize, failure paths. | PySide6 |
| `test_remote_explorer_widget.py` | Offscreen widget-layer tests for `zxnu_remote_explorer.RemoteExplorerWidget` (the NextSync tab's dual-pane file manager) — the counterpart of `test_remote_listen.py`'s worker-side coverage. A real widget under `QT_QPA_PLATFORM=offscreen` is driven through its seam: outgoing commands recorded from the injected `enqueue`, worker replies hand-fed via the `on_*` slots, `QInputDialog`/`QMessageBox` replaced by scripted fakes. Covers connection/drive state, listing + sort persistence, navigation and ls-failure fallback, the operation lifecycle (block/cancel/disconnect/background-rcpy overlay), get/put transfers, copy/cut/paste in all four directions with move markers, the rcpy free-space precheck, sync-root handling, local file ops (incl. a zip round-trip) and drag & drop. | PySide6 |
| `test_http_bridge.py` | End-to-end HTTP bridge (`zxnu_http_bridge`) against the mock Next, over both hosts: real HTTP → bridge → app worker, and real HTTP → bridge → `nextsync5.listen_session`. | PySide6, Flask (skipped by `run_all.py` when Flask is missing) |
| `test_retro_log_widget.py` | `RetroLogWidget.set_text_color` unit checks (hex/tuple parsing, clamping, fallback to phosphor green, per-instance tint). Uses the REAL Qt platform — pygame-adjacent widgets crash under offscreen Qt — but never shows a window. | PySide6 |
| `test_i18n.py` | `zxnu_i18n` — the UI translation layer behind Settings → "Application language:". Catalog integrity (ES/PT/PL/RU/CS/FR all carry the same keys, none empty, %-markers/spacing/emoji preserved) and the widget-tree retranslation walk on a synthetic tree: en→es→fr→en round-trips losslessly, dynamic rewrites are adopted not clobbered, and the deliberate exclusions hold (QTabWidget titles and QComboBox items are never rewritten — they are dispatch keys / option values). | PySide6 (offscreen) |
| `test_ui_offscreen.py` | Offscreen end-to-end UI suite: launches a COPY of `zx-next-unite.py` (own scratch dir + `hdfg.cfg` under the OS temp folder — the real config is never touched) under `QT_QPA_PLATFORM=offscreen` and drives the real widgets. Eight phases: (1) SD Card tab path rows, Up/Refresh, local + in-image path navigation on a generated test HDF, persistence to `hdfg.cfg`, Settings color picker; (2) startup restore of the saved in-image path + retro-log color; (3) startup fallback for a missing saved path; (4) NextSync classic explorer drag & drop; (5) watched-folder delete regression (full deletion, zero `QFileSystemWatcher` access-denied warnings); (6) self-update Settings toggle + the ".sync5 dot updated" advisory popup; (7) dotN advisory first-run silent persist and the toggle's default-ON; (8) the amber "load an image" hint pulse on 'Select NextZXOS disk Image' / 'Download NextZXOS Image' (runs while an emulator is detected and no image is loaded, stops once the test HDF loads; checked "stays off" on machines without an emulator); (9) UI language: `ui_language=es` starts the static UI in Spanish, tab titles stay untouched, and switching the Settings combo back to English live restores the originals and persists. Every phase disables the GitHub release check (or quits before it fires), so the suite never touches the network. | PySide6; phases 1–3 additionally need **hdfmonkey** (PATH or a populated `downloads/` folder — gitignored) and SKIP cleanly without it |

Notes:

- `test_ui_offscreen.py` without arguments runs all nine phases in separate
  subprocesses (a fresh `QApplication` per phase); pass a phase number to run
  just one, e.g. `python tests/test_ui_offscreen.py 5`. Phase 1 builds the
  scratch state (including the test HDF) that phases 2–3 reuse.
- The UI suite import-blocks pygame: pygame crashes natively under offscreen
  Qt, and the app degrades gracefully when it is "not installed".
- On Windows the UI suite creates a directory junction from its scratch dir to
  the repo's `downloads/` folder so the app copy discovers hdfmonkey the same
  way the real app does.
