"""Qt (PySide6) desktop interface of antenati-dl.

Start it with ``antenati-dl-gui`` or ``python -m antenati_dl.gui``.

Modules:

* :mod:`.app`          - ``main()`` entry point
* :mod:`.main_window`  - the main window wiring everything together
* :mod:`.navigator`    - lazy tree model of the portal hierarchy
* :mod:`.register_view`- register header + lazily loaded thumbnail grid
* :mod:`.downloads`    - download queue (QThread per running job) and its table model
* :mod:`.dialogs`      - settings, page preview and bulk-download confirmation dialogs
* :mod:`.workers`      - QThreadPool based background tasks delivering results on the GUI thread
* :mod:`.settings`     - persisted user settings (QSettings)
"""
