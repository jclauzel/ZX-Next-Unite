"""Thin launcher kept for the documented `python zx-next-unite.py` workflow
and the PyInstaller build. The application itself lives in zxnu_main.py (its
startup runs at module level), so the PyPI entry point (zxnu_app.main) and
`python -m zxnu_main` can reach it by import — this filename, with its
hyphens, cannot be imported."""
import zxnu_main  # noqa: F401  (importing launches the app)
