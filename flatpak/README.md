# Flatpak packaging

This directory holds everything needed to build ZX Next Unite as a Flatpak
for Linux — the least-served platform gets software-center discoverability
and painless updates.

| File | Role |
|---|---|
| `io.github.jclauzel.ZXNextUnite.yml` | flatpak-builder manifest (KDE 6.8 runtime + the PySide BaseApp; the app itself is installed as plain modules under `/app/lib/zx-next-unite` with a shell launcher) |
| `io.github.jclauzel.ZXNextUnite.desktop` | desktop entry (menu name, icon, categories) |
| `io.github.jclauzel.ZXNextUnite.metainfo.xml` | AppStream metadata — what software centers render (summary, description, screenshot, OARS rating, release list) |
| `icons/` | 128/256 px app icons — chunky pixel-art "N" with bright confetti dots (deliberately NOT the Sinclair rainbow, see the project's trade-dress rule) |

## How the sandbox maps to the app

- `--env=ZX_NEXT_UNITE_MODE=installed` routes `hdfg.cfg`, the logs and the
  whole `downloads/` tree to the XDG data dir
  (`~/.var/app/io.github.jclauzel.ZXNextUnite/data/zx-next-unite/`) — the
  same switch the PyPI entry point flips. `/app` stays read-only.
- `--share=network` covers the catalogue fetches AND the inbound NextSync
  (TCP 2048) / HTTP-bridge servers.
- `--filesystem=home` lets hdfmonkey (a child process) open SD images and
  sync roots anywhere in the user's home by plain path.
- The in-app self-updater detects `FLATPAK_ID` and steps aside — updates
  come from the Flatpak remote.
- hdfmonkey: the "Download and install HDF Monkey" button works as usual;
  the jjjs `linux-musl` build is static and runs inside the sandbox.
- The four optional extras (pygame-ce retro modes + Alien Floyd's, itch-dl
  / the itch.io tab, the Flask HTTP bridge, Send2Trash) are bundled as
  pinned wheels (the `python3-extras` module), so their Settings toggles
  work out of the box.
- Emulator launches are delegated to the host via `flatpak-spawn --host`
  when sandboxed (hence the `--talk-name=org.freedesktop.Flatpak`
  finish-arg): "Launch MAME via Flatpak" runs `flatpak run org.mamedev.MAME`
  on the host (`mame_flatpak_command` in `zxnu_config.py`), and CSpect runs
  under the HOST's mono (which must be installed there) — all involved
  paths live under the user's real home, so they resolve unchanged.
- Manual hdfmonkey drops are found in the stray
  `~/.var/app/<id>/downloads/` location as well as the real data root
  (`flatpak_stray_download_root` in `zxnu_config.py`).

## Building locally (on Linux)

```
flatpak remote-add --if-not-exists --user flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak-builder --force-clean --user --install-deps-from=flathub \
    --repo=repo builddir flatpak/io.github.jclauzel.ZXNextUnite.yml
flatpak build-bundle repo zx-next-unite.flatpak io.github.jclauzel.ZXNextUnite
flatpak install --user zx-next-unite.flatpak
```

## CI

`.github/workflows/flatpak.yml` builds the bundle in the
`flathub-infra/flatpak-github-actions` KDE 6.8 container and uploads
`zx-next-unite.flatpak` as a workflow artifact. Triggers: manual
(workflow_dispatch, once the workflow is on the default branch), any
push touching `flatpak/**` or the workflow itself — so manifest changes are
CI-validated before they merge — and `v*` tag creation, where the bundle
(built from the exact tag commit) is additionally attached to the tag's
draft release as `zx-next-unite-<tag>-linux-x86_64.flatpak` (stdlib-only
uploader, drafts included; it retries while `release.yml` creates the
draft, then degrades to a warning). It is deliberately separate from
`release.yml` so the self-updater's release-asset contract (exe / tar.gz /
zip) stays untouched and a Flatpak failure can never block a release.

## Submitting to Flathub (manual, once)

1. Swap the app module's `type: dir` source for a git source pinned to a
   release tag and commit (Flathub forbids dir sources):

   ```yaml
   sources:
     - type: git
       url: https://github.com/jclauzel/ZX-Next-Unite.git
       tag: v9.6.1
       commit: <full commit sha of the tag>
   ```

2. Verify the app id: `io.github.jclauzel.ZXNextUnite` is auto-verifiable
   via the GitHub account that owns the repo.
3. Fork https://github.com/flathub/flathub, create branch `new-pr`, add the
   manifest (+ desktop/metainfo/icons or keep them fetched from the git
   source, which this manifest does), open a PR against the `new-pr` base
   branch and follow the reviewer feedback.
4. After acceptance Flathub creates `flathub/io.github.jclauzel.ZXNextUnite`;
   version bumps are PRs updating the pinned tag/commit there (a bot can be
   set up to open them automatically on new GitHub releases).
5. Keep `metainfo.xml`'s `<releases>` current — software centers show it.
