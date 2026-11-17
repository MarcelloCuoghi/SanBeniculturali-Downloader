"""PyInstaller entry point for the windowed executable."""

import multiprocessing
import sys

from antenati_dl.gui.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
