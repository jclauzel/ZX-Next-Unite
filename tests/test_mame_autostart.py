"""MAME auto-start tests (SD Card tab).

The MAME twin of test_cspect_autostart.py. With MAME usable the SD Card tab
offers two right-click actions that boot a file straight into the emulator:

  * image explorer  -> "Start MAME with file X"        (file is on the image)
  * local explorer  -> "Send to SD Card and start MAME with file X"
                       (upload into the image first, then start)

WHAT MAME ACTUALLY NEEDS — verified against a real MAME 0.288 rather than
assumed, because the widely repeated advice is wrong. A raw file does NOT need
a software-list entry and does NOT need copying into a software/<system>
folder: `mame -listmedia tbblue` reports .nex among the *snapshot* extensions
and

    mame tbblue -snapshot <host path>.nex -hard1 <image>

boots it (checked to run and, with a bogus path, to fail loudly). The software
list the folklore refers to, hash/specnext_sd.xml, describes hash-matched
SD-card images — a different mechanism that cannot describe a file it has never
seen, so dropping a .nex into software/specnext would match nothing.

The path must be a HOST path: MAME's loader knows nothing about the contents of
the emulated SD card, so a file living on the image has to be extracted first.
That is the same trap the CSpect feature shipped with, and it is pinned below.

The launch itself needs a built MainWindow and a real emulator, so the argv
assembly is checked at source level.

Run with: python tests/test_mame_autostart.py
"""
import ast
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

from zxnu_config import (  # noqa: E402
    MAME_CASSETTE_EXTENSIONS,
    MAME_QUICKLOAD_EXTENSIONS,
    MAME_SNAPSHOT_EXTENSIONS,
    mame_autostart_argument,
    mame_can_autostart,
)

FAIL = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


# ---- the media switch per file type -------------------------------------
check("a .nex loads as a snapshot (the format the feature is for)",
      mame_autostart_argument("/games/foo.nex") == "-snapshot")
check("matching is case-insensitive",
      mame_autostart_argument("/A.NEX") == "-snapshot"
      and mame_autostart_argument("/b.SnA") == "-snapshot")
check("every snapshot extension maps to -snapshot",
      all(mame_autostart_argument("/x" + e) == "-snapshot"
          for e in MAME_SNAPSHOT_EXTENSIONS))
check("tapes map to -cassette, not -snapshot",
      all(mame_autostart_argument("/x" + e) == "-cassette"
          for e in MAME_CASSETTE_EXTENSIONS))
check("quickloads map to -quickload",
      all(mame_autostart_argument("/x" + e) == "-quickload"
          for e in MAME_QUICKLOAD_EXTENSIONS))
check("the three media sets do not overlap",
      not (set(MAME_SNAPSHOT_EXTENSIONS) & set(MAME_CASSETTE_EXTENSIONS))
      and not (set(MAME_SNAPSHOT_EXTENSIONS) & set(MAME_QUICKLOAD_EXTENSIONS))
      and not (set(MAME_CASSETTE_EXTENSIONS) & set(MAME_QUICKLOAD_EXTENSIONS)))

# ---- the gate stays useful ----------------------------------------------
check("a readme is NOT offered", not mame_can_autostart("/docs/readme.txt"))
check("an extensionless name is not offered",
      not mame_can_autostart("/games/README"))
check("empty / None are safe",
      not mame_can_autostart("") and not mame_can_autostart(None)
      and mame_autostart_argument(None) == "")
check("a name merely CONTAINING an extension is not offered",
      not mame_can_autostart("/games/nex-collection"))
check("generic audio is not offered as a tape",
      not mame_can_autostart("/music/track.wav")
      and not mame_can_autostart("/music/track.flac"))
check("CSpect-only disk formats are not offered for MAME",
      not any(mame_can_autostart("/x" + e)
              for e in (".trd", ".scl", ".dsk", ".slt", ".szx")),
      "MAME's Next drivers take no such media")

# ---- the launcher takes the file and builds the right argv ---------------
src = open(os.path.join(REPO, "zxnu_emulator_ops.py"), encoding="utf-8").read()
tree = ast.parse(src)
fn = next((n for n in ast.walk(tree)
           if isinstance(n, ast.FunctionDef) and n.name == "launch_mame"), None)
check("launch_mame accepts the auto-start file", fn is not None
      and any(a.arg == "autostart_file" for a in fn.args.args))
if fn is not None:
    body = ast.dump(fn)
    # button_start_mame.clicked connects straight to launch_mame, so Qt hands
    # it the checked bool. Without the guard that bool becomes a "file name".
    # Matched precisely: a bare `"isinstance" in body` would also be satisfied
    # by any unrelated isinstance() the function grows later.
    guarded = any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "isinstance"
        and n.args and isinstance(n.args[0], ast.Name)
        and n.args[0].id == "autostart_file"
        for n in ast.walk(fn))
    check("a Qt clicked() bool cannot be taken for a file name",
          guarded, "launch_mame is also a clicked() slot")
    check("the auto-start argument is made an absolute host path",
          "os.path.abspath(autostart_file" in src)
    check("no leading-slash stripping (host path, not an image path)",
          'autostart_file.strip().lstrip("/")' not in src)
    check("the media switch is chosen per file type, not hard-coded",
          "mame_autostart_argument" in body)
    # -hard1 <image> has always been documented as the final argument.
    check("the file is added BEFORE -hard1, which stays last",
          src.index("_auto_switch, _auto") < src.index("MAME_HARD_DISK_PARAMETER, mame_image"))
    check("an unsupported file does not silently vanish",
          "MAME cannot load {name} directly" in src)

# ---- booting a file that lives on the image must extract it first --------
ops = open(os.path.join(REPO, "zxnu_sdcard_ops.py"), encoding="utf-8").read()
check("there is an extract-then-launch step for image files",
      "def _mame_start_from_image" in ops)
seg_img = ops[ops.find("def _mame_start_from_image"):]
seg_img = seg_img[:seg_img.find("def _image_remote_zip")]
check("it pulls the file out of the image with the hdfmonkey helper",
      "image_get_paths_to_local" in seg_img)
check("it launches the EXTRACTED host copy, not the in-image path",
      "_launch_mame_fn(local)" in seg_img)
check("a failed extraction does not start MAME",
      "could not be read from the image" in seg_img)
check("the temp copy is not deleted out from under a detached MAME",
      "shutil.rmtree(tmp" in seg_img
      and seg_img.count("shutil.rmtree(tmp") == 1,
      "rmtree must only run on the failure path")

# The local action must launch the LOCAL file, never the in-image copy.
seg_loc = ops[ops.find("def _send_and_start_mame"):]
seg_loc = seg_loc[:seg_loc.find("menu.addAction")]
check("the local action starts MAME on the local (host) file",
      "_launch_mame_fn(_p)" in seg_loc)
check("the local action never builds an in-image path for MAME",
      "in_image" not in seg_loc)
check("a failed transfer does not start MAME",
      "if not success" in seg_loc)
check("the local action uploads first, then launches",
      "on_complete=_after" in seg_loc,
      "upload must drive the launch through its completion callback")

# ---- both menus are gated on MAME being usable --------------------------
for label, marker in (
        ("image explorer", 'ui_tr_now("Start MAME with file {name}")'),
        ("local explorer",
         'ui_tr_now("Send to SD Card and start MAME with file {name}"')):
    at = ops.find(marker)
    check(f"the {label} action exists", at != -1)
    if at == -1:
        continue
    # The gate is the `if` above the addAction; the local action's handler body
    # sits in between, so look back far enough to clear it.
    window = ops[max(0, at - 2600):at]
    check(f"the {label} action is gated on MAME being usable",
          "_mame_usable()" in window,
          "must use _mame_usable() so Flatpak MAME counts too")
    check(f"the {label} action is gated on a loadable extension",
          "mame_can_autostart" in window)

# Top of the menu, as asked for, and not at the price of the CSpect entry.
img_at = ops.find('ui_tr_now("Start MAME with file {name}")')
newfolder_at = ops.find("new_folder_label = ")
check("the image-explorer action sits above the rest of that menu",
      img_at != -1 and newfolder_at != -1 and img_at < newfolder_at,
      f"mame={img_at} newfolder={newfolder_at}")
cspect_img_at = ops.find('ui_tr_now("Start CSpect with file {name}")')
check("the CSpect image action still sits at the top too",
      cspect_img_at != -1 and cspect_img_at < newfolder_at)
check("one separator covers both emulator entries",
      "_emu_entry_added" in ops,
      "a per-entry separator would leave a stray line when only one is shown")

# ---- every new string is translated -------------------------------------
from zxnu_i18n import CATALOGS  # noqa: E402

NEW_STRINGS = (
    "Start MAME with file {name}",
    "Send to SD Card and start MAME with file {name}",
    "Extracting {name} from the image, then starting MAME…",
    "Start MAME: {name} could not be read from the image, MAME was not started.",
    "Send to SD Card and start MAME: the transfer failed, MAME was not started.",
    "Sending {name} to the SD card image, then starting MAME…",
    "MAME cannot load {name} directly; starting MAME without it.",
)
missing = [(lg, s) for s in NEW_STRINGS for lg in CATALOGS if not CATALOGS[lg].get(s)]
check("every new MAME string is translated in all languages",
      not missing, f"{len(missing)} gap(s): {missing[:3]}")
broken = []
for s in NEW_STRINGS:
    for lg in CATALOGS:
        try:
            (CATALOGS[lg].get(s) or s).format(name="beast.nex")
        except (KeyError, IndexError):
            broken.append((lg, s))
check("every translation renders with its placeholder", not broken, str(broken[:3]))

print()
if FAIL:
    print(f"{len(FAIL)} FAILURE(S): " + ", ".join(FAIL))
    sys.exit(1)
print("all MAME auto-start checks passed")
sys.exit(0)
