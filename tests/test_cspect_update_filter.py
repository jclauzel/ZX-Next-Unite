"""The CSpect update check leaves password-protected builds out (9.7.58).

CSpect's itch.io page lists more than the public releases: "Beta", "Patrons"
and "Builders" uploads sit beside them, password protected - only a few
people can install them. The startup update check picked the newest upload
whatever it was, so everybody was offered ``CSpect4_5_3_0_BuilderPatrons``
(reported 2026-11-05, with the prompt's screenshot).

Settings -> "CSpect - Discard Beta or Patrons, Builders updates" (off by
default, ``cspect_discard_restricted_updates`` in hdfg.cfg) drops every
upload whose file or version name carries "beta", "patron" or "builder"
(any capitalisation) BEFORE the newest is picked. Dropped from the list, not
merely skipped over: the install path downloads ``uploads[0]``, and the
build left on top is what gets compared - so a newer PUBLIC build hidden
behind a Patrons one is still offered.

Covered headlessly (the itch.io listing is stubbed; no network):

* ``cspect_build_is_restricted`` and the setting's decoder;
* ``zxnu_itchio.latest_cspect_upload``: unchanged with no words (the beta
  rule that was always there still holds), the filter with them, what it
  reports as discarded, and the all-discarded case;
* the caller in ``zxnu_emulator_ops``: the words are passed only while the
  setting is on, and a skipped build is said in the log;
* the Settings row, right under the check it filters (its restore and a
  click are driven in offscreen phase 7).

Run: python tests/test_cspect_update_filter.py  (no pytest, matching the suite).
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, REPO)

import zxnu_itchio  # noqa: E402
from zxnu_config import (  # noqa: E402
    CONFIG_FILE_SETTINGS, CSPECT_RESTRICTED_BUILD_WORDS,
    SETTING_CSPECT_DISCARD_RESTRICTED_UPDATES, build_version_key,
    cspect_build_is_restricted, cspect_discard_restricted_updates,
    cspect_version_newer)

FAIL = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label
          + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        FAIL.append(label)


def upload(name, filename=None):
    return {"id": abs(hash(name)) % 100000, "size": 1,
            "filename": filename or (name + ".zip"),
            "version_name": name, "version_key": build_version_key(name)}


def with_listing(uploads, call):
    """Run *call* with itch.io's listing stubbed to *uploads* (newest first,
    as list_owned_uploads returns them)."""
    real = zxnu_itchio.list_owned_uploads
    zxnu_itchio.list_owned_uploads = lambda game, api_key: (
        None if uploads is None else (4242, 77, list(uploads)))
    try:
        return call()
    finally:
        zxnu_itchio.list_owned_uploads = real


def test_words():
    print("--- which builds are restricted")
    check("the three words", CSPECT_RESTRICTED_BUILD_WORDS == ("beta", "patron", "builder"))
    for name in ("CSpect4_5_3_0_BuilderPatrons", "CSpect4_5_3_0_Patrons",
                 "cspect4_5_3_0_builders", "CSPECT4_5_3_0_BETA", "CSpect_Beta2",
                 "CSpect4_5_3_0_patron.zip", "Builder"):
        check(f"restricted: {name}", cspect_build_is_restricted(name))
    for name in ("CSpect4_5_1_0", "CSpect4_5_3_0", "CSpect4_5_3_0.zip", "", None,
                 "CSpect_alpha"):
        check(f"public: {name!r}", not cspect_build_is_restricted(name))
    check("either of the two names is enough (file name OR version name)",
          cspect_build_is_restricted("CSpect4_5_3_0", "CSpect4_5_3_0_Patrons.zip")
          and cspect_build_is_restricted("CSpect4_5_3_0_Patrons", "x.zip")
          and not cspect_build_is_restricted("CSpect4_5_3_0", "CSpect4_5_3_0.zip"))
    check("no words, nothing is restricted",
          not cspect_build_is_restricted("CSpect_Beta", words=()))
    check("the setting is OFF by default",
          not cspect_discard_restricted_updates({})
          and not cspect_discard_restricted_updates(None)
          and not cspect_discard_restricted_updates(
              {SETTING_CSPECT_DISCARD_RESTRICTED_UPDATES: ""}))
    check("...on when saved true, off when saved false",
          cspect_discard_restricted_updates(
              {SETTING_CSPECT_DISCARD_RESTRICTED_UPDATES: "true"})
          and not cspect_discard_restricted_updates(
              {SETTING_CSPECT_DISCARD_RESTRICTED_UPDATES: "false"}))
    check("...and saved to hdfg.cfg (an unregistered key reads but never "
          "persists)",
          SETTING_CSPECT_DISCARD_RESTRICTED_UPDATES == "cspect_discard_restricted_updates"
          and SETTING_CSPECT_DISCARD_RESTRICTED_UPDATES in CONFIG_FILE_SETTINGS)


def test_lookup():
    print("--- latest_cspect_upload")
    patrons = upload("CSpect4_5_3_0_BuilderPatrons")
    beta = upload("CSpect4_5_2_5_Beta")
    public_new = upload("CSpect4_5_2_0")
    public_old = upload("CSpect4_5_1_0")
    listing = [patrons, beta, public_new, public_old]

    info = with_listing(listing, lambda: zxnu_itchio.latest_cspect_upload("key"))
    check("setting off: the Patrons build is the newest - the reported prompt",
          info and info["version_name"] == "CSpect4_5_3_0_BuilderPatrons",
          repr(info and info["version_name"]))
    check("...betas were already left out, and still are",
          [u["version_name"] for u in info["uploads"]]
          == ["CSpect4_5_3_0_BuilderPatrons", "CSpect4_5_2_0", "CSpect4_5_1_0"])

    dropped = []
    info = with_listing(listing, lambda: zxnu_itchio.latest_cspect_upload(
        "key", discard_words=CSPECT_RESTRICTED_BUILD_WORDS, discarded=dropped))
    check("setting on: the newest PUBLIC build is the one offered",
          info and info["version_name"] == "CSpect4_5_2_0",
          repr(info and info["version_name"]))
    check("...and it is uploads[0], which is what the install downloads",
          info["uploads"][0] is public_new
          and info["filename"] == "CSpect4_5_2_0.zip"
          and info["version_key"] == public_new["version_key"])
    check("...no restricted build is left in the list at all",
          [u["version_name"] for u in info["uploads"]]
          == ["CSpect4_5_2_0", "CSpect4_5_1_0"])
    check("...the Patrons build is reported as discarded (the beta is not: "
          "that rule is the old, silent one)",
          dropped == ["CSpect4_5_3_0_BuilderPatrons"], str(dropped))
    check("...a newer public build hidden behind a Patrons one is still an "
          "update for an older install",
          cspect_version_newer(info["version_name"], "CSpect4_5_1_0"))

    # The word only in the FILE name (the version name was typed clean).
    sneaky = upload("CSpect4_5_4_0", filename="CSpect4_5_4_0_patron.zip")
    dropped = []
    info = with_listing([sneaky, public_old], lambda: zxnu_itchio.latest_cspect_upload(
        "key", discard_words=CSPECT_RESTRICTED_BUILD_WORDS, discarded=dropped))
    check("the word in the file name alone is enough",
          info["version_name"] == "CSpect4_5_1_0" and dropped == ["CSpect4_5_4_0"],
          f"{info['version_name']} {dropped}")

    dropped = []
    info = with_listing([patrons, upload("CSpect4_5_3_0_Builders")],
                        lambda: zxnu_itchio.latest_cspect_upload(
                            "key", discard_words=CSPECT_RESTRICTED_BUILD_WORDS,
                            discarded=dropped))
    check("only restricted builds listed: nothing to offer",
          info is None and len(dropped) == 2, f"{info} {dropped}")
    check("without the out-list the call still works",
          with_listing(listing, lambda: zxnu_itchio.latest_cspect_upload(
              "key", discard_words=("patron",)))["version_name"] == "CSpect4_5_2_0")
    check("case does not matter in the words either",
          with_listing(listing, lambda: zxnu_itchio.latest_cspect_upload(
              "key", discard_words=("PATRON",)))["version_name"] == "CSpect4_5_2_0")
    check("an account that does not own CSpect is still None",
          with_listing(None, lambda: zxnu_itchio.latest_cspect_upload(
              "key", discard_words=CSPECT_RESTRICTED_BUILD_WORDS)) is None)


def test_wiring():
    print("--- the startup check and the Settings row")
    with open(os.path.join(REPO, "zxnu_emulator_ops.py"), encoding="utf-8") as fh:
        ops = fh.read()
    start = ops.index("    def _check_cspect_update_async(")
    chk = ops[start:ops.index("host._check_cspect_update_async = _check_cspect_update_async", start)]
    check("the check reads the setting each time it runs",
          "cspect_discard_restricted_updates(configuration_dictionary)" in chk)
    check("the words are passed only while the setting is on",
          "discard_words=CSPECT_RESTRICTED_BUILD_WORDS if _discard else ()" in chk)
    check("only builds that would have been an update are mentioned",
          "cspect_version_newer(n, installed_name)" in chk)
    check("a skipped build is said in the log, before the verdict",
          'if info.get("discarded"):' in chk
          and chk.index('if info.get("discarded"):') < chk.index('if not info.get("newer"):'))
    check("nothing public left: the check says why instead of 'not owned'",
          "only beta / patron / builder builds" in chk)
    with open(os.path.join(REPO, "zxnu_settings_pane.py"), encoding="utf-8") as fh:
        pane = fh.read()
    check("the Settings checkbox carries the asked label",
          '"CSpect - Discard Beta or Patrons, Builders updates"' in pane)
    from zxnu_settings_pane import SETTINGS_TAB_ROWS
    rows = list(SETTINGS_TAB_ROWS)
    check("its row sits right under the update check it filters",
          rows.index("cspect_discard_restricted_updates")
          == rows.index("cspect_update_check") + 1)
    with open(os.path.join(REPO, "zxnu_i18n.py"), encoding="utf-8") as fh:
        i18n = fh.read()
    check("the label is in all six catalogs",
          i18n.count('"CSpect - Discard Beta or Patrons, Builders updates":') == 6)


def main():
    test_words()
    test_lookup()
    test_wiring()
    print()
    if FAIL:
        print(f"FAILED: {len(FAIL)}")
        for f in FAIL:
            print("  -", f)
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    main()
