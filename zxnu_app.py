"""PyPI / pipx entry point for ZX-Next-Unite.

The console script generated from [project.gui-scripts] imports THIS module
(cheap, no side effects) and calls main(), which imports zxnu_main — whose
module-level startup launches the app and exits via sys.exit(app.exec()).
Keeping the side-effectful import inside main() means importing zxnu_app
itself never starts the GUI."""


def main():
    import zxnu_main  # noqa: F401  (importing launches the app)
