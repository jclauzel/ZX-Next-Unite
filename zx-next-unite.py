"""Thin launcher kept for the documented `python zx-next-unite.py` workflow
and the PyInstaller build. The application itself lives in zxnu_main.py (its
startup runs at module level), so the PyPI entry point (zxnu_app.main) and
`python -m zxnu_main` can reach it by import — this filename, with its
hyphens, cannot be imported.

``--mcp-stdio`` (9.7.57) turns this launcher into the MCP stdio shim instead
of the GUI: an MCP client that can only spawn a stdio server (Claude
Desktop's config file) runs ``zx-next-unite --mcp-stdio``, and every JSON-RPC
line it writes is relayed to the MCP server inside the RUNNING app. Decided
before any Qt import, so the shim costs no window and no Qt at all."""
import sys

if "--mcp-stdio" in sys.argv[1:]:
    from zxnu_mcp import stdio_main
    sys.exit(stdio_main(sys.argv[1:]))

import zxnu_main  # noqa: F401,E402  (importing launches the app)
