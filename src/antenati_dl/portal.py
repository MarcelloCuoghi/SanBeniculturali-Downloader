"""Client for the Portale Antenati (https://antenati.cultura.gov.it).

Hierarchy exposed by the portal and how it is retrieved::

    Archivio di Stato        /esplora-gli-archivi/            (list, ids parsed from map popups)
      └─ Fondo               /archive/?archivio=<id>          ("Esplora i fondi")
          └─ Serie (comune)  same page, links /search-registry/?serie=<id>
              └─ Tipologia   facet ``tipologia_ss`` of /search-registry/?serie=<id>
                  └─ Anno    facet ``anni_is``
                      └─ Registro   search result -> /ark:/12657/an_ua<id>/  (page embeds the manifest URL)
                          └─ Pagine IIIF manifest on dam-antenati -> images on iiif-antenati

The parsing functions (``parse_*``) are pure and operate on HTML/JSON strings so they can be tested
offline against fixtures; :class:`Portal` adds HTTP and caching on top of them.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import threading
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

from lxml import html as lxml_html

from .http import HttpClient, PortalError
from .models import (
    ARK_PREFIX,
    FACET_FIELDS,
    PORTAL_URL,
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

EXPLORE_URL = f"{PORTAL_URL}/esplora-gli-archivi/"
ARCHIVE_URL = f"{PORTAL_URL}/archive/"
SEARCH_URL = f"{PORTAL_URL}/search-registry/"
MANIFEST_RE = re.compile(r"""manifestId\s*:\s*['"]([^'"]+/manifest)['"]""")
REGISTER_ID_RE = re.compile(r"\b(an_ua\d+)\b")
MAX_PAGE_SIZE = 1000


# --------------------------------------------------------------------------- helpers
def _text(node) -> str:
    return re.sub(r"\s+", " ", node.text_content()).strip() if node is not None else ""


def _strip_tags(value: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", str(value)))).strip()


def _unquote_term_value(raw: str) -> str:
    value = raw.split(":", 1)[1] if ":" in raw else raw
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    return html_lib.unescape(value)


def parse_register_ref(ref: str) -> tuple[str | None, str | None, str | None]:
    """Parse whatever the user pasted into ``(register_id, page_image_id, manifest_url)``.

    Accepted forms::

        an_ua37845354
        37845354
        https://antenati.cultura.gov.it/ark:/12657/an_ua37845354/
        https://antenati.cultura.gov.it/ark:/12657/an_ua37845354/LzblN79      (a single page)
        https://dam-antenati.cultura.gov.it/antenati/containers/5dqWBYx/manifest
    """
    ref = ref.strip()
    if ref.endswith("/manifest") or "/containers/" in ref:
        url = ref if ref.endswith("/manifest") else ref.rstrip("/") + "/manifest"
        return None, None, url
    if ref.isdigit():
        return f"an_ua{ref}", None, None
    m = re.search(r"(an_ua\d+)(?:/([A-Za-z0-9]+))?/?(?:[?#].*)?$", ref)
    if m:
        return m.group(1), m.group(2), None
    m = REGISTER_ID_RE.search(ref)
    if m:
        return m.group(1), None, None
    raise ValueError(f"Not a register reference: {ref!r}")


_YEAR_RE = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")


def _parse_year_spec(spec: str) -> set[int]:
    """``"1911"``, ``"1860-1865"``, ``"1860,1870"`` -> set of years."""
    years: set[int] = set()
    for part in re.split(r"[,;\s]+", spec.strip()):
        found = [int(y) for y in _YEAR_RE.findall(part)]
        if len(found) >= 2 and "-" in part:
            lo, hi = min(found[0], found[1]), max(found[0], found[1])
            years.update(range(lo, hi + 1))
        else:
            years.update(found)
    return years


def register_years(title: str) -> set[int]:
    """Years covered by a register title like ``"Registro: 1852-1857"`` or ``"Registro: 1911"``."""
    return _parse_year_spec(title.split(":", 1)[-1])


def requested_years(query: SearchQuery) -> set[int] | None:
    """Years selected by ``query`` (``anno`` param and ``anni_is`` facets), or None if unconstrained."""
    sets: list[set[int]] = []
    if query.anno:
        sets.append(_parse_year_spec(str(query.anno)))
    for f in query.facets:
        if f.startswith("anni_is:"):
            sets.append(_parse_year_spec(_unquote_term_value(f)))
    if not sets:
        return None
    result = sets[0]
    for s in sets[1:]:
        result &= s
    return result


def _has_tipologia(query: SearchQuery) -> bool:
    return bool(query.tipologia) or any(f.startswith("tipologia_ss:") for f in query.facets)


def _has_other_filter(query: SearchQuery) -> bool:
    """True if the query is narrowed by something besides archive, tipologia and years."""
    return bool(query.serie or query.localita) or any(
        not f.startswith(("tipologia_ss:", "anni_is:", "conservatore_s:")) for f in query.facets
    )


def without_years(query: SearchQuery) -> SearchQuery:
    return SearchQuery(
        archivio=query.archivio,
        serie=query.serie,
        localita=query.localita,
        tipologia=query.tipologia,
        anno=None,
        facets=tuple(f for f in query.facets if not f.startswith("anni_is:")),
        sort=query.sort,
        nominativi=query.nominativi,
    )


# --------------------------------------------------------------------------- parsers
def parse_archives(page_html: str) -> list[Archive]:
    """Parse the list of archives from ``/esplora-gli-archivi/``."""
    found: dict[int, Archive] = {}
    # Map popups: <h3>Name</h3> <a href='.../archivio/slug/'>Scopri</a><a href='/archive/?archivio=ID
    popup_re = re.compile(
        r"<h3>([^<]+)</h3>\s*<a href='([^']+)'>[^<]*</a>\s*<a href='/archive/\?archivio=(\d+)",
    )
    for name, url, aid in popup_re.findall(page_html):
        found[int(aid)] = Archive(int(aid), html_lib.unescape(name).strip(), url)
    # Fallback / complement: "Cerca nei registri" links carry id + description
    link_re = re.compile(r"search-registry/\?archivio=(\d+)&(?:#038;|amp;)?descrizione=([^&'\"]+)")
    for aid, name in link_re.findall(page_html):
        if int(aid) not in found:
            found[int(aid)] = Archive(int(aid), html_lib.unescape(name).strip())
    return sorted(found.values(), key=lambda a: a.name.lower())


def parse_fondi(page_html: str, archive_id: int) -> list[Fondo]:
    """Parse fondi and their series from ``/archive/?archivio=<id>``."""
    doc = lxml_html.fromstring(page_html)
    names: dict[int, str] = {}
    for node in doc.xpath("//*[@id='archive_property']//*[@data-property]"):
        h3 = node.find(".//h3")
        names[int(node.get("data-property"))] = _text(h3) if h3 is not None else _text(node)
    fondi: list[Fondo] = []
    seen: set[int] = set()
    toponym_xpath = "//div[contains(concat(' ', normalize-space(@class), ' '), ' property-toponym ')]"
    for block in doc.xpath(toponym_xpath):
        fid = int(block.get("data-id"))
        series: list[Serie] = []
        for a in block.xpath(".//a[contains(@href, 'serie=')]"):
            qs = parse_qs(urlparse(a.get("href")).query)
            try:
                sid = int(qs["serie"][0])
            except (KeyError, ValueError):
                continue
            label = _text(a) or (qs.get("descrizione") or [""])[0]
            series.append(Serie(sid, label))
        fondi.append(Fondo(fid, names.get(fid, str(fid)), archive_id, tuple(series)))
        seen.add(fid)
    for fid, name in names.items():  # fondi without a series block
        if fid not in seen:
            fondi.append(Fondo(fid, name, archive_id, ()))
    return fondi


def parse_search(page_html: str, query: SearchQuery, page: int = 1, size: int = 100) -> SearchPage:
    """Parse a ``/search-registry/`` result page (registers + facets)."""
    doc = lxml_html.fromstring(page_html)
    total = 0
    strong = doc.xpath("//*[contains(@class,'search-query')]/strong")
    if strong:
        digits = re.sub(r"\D", "", _text(strong[0]))
        total = int(digits) if digits else 0

    registers: list[RegisterSummary] = []
    for li in doc.xpath("//ul[@id='search_list']/li[contains(@class,'search-item')]"):
        link = li.xpath(".//h3//a[@href]")
        href = link[0].get("href") if link else ""
        m = REGISTER_ID_RE.search(href)
        data_id = li.get("data-id", "")
        if m:
            rid = m.group(1)
        elif data_id.isdigit():
            rid = f"an_ua{data_id}"
        else:
            continue
        title = _text(link[0]) if link else rid
        tipologia = segnatura = context = archive = ""
        for p in li.xpath("./div/p"):
            txt = _text(p)
            if not txt:
                continue
            if txt.startswith("Segnatura"):
                segnatura = txt.split(":", 1)[-1].strip()
            elif txt.startswith("Conservato da"):
                archive = txt.split(":", 1)[-1].strip()
            elif ">" in txt and not context:
                context = re.sub(r"\s*>\s*", " > ", txt.replace("\xa0", " "))
            elif not tipologia and p.find("strong") is not None:
                tipologia = txt
        registers.append(RegisterSummary(rid, title, tipologia, segnatura, context, archive))

    facets: dict[str, Facet] = {}
    facet_xpath = "//aside[@id='facets']//li[contains(@class,'facet') and not(contains(@class,'facet-term'))]"
    for li in doc.xpath(facet_xpath):
        name_node = li.xpath("./h4[contains(@class,'facet-name')]")
        if not name_node:
            continue
        terms: dict[str, FacetTerm] = {}
        for a in li.xpath(".//a[@data-facet-term]"):
            raw = a.get("data-facet-term")
            if raw in terms or ":" not in raw:
                continue
            field = raw.split(":", 1)[0]
            count = 0
            small = a.find("small")
            spans = a.findall("span")
            if small is not None:
                count = int(re.sub(r"\D", "", _text(small)) or 0)
            elif spans and re.fullmatch(r"\d+", _text(spans[0])):
                count = int(_text(spans[0]))
            terms[raw] = FacetTerm(field, _unquote_term_value(raw), count, raw)
        if not terms:
            continue
        field = next(iter(terms.values())).field
        label = FACET_FIELDS.get(field, _text(name_node[0]))
        term_list = list(terms.values())
        if field == "anni_is":
            term_list.sort(key=lambda t: (int(t.value) if t.value.isdigit() else 0, t.value))
        else:
            term_list.sort(key=lambda t: t.value.lower())
        facets[field] = Facet(field, label, term_list)
    return SearchPage(query=query, total=total, page=page, size=size, registers=registers, facets=facets)


def parse_manifest_url(page_html: str) -> str:
    m = MANIFEST_RE.search(page_html)
    if not m:
        raise PortalError("Manifest URL not found in register page")
    return m.group(1)


def parse_manifest(data: dict | str, url: str | None = None) -> Manifest:
    """Parse an IIIF Presentation 2.x manifest into a :class:`Manifest`."""
    if isinstance(data, str):
        data = json.loads(data)
    metadata: dict[str, str] = {}
    for item in data.get("metadata") or []:
        label = _strip_tags(item.get("label", ""))
        value = item.get("value", "")
        if isinstance(value, list):  # IIIF allows language maps
            value = "; ".join(v.get("@value", "") if isinstance(v, dict) else str(v) for v in value)
        metadata[label] = _strip_tags(value)
    pages: list[Page] = []
    sequences = data.get("sequences") or []
    canvases = sequences[0].get("canvases", []) if sequences else []
    register_id = None
    for idx, canvas in enumerate(canvases, start=1):
        images = canvas.get("images") or []
        if not images:
            continue
        resource = images[0].get("resource", {})
        service = resource.get("service") or {}
        if isinstance(service, list):
            service = service[0] if service else {}
        service_url = (service.get("@id") or "").rstrip("/")
        if not service_url:
            rid = resource.get("@id", "")
            service_url = rid.split("/full/")[0] if "/full/" in rid else rid
        image_id = service_url.rsplit("/", 1)[-1]
        pages.append(Page(idx, _strip_tags(canvas.get("label", f"{idx}")), image_id, service_url))
        if register_id is None:
            m = REGISTER_ID_RE.search(canvas.get("@id", ""))
            register_id = m.group(1) if m else None
    if register_id is None:
        m = REGISTER_ID_RE.search(metadata.get("Vedi il registro", ""))
        register_id = m.group(1) if m else None
    label = data.get("label", "")
    if isinstance(label, list):
        label = "; ".join(str(v.get("@value", v)) if isinstance(v, dict) else str(v) for v in label)
    return Manifest(
        url=url or data.get("@id", ""),
        label=_strip_tags(label),
        metadata=metadata,
        pages=pages,
        register_id=register_id,
    )


# --------------------------------------------------------------------------- client
class Portal:
    """High level, cached access to the portal. Safe to share between threads."""

    def __init__(self, client: HttpClient | None = None) -> None:
        self.http = client or HttpClient()
        self._lock = threading.Lock()
        self._archives: list[Archive] | None = None
        self._fondi: dict[int, list[Fondo]] = {}
        self._manifest_urls: dict[str, str] = {}
        self._manifests: dict[str, Manifest] = {}

    # ---------------------------------------------------------------- archives
    def archives(self, refresh: bool = False) -> list[Archive]:
        with self._lock:
            if self._archives is not None and not refresh:
                return list(self._archives)
        archives = parse_archives(self.http.get_text(EXPLORE_URL))
        if not archives:
            raise PortalError("No archives found on the portal (page layout changed?)")
        with self._lock:
            self._archives = archives
        return list(archives)

    def find_archives(self, text: str) -> list[Archive]:
        """Archives whose id equals ``text`` or whose name contains it (case-insensitive)."""
        text = text.strip()
        archives = self.archives()
        if text.isdigit():
            exact = [a for a in archives if a.id == int(text)]
            if exact:
                return exact
        needle = text.lower()
        matches = [a for a in archives if needle in a.name.lower()]
        exact_tail = [a for a in matches if a.name.lower().endswith(" " + needle)]
        return exact_tail or matches

    def get_archive(self, text_or_id: str | int) -> Archive:
        matches = self.find_archives(str(text_or_id))
        if not matches:
            raise PortalError(f"No archive matches {text_or_id!r}")
        if len(matches) > 1:
            names = ", ".join(a.name for a in matches[:8])
            raise PortalError(f"Ambiguous archive {text_or_id!r}: {names}")
        return matches[0]

    def fondi(self, archive_id: int) -> list[Fondo]:
        with self._lock:
            if archive_id in self._fondi:
                return self._fondi[archive_id]
        fondi = parse_fondi(self.http.get_text(ARCHIVE_URL, params={"archivio": archive_id}), archive_id)
        with self._lock:
            self._fondi[archive_id] = fondi
        return fondi

    # ---------------------------------------------------------------- search
    def search(self, query: SearchQuery, page: int = 1, size: int = 100) -> SearchPage:
        """One page of results.

        Works around a portal bug: a query combining a *tipologia* and a *year* constraint with
        any other filter (serie, località, ...) always returns 0 results. In that case the year
        constraint is dropped server-side and applied here, on the register titles.
        """
        if query.is_empty():
            raise ValueError("Search query needs at least archivio, serie, localita, tipologia or anno")
        # the portal answers 0 results to s_size=1
        size = max(2, min(size, MAX_PAGE_SIZE))
        result = self._search_raw(query, page, size)
        if result.total == 0:
            years = requested_years(query)
            if years and _has_tipologia(query) and _has_other_filter(query):
                return self._search_filtering_years(query, years, page, size)
        return result

    def _search_raw(self, query: SearchQuery, page: int, size: int) -> SearchPage:
        text = self.http.get_text(SEARCH_URL, params=query.to_params(page=page, size=size))
        return parse_search(text, query, page=page, size=size)

    def _search_filtering_years(
        self, query: SearchQuery, years: set[int], page: int, size: int
    ) -> SearchPage:
        base = without_years(query)
        first = self._search_raw(base, 1, MAX_PAGE_SIZE)
        registers = list(first.registers)
        for extra in range(2, first.page_count + 1):
            registers.extend(self._search_raw(base, extra, MAX_PAGE_SIZE).registers)
        matching = [r for r in registers if register_years(r.title) & years]
        facets = dict(first.facets)
        if "anni_is" in facets:  # keep only the selected years in the year facet
            facets["anni_is"] = Facet(
                "anni_is",
                facets["anni_is"].label,
                [t for t in facets["anni_is"].terms if t.value.isdigit() and int(t.value) in years],
            )
        start = (page - 1) * size
        return SearchPage(
            query=query,
            total=len(matching),
            page=page,
            size=size,
            registers=matching[start : start + size],
            facets=facets,
        )

    def iter_registers(
        self, query: SearchQuery, limit: int | None = None, size: int = MAX_PAGE_SIZE
    ) -> Iterator[RegisterSummary]:
        """Yield every register matching ``query`` (handles pagination)."""
        page, yielded = 1, 0
        seen: set[str] = set()
        while True:
            result = self.search(query, page=page, size=size)
            new = [r for r in result.registers if r.id not in seen]
            for reg in new:
                seen.add(reg.id)
                yield reg
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            if not new or page >= result.page_count:
                return
            page += 1

    def count(self, query: SearchQuery) -> int:
        return self.search(query, page=1, size=1).total

    # ---------------------------------------------------------------- registers
    def manifest_url(self, register_id: str) -> str:
        with self._lock:
            if register_id in self._manifest_urls:
                return self._manifest_urls[register_id]
        url = parse_manifest_url(self.http.get_text(f"{ARK_PREFIX}{register_id}/"))
        with self._lock:
            self._manifest_urls[register_id] = url
        return url

    def manifest(self, ref: str | RegisterSummary) -> Manifest:
        """Load a register's manifest from an id, ark URL, manifest URL or search result."""
        register_id = ref.id if isinstance(ref, RegisterSummary) else None
        manifest_url = None
        if register_id is None:
            register_id, _page, manifest_url = parse_register_ref(str(ref))
        if manifest_url is None:
            manifest_url = self.manifest_url(register_id)
        with self._lock:
            if manifest_url in self._manifests:
                return self._manifests[manifest_url]
        manifest = parse_manifest(self.http.get_json(manifest_url), url=manifest_url)
        if manifest.register_id is None:
            manifest.register_id = register_id
        with self._lock:
            self._manifests[manifest_url] = manifest
            if manifest.register_id:
                self._manifest_urls[manifest.register_id] = manifest_url
        return manifest

    def image_info(self, page: Page) -> dict:
        """IIIF ``info.json`` of a page (width/height of the original scan)."""
        return self.http.get_json(f"{page.service_url}/info.json")

    def thumbnail(self, page: Page, height: int = 200) -> bytes:
        return self.http.get_image(page.thumbnail_url(height))
