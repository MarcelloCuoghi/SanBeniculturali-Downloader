"""Plain data structures shared by the portal client, the downloader, the CLI and the GUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

PORTAL_URL = "https://antenati.cultura.gov.it"
ARK_PREFIX = f"{PORTAL_URL}/ark:/12657/"

#: Separator the portal uses to join several facet terms inside the single ``s_facet_query``
#: parameter. It is the *literal* text ``%2C`` (so it travels URL-encoded as ``%252C``).
FACET_SEPARATOR = "%2C"

#: Facet fields exposed by the search page, in hierarchical order, with the portal's labels.
FACET_FIELDS: dict[str, str] = {
    "conservatore_s": "Archivio",
    "primo_livello_label_s": "Fondo",
    "localita_fondo_s": "Serie",
    "localita_ss": "Località",
    "tipologia_ss": "Tipologia",
    "anni_is": "Anno",
}


@dataclass(frozen=True)
class Archive:
    """An Archivio di Stato (top level of the hierarchy)."""

    id: int
    name: str
    url: str | None = None  # the "Scopri l'archivio" page

    @property
    def explore_url(self) -> str:
        return f"{PORTAL_URL}/archive/?archivio={self.id}"


@dataclass(frozen=True)
class Serie:
    """A series inside a fondo; in practice one per comune / quartiere."""

    id: int
    name: str


@dataclass(frozen=True)
class Fondo:
    """A fondo (e.g. "Stato civile italiano") of an archive, with its series."""

    id: int
    name: str
    archive_id: int
    series: tuple[Serie, ...] = ()


@dataclass(frozen=True)
class FacetTerm:
    """One selectable value of a facet, e.g. ``tipologia_ss:Nati (13)``."""

    field: str
    value: str  # human readable value (unquoted, HTML-unescaped)
    count: int
    query: str  # raw term to put in ``s_facet_query`` (e.g. ``tipologia_ss:"Morti, allegati"``)

    @property
    def label(self) -> str:
        return f"{self.value} ({self.count})"


@dataclass
class Facet:
    field: str
    label: str
    terms: list[FacetTerm] = field(default_factory=list)


@dataclass(frozen=True)
class RegisterSummary:
    """A register (``unità archivistica``) as listed in search results."""

    id: str  # ark name, e.g. "an_ua37845354"
    title: str  # e.g. "Registro: 1911"
    tipologia: str = ""
    segnatura: str = ""
    context: str = ""  # e.g. "Stato civile italiano > Acquaro"
    archive: str = ""  # conservato da

    @property
    def url(self) -> str:
        return f"{ARK_PREFIX}{self.id}/"

    @property
    def year(self) -> str:
        return self.title.split(":", 1)[-1].strip() if ":" in self.title else self.title


@dataclass
class SearchQuery:
    """Parameters of ``/search-registry/``.

    At least one of ``archivio``, ``serie``, ``localita``, ``tipologia``, ``anno`` must be set,
    otherwise the portal returns no results. ``facets`` are raw facet terms
    (see :attr:`FacetTerm.query`) that further narrow the results.
    """

    archivio: int | None = None
    serie: int | None = None
    localita: str | None = None
    tipologia: str | None = None
    anno: str | None = None
    facets: tuple[str, ...] = ()
    sort: str | None = None  # "", "estremoRemoto_i desc", "estremoRemoto_i asc"
    nominativi: bool = False  # only registers with indexed names

    def is_empty(self) -> bool:
        return not any([self.archivio, self.serie, self.localita, self.tipologia, self.anno])

    def refine(self, term: FacetTerm | str) -> SearchQuery:
        """Return a copy of the query narrowed by one more facet term."""
        raw = term.query if isinstance(term, FacetTerm) else term
        if raw in self.facets:
            return self
        return SearchQuery(
            archivio=self.archivio,
            serie=self.serie,
            localita=self.localita,
            tipologia=self.tipologia,
            anno=self.anno,
            facets=(*self.facets, raw),
            sort=self.sort,
            nominativi=self.nominativi,
        )

    def to_params(self, page: int = 1, size: int = 100) -> dict[str, str]:
        params: dict[str, str] = {}
        if self.archivio is not None:
            params["archivio"] = str(self.archivio)
        if self.serie is not None:
            params["serie"] = str(self.serie)
        if self.localita:
            params["localita"] = self.localita
        if self.tipologia:
            params["tipologia"] = self.tipologia
        if self.anno:
            params["anno"] = str(self.anno)
        if self.facets:
            params["s_facet_query"] = FACET_SEPARATOR.join(self.facets)
        if self.sort:
            params["s_sort"] = self.sort
        if self.nominativi:
            params["nominativi"] = "true"
        params["s_page"] = str(page)
        params["s_size"] = str(size)
        return params


@dataclass
class SearchPage:
    """One page of search results plus the facets available to refine it."""

    query: SearchQuery
    total: int
    page: int
    size: int
    registers: list[RegisterSummary] = field(default_factory=list)
    facets: dict[str, Facet] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        return max(1, -(-self.total // self.size)) if self.size else 1


@dataclass(frozen=True)
class Page:
    """One scanned image of a register (an IIIF canvas)."""

    index: int  # 1-based position in the register
    label: str  # e.g. "pag. 1"
    image_id: str  # IIIF identifier, e.g. "LzblN79"
    service_url: str  # IIIF Image API base, e.g. https://iiif-antenati.cultura.gov.it/iiif/2/LzblN79

    def image_url(self, size: str = "full") -> str:
        """IIIF URL for this page.

        ``size``: ``"full"`` (original resolution), ``"max"`` (server-capped ~10 MP),
        or an integer width in pixels (as int or str).

        Note: the canonical ``full/full`` form is blocked by the portal's firewall, so the
        original resolution is requested as ``pct:100`` instead.
        """
        if size in ("full", "original", None):
            spec = "pct:100"
        elif size == "max":
            spec = "max"
        else:
            spec = f"{int(size)},"
        return f"{self.service_url}/full/{spec}/0/default.jpg"

    def thumbnail_url(self, height: int = 200) -> str:
        return f"{self.service_url}/full/,{height}/0/default.jpg"


@dataclass
class Manifest:
    """A register with all its pages (parsed IIIF Presentation 2 manifest)."""

    url: str
    label: str
    metadata: dict[str, str] = field(default_factory=dict)
    pages: list[Page] = field(default_factory=list)
    register_id: str | None = None  # an_ua... when known

    @property
    def archive(self) -> str:
        return self.metadata.get("Conservato da", "")

    @property
    def context(self) -> list[str]:
        """Archival context path, e.g. ["Archivio di Stato di X", "Stato civile italiano", "Acquaro"]."""
        raw = self.metadata.get("Contesto archivistico") or self.label
        return [p.strip() for p in raw.split(">") if p.strip()]

    @property
    def tipologia(self) -> str:
        return self.metadata.get("Tipologia", "")

    @property
    def title(self) -> str:
        return self.metadata.get("Titolo", "") or self.label.split(">")[-1].strip()

    @property
    def dating(self) -> str:
        return self.metadata.get("Datazione", "")


class EventKind(str, Enum):
    REGISTER_START = "register_start"
    REGISTER_DONE = "register_done"
    REGISTER_FAILED = "register_failed"
    PAGE_DONE = "page_done"
    PAGE_SKIPPED = "page_skipped"  # already on disk
    PAGE_FAILED = "page_failed"
    FINISHED = "finished"
    CANCELLED = "cancelled"
    LOG = "log"


@dataclass
class ProgressEvent:
    """Emitted by the download engine; consumed by the CLI progress bars and the GUI."""

    kind: EventKind
    message: str = ""
    register: Manifest | None = None
    register_index: int = 0  # 1-based index of the register in the job
    register_total: int = 0  # total registers in the job (0 if still unknown)
    page: Page | None = None
    pages_done: int = 0  # pages finished (done+skipped+failed) in the current register
    pages_total: int = 0  # pages to process in the current register
    bytes: int = 0
    path: str | None = None
    error: str | None = None


@dataclass
class DownloadReport:
    registers_done: int = 0
    registers_failed: int = 0
    pages_downloaded: int = 0
    pages_skipped: int = 0
    pages_failed: int = 0
    bytes_downloaded: int = 0
    cancelled: bool = False
    errors: list[str] = field(default_factory=list)
    output_dirs: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.cancelled and self.pages_failed == 0 and self.registers_failed == 0
