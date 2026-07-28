"""PyPI / pipx entry point for ZX-Next-Unite.

The console script generated from [project.gui-scripts] imports THIS module
(cheap, no side effects) and calls main(), which imports zxnu_main — whose
module-level startup launches the app and exits via sys.exit(app.exec()).
Keeping the side-effectful import inside main() means importing zxnu_app
itself never starts the GUI."""


def main():
    import os

    # Mark this process as a wheel/pipx install BEFORE any zxnu import:
    # zxnu_config resolves the per-user data root (hdfg.cfg, logs, the
    # downloads/ tree all detection is rooted in) at import time, and
    # "installed" sends it to the platform app-data dir instead of the
    # script directory. setdefault so an explicit user override (e.g.
    # ZX_NEXT_UNITE_MODE=portable, or ZX_NEXT_UNITE_HOME=…) still wins.
    os.environ.setdefault("ZX_NEXT_UNITE_MODE", "installed")
    import zxnu_main  # noqa: F401  (importing launches the app)
