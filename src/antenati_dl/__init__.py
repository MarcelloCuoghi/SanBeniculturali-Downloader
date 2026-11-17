"""antenati-dl: browse and download registers from the Portale Antenati.

The package is split in layers:

* :mod:`antenati_dl.http`     - HTTP session with the headers/retries the portal requires
* :mod:`antenati_dl.models`   - plain dataclasses shared by every layer
* :mod:`antenati_dl.portal`   - scraping/parsing of the portal (archives, fondi, serie, search, manifests)
* :mod:`antenati_dl.download` - concurrent, resumable download engine with progress events
* :mod:`antenati_dl.cli`      - command line interface
* :mod:`antenati_dl.gui`      - Qt (PySide6) graphical interface
"""

__version__ = "1.0.0"

from .models import (  # noqa: F401  (re-exported public API)
    Archive,
    Facet,
    FacetTerm,
    Fondo,
    Manifest,
    Page,
    RegisterSummary,
    SearchPage,
    SearchQuery,
    Serie,
)
from .portal import Portal  # noqa: F401
