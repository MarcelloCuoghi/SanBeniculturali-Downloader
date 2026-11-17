# PyInstaller spec: builds a single folder "dist/antenati-dl" containing
#   antenati-dl(.exe)      console executable (CLI; "antenati-dl gui" opens the GUI)
#   antenati-dl-gui(.exe)  windowed executable (GUI)
# Build with:  uv run --with pyinstaller pyinstaller packaging/antenati-dl.spec --noconfirm
# -*- mode: python ; coding: utf-8 -*-
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SRC = os.path.join(ROOT, "src")
EXCLUDES = ["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
            "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtCharts"]

cli_a = Analysis([os.path.join(SPECPATH, "cli_entry.py")], pathex=[SRC], excludes=EXCLUDES,
                 hiddenimports=["antenati_dl.gui.app"])
gui_a = Analysis([os.path.join(SPECPATH, "gui_entry.py")], pathex=[SRC], excludes=EXCLUDES)



def _pin_python_dlls(analysis):
    """Use the OpenSSL/libffi DLLs shipped with the building Python.

    PyInstaller may otherwise pick same-named DLLs found earlier on PATH (e.g. Git for Windows'
    mingw64 OpenSSL), which makes ``_ssl`` fail to import in the frozen app.
    """
    dll_dir = os.path.join(sys.base_prefix, "DLLs")
    if not os.path.isdir(dll_dir):
        return
    fixed = []
    for dest, src, kind in analysis.binaries:
        candidate = os.path.join(dll_dir, os.path.basename(dest))
        if os.path.dirname(dest) == "" and os.path.isfile(candidate):
            src = candidate
        fixed.append((dest, src, kind))
    analysis.binaries[:] = fixed


_pin_python_dlls(cli_a)
_pin_python_dlls(gui_a)

cli_pyz = PYZ(cli_a.pure)
gui_pyz = PYZ(gui_a.pure)

cli_exe = EXE(cli_pyz, cli_a.scripts, [], exclude_binaries=True, name="antenati-dl", console=True)
gui_exe = EXE(gui_pyz, gui_a.scripts, [], exclude_binaries=True, name="antenati-dl-gui", console=False)

coll = COLLECT(
    cli_exe, cli_a.binaries, cli_a.datas,
    gui_exe, gui_a.binaries, gui_a.datas,
    name="antenati-dl",
)
