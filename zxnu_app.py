"""PyPI / pipx entry point for ZX-Next-Unite.

The console script generated from [project.gui-scripts] imports THIS module
(cheap, no side effects) and calls main(), which imports zxnu_main — whose
module-level startup launches the app and exits via sys.exit(app.exec()).
Keeping the side-effectful import inside main() means importing zxnu_app
itself never starts the GUI."""


def main():
    import os
    import sys

    # `zx-next-unite --mcp-stdio` (9.7.57): the MCP stdio shim instead of the
    # GUI, for MCP clients that can only spawn a stdio server. Decided before
    # any Qt import (the shim relays to the server inside the RUNNING app).
    if "--mcp-stdio" in sys.argv[1:]:
        from zxnu_mcp import stdio_main
        sys.exit(stdio_main(sys.argv[1:]))

    # Mark this process as a wheel/pipx install BEFORE any zxnu import:
    # zxnu_config resolves the per-user data root (hdfg.cfg, logs, the
    # downloads/ tree all detection is rooted in) at import time, and
    # "installed" sends it to the platform app-data dir instead of the
    # script directory. setdefault so an explicit user override (e.g.
    # ZX_NEXT_UNITE_MODE=portable, or ZX_NEXT_UNITE_HOME=…) still wins.
    os.environ.setdefault("ZX_NEXT_UNITE_MODE", "installed")
    import zxnu_main  # noqa: F401  (importing launches the app)
