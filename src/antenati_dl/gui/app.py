"""Entry point of the graphical interface (``antenati-dl-gui`` / ``python -m antenati_dl.gui``)."""

from __future__ import annotations

import logging
import sys


def main(argv: list[str] | None = None) -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:  # pragma: no cover - depends on the installation
        print(
            "The graphical interface needs PySide6: pip install 'antenati-dl[gui]' (or pip install PySide6)",
            file=sys.stderr,
        )
        return 1

    from .. import __version__
    from .main_window import MainWindow

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    app = QApplication.instance() or QApplication(list(sys.argv if argv is None else argv))
    app.setApplicationName("antenati-dl")
    app.setOrganizationName("antenati-dl")
    app.setApplicationDisplayName("Antenati Downloader")
    app.setApplicationVersion(__version__)

    window = MainWindow()
    window.show()
    code = app.exec()
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
