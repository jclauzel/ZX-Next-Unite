"""CSpect auto-start tests (SD Card tab).

With CSpect installed the SD Card tab offers two right-click actions that boot
a file straight into the emulator:

  * image explorer  -> "Start CSpect with file X"           (file already on
                                                             the mounted image)
  * local explorer  -> "Send to SD Card and start CSpect with file X"
                       (upload into the image first, then start)

Both end up appending the file to CSpect's command line, which CSpect resolves
against the -mmc root:

    CSpect.exe … -zxnext -mmc=<image> games/foo.nex

Covered here: the extension gate that decides whether the actions are offered
at all, and the source-level contract of the two menus (both must be gated on
CSpect actually being installed, and must sit at the top of their menu). The
launch itself needs a built MainWindow and a real emulator, so it is not run.

Run with: python tests/test_cspect_autostart.py
"""
import ast
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

from zxnu_config import (  # noqa: E402
    CSPECT_AUTOSTART_EXTENSIONS,
    cspect_can_autostart,
)

FAIL = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


# ---- the extension gate -------------------------------------------------
check("a .nex is offered (the format the feature was asked for)",
      cspect_can_autostart("/games/foo.nex"))
check("matching is case-insensitive", cspect_can_autostart("/A.NEX")
      and cspect_can_autostart("/b.SnA"))
check("snapshots and tapes are offered too",
      all(cspect_can_autostart("/x" + e) for e in CSPECT_AUTOSTART_EXTENSIONS))
check("a readme is NOT offered (the menu stays useful)",
      not cspect_can_autostart("/docs/readme.txt"))
check("an extensionless name is not offered",
      not cspect_can_autostart("/games/README"))
check("empty / None are safe", not cspect_can_autostart("")
      and not cspect_can_autostart(None))
check("a name merely CONTAINING an extension is not offered",
      not cspect_can_autostart("/games/nex-collection"))

# ---- the launcher takes the file as a trailing argument -----------------
src = open(os.path.join(REPO, "zxnu_emulator_ops.py"), encoding="utf-8").read()
tree = ast.parse(src)
fn = next((n for n in ast.walk(tree)
           if isinstance(n, ast.FunctionDef) and n.name == "launch_cspect"), None)
check("launch_cspect accepts the auto-start file", fn is not None
      and any(a.arg == "autostart_file" for a in fn.args.args))
if fn is not None:
    body = ast.dump(fn)
    # Qt hands clicked() slots a bool; that must not be mistaken for a path.
    check("a Qt clicked() bool cannot be taken for a file name",
          "isinstance" in body,
          "launch_cspect is also a clicked() slot")
    check("the file is appended after -mmc, not before",
          src.index("-mmc=") < src.index("autostart_file.strip().lstrip"))

# ---- both menus are gated on CSpect being installed ---------------------
ops = open(os.path.join(REPO, "zxnu_sdcard_ops.py"), encoding="utf-8").read()
for label, marker in (
        ("image explorer", 'ui_tr_now("Start CSpect with file {name}")'),
        ("local explorer",
         'ui_tr_now("Send to SD Card and start CSpect with file {name}"')):
    at = ops.find(marker)
    check(f"the {label} action exists", at != -1)
    if at == -1:
        continue
    # The gate is the `if` above the addAction. The local action's handler
    # body sits in between, so look back far enough to clear it.
    window = ops[max(0, at - 2600):at]
    check(f"the {label} action is gated on CSpect being installed",
          "_cspect_executable_path" in window, "no detection gate found")
    check(f"the {label} action is gated on a bootable extension",
          "cspect_can_autostart" in window)

# It must be the FIRST entry of the image menu (asked for: top of the list).
img_at = ops.find('ui_tr_now("Start CSpect with file {name}")')
newfolder_at = ops.find("new_folder_label = ")
check("the image-explorer action sits above the rest of that menu",
      img_at != -1 and newfolder_at != -1 and img_at < newfolder_at,
      f"cspect={img_at} newfolder={newfolder_at}")

# The local action must upload BEFORE launching, and not launch on failure.
loc_at = ops.find("def _send_and_start")
seg = ops[loc_at:loc_at + 1400] if loc_at != -1 else ""
check("the local action uploads first, then launches",
      seg.index("image_upload_external_paths") > seg.index("_launch_cspect_fn")
      or "on_complete=_after" in seg,
      "upload must drive the launch through its completion callback")
check("a failed transfer does not start CSpect",
      "if not success" in seg)

print()
if FAIL:
    print(f"{len(FAIL)} FAILURE(S): " + ", ".join(FAIL))
    sys.exit(1)
print("all CSpect auto-start checks passed")
sys.exit(0)
