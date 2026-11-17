"""PyInstaller entry point for the console executable."""

import multiprocessing
import sys

from antenati_dl.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
