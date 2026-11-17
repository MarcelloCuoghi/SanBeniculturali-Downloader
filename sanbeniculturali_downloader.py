"""Backward compatible launcher (and PyInstaller entry point).

* without arguments it opens the GUI;
* with arguments it behaves like the ``antenati-dl`` command line tool.

Examples::

    python sanbeniculturali_downloader.py
    python sanbeniculturali_downloader.py archives
    python sanbeniculturali_downloader.py download an_ua37845354 --pages 1-10
"""

import multiprocessing
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):  # running from a source checkout
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def main() -> int:
    from antenati_dl.cli import main as cli_main

    if len(sys.argv) > 1:
        return cli_main(sys.argv[1:])
    return cli_main(["gui"])


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
