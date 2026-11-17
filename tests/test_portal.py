"""Portal client with mocked HTTP (``responses``)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
import responses

from antenati_dl.http import BlockedError, PortalError
from antenati_dl.models import Page, RegisterSummary, SearchQuery
from antenati_dl.portal import ARCHIVE_URL, EXPLORE_URL, MAX_PAGE_SIZE, SEARCH_URL
from conftest import (
    CLOUDFLARE_PAGE,
    DAM,
    HTML_TYPE,
    IIIF,
    JPEG,
    PORTAL,
    load_json,
    load_text,
    manifest_json,
    search_html,
)

REGISTER_ID = "an_ua37845354"
ARK_URL = f"{PORTAL}/ark:/12657/{REGISTER_ID}/"
MANIFEST_URL = f"{DAM}/5dqWBYx/manifest"


def query_of(call) -> dict[str, str]:
    """Query string of a recorded call, decoded once (as the portal's PHP would see it)."""
    return {k: v[0] for k, v in parse_qs(urlparse(call.request.url).query).items()}


def add_html(url: str, body: str, status: int = 200) -> None:
    responses.get(url, body=body, status=status, content_type=HTML_TYPE)


# --------------------------------------------------------------------------- archives
@responses.activate
def test_archives_parsed_and_cached(portal):
    add_html(EXPLORE_URL, load_text("explore_archives.html"))
    first = portal.archives()
    second = portal.archives()
    assert len(first) == 98
    assert first == second
    assert first is not second  # callers get a copy
    assert len(responses.calls) == 1
    portal.archives(refresh=True)
    assert len(responses.calls) == 2


@responses.activate
def test_archives_cache_not_mutable_from_outside(portal):
    add_html(EXPLORE_URL, load_text("explore_archives.html"))
    portal.archives().clear()
    assert len(portal.archives()) == 98


@responses.activate
def test_archives_empty_page_raises(portal):
    add_html(EXPLORE_URL, "<html>maintenance</html>")
    with pytest.raises(PortalError, match="No archives"):
        portal.archives()


@responses.activate
def test_archives_blocked_by_cloudflare(portal):
    responses.get(EXPLORE_URL, body=CLOUDFLARE_PAGE, status=403)
    with pytest.raises(BlockedError):
        portal.archives()


@pytest.fixture
def explore_mock():
    with responses.RequestsMock() as rsps:
        rsps.get(EXPLORE_URL, body=load_text("explore_archives.html"), content_type=HTML_TYPE)
        yield rsps


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("145", "Archivio di Stato di Vibo Valentia"),
        (145, "Archivio di Stato di Vibo Valentia"),
        ("vibo valentia", "Archivio di Stato di Vibo Valentia"),
        ("  VIBO  ", "Archivio di Stato di Vibo Valentia"),
        ("Bologna", "Archivio di Stato di Bologna"),  # name ending wins over "Bologna, Sezione di Imola"
        ("Imola", "Archivio di Stato di Bologna, Sezione di Imola"),
        ("Enna", "Archivio di Stato di Enna"),  # not "Ravenna"
        ("Imperia", "Archivio di Stato di Imperia"),
    ],
)
def test_get_archive(explore_mock, portal, text, expected):
    assert portal.get_archive(text).name == expected


@pytest.mark.parametrize("text", ["di Stato", "Urbino", "Sezione"])
def test_get_archive_ambiguous(explore_mock, portal, text):
    with pytest.raises(PortalError, match="Ambiguous"):
        portal.get_archive(text)


@pytest.mark.parametrize("text", ["Atlantide", "999999"])
def test_get_archive_not_found(explore_mock, portal, text):
    with pytest.raises(PortalError, match="No archive matches"):
        portal.get_archive(text)


def test_find_archives_multiple(explore_mock, portal):
    names = [a.name for a in portal.find_archives("Urbino")]
    assert names == [
        "Archivio di Stato di Pesaro Urbino",
        "Archivio di Stato di Pesaro Urbino, Sezione di Urbino",
    ]
    assert len(portal.find_archives("di Stato")) == 98
    assert len(explore_mock.calls) == 1  # archive list fetched once


# --------------------------------------------------------------------------- fondi
@responses.activate
def test_fondi_fetched_with_param_and_cached(portal):
    add_html(ARCHIVE_URL, load_text("archive_145.html"))
    fondi = portal.fondi(145)
    assert [f.name for f in fondi] == ["Stato civile italiano"]
    assert len(fondi[0].series) == 60
    assert portal.fondi(145) == fondi
    assert len(responses.calls) == 1
    assert query_of(responses.calls[0]) == {"archivio": "145"}


# --------------------------------------------------------------------------- search
@responses.activate
def test_search_fixture(portal):
    add_html(SEARCH_URL, load_text("search_serie.html"))
    page = portal.search(SearchQuery(serie=37842016), size=20)
    assert page.total == 133
    assert len(page.registers) == 20
    assert "tipologia_ss" in page.facets
    assert query_of(responses.calls[0]) == {"serie": "37842016", "s_page": "1", "s_size": "20"}


@responses.activate
def test_search_facets_joined_with_literal_encoded_separator(portal):
    add_html(SEARCH_URL, load_text("search_serie.html"))
    query = (
        SearchQuery(serie=37842016).refine('tipologia_ss:"Matrimoni, pubblicazioni"').refine("anni_is:1911")
    )
    portal.search(query, page=2, size=20)
    url = responses.calls[0].request.url
    # the separator travels as %252C (a literal "%2C"), the comma inside the value as %2C
    assert "%252C" in url
    assert "tipologia_ss%3A%22Matrimoni%2C+pubblicazioni%22%252Canni_is%3A1911" in url
    params = query_of(responses.calls[0])
    assert params["s_facet_query"] == 'tipologia_ss:"Matrimoni, pubblicazioni"%2Canni_is:1911'
    assert params["s_page"] == "2"


@responses.activate
def test_search_all_params(portal):
    add_html(SEARCH_URL, load_text("search_serie.html"))
    query = SearchQuery(
        archivio=145, localita="Acquaro", tipologia="Nati", sort="estremoRemoto_i asc", nominativi=True
    )
    portal.search(query)
    assert query_of(responses.calls[0]) == {
        "archivio": "145",
        "localita": "Acquaro",
        "tipologia": "Nati",
        "s_sort": "estremoRemoto_i asc",
        "nominativi": "true",
        "s_page": "1",
        "s_size": "100",
    }


@pytest.mark.parametrize(
    ("size", "sent"), [(1, "2"), (0, "2"), (-5, "2"), (2, "2"), (50, "50"), (5000, "1000")]
)
@responses.activate
def test_search_size_clamped(portal, size, sent):
    add_html(SEARCH_URL, load_text("search_serie.html"))
    result = portal.search(SearchQuery(serie=37842016), size=size)
    assert query_of(responses.calls[0])["s_size"] == sent
    assert result.size == int(sent)


@responses.activate
def test_count_uses_size_2(portal):
    add_html(SEARCH_URL, load_text("search_serie.html"))
    assert portal.count(SearchQuery(serie=37842016)) == 133
    assert query_of(responses.calls[0])["s_size"] == "2"


@responses.activate
def test_search_empty_query_rejected(portal):
    with pytest.raises(ValueError):
        portal.search(SearchQuery(facets=("tipologia_ss:Nati",)))
    assert len(responses.calls) == 0


@responses.activate
def test_search_no_results(portal):
    add_html(SEARCH_URL, load_text("search_empty.html"))
    page = portal.search(SearchQuery(serie=1))
    assert page.total == 0
    assert page.registers == []
    assert len(responses.calls) == 1  # no year constraint -> no fallback


@responses.activate
def test_search_blocked(portal):
    responses.get(SEARCH_URL, body=CLOUDFLARE_PAGE, status=403)
    with pytest.raises(BlockedError):
        portal.search(SearchQuery(serie=1))


# --------------------------------------------------------------------------- tipologia + year fallback
@responses.activate
def test_search_tipologia_year_fallback_filters_titles(portal):
    add_html(SEARCH_URL, load_text("search_empty.html"))  # the portal bug: 0 results
    add_html(SEARCH_URL, load_text("search_serie.html"))  # same query without the year
    query = SearchQuery(serie=37842016, facets=("tipologia_ss:Morti", "anni_is:1912"))
    page = portal.search(query, size=20)

    assert len(responses.calls) == 2
    first, second = (query_of(c) for c in responses.calls)
    assert first["s_facet_query"] == "tipologia_ss:Morti%2Canni_is:1912"
    assert second["s_facet_query"] == "tipologia_ss:Morti"  # year dropped server side
    assert second["s_size"] == str(MAX_PAGE_SIZE)
    assert second["s_page"] == "1"

    assert page.query is query
    assert page.total == 4  # the fixture has 4 registers titled "Registro: 1912"
    assert {r.title for r in page.registers} == {"Registro: 1912"}
    assert [t.value for t in page.facets["anni_is"].terms] == ["1912"]
    assert "tipologia_ss" in page.facets


@responses.activate
def test_search_fallback_with_anno_param_and_paging(portal):
    add_html(SEARCH_URL, load_text("search_empty.html"))
    add_html(SEARCH_URL, load_text("search_serie.html"))
    query = SearchQuery(serie=37842016, tipologia="Morti", anno="1911-1913")
    page = portal.search(query, page=2, size=5)
    second = query_of(responses.calls[1])
    assert "anno" not in second
    assert second["tipologia"] == "Morti"
    assert page.total == 12  # 4 registers for each of 1911, 1912, 1913
    assert page.page_count == 3
    assert len(page.registers) == 5
    assert page.registers[0].title == "Registro: 1912"  # items 6-10 of the filtered list


@responses.activate
def test_search_fallback_fetches_every_page(portal):
    regs_p1 = [(i, f"Registro: {1800 + i % 50}", "Nati") for i in range(1, 6)]
    regs_p2 = [(i, f"Registro: {1800 + i % 50}", "Nati") for i in range(51, 53)]
    add_html(SEARCH_URL, load_text("search_empty.html"))
    add_html(SEARCH_URL, search_html(regs_p1, total=MAX_PAGE_SIZE + 2))
    add_html(SEARCH_URL, search_html(regs_p2, total=MAX_PAGE_SIZE + 2))
    query = SearchQuery(localita="Paese", tipologia="Nati", anno="1801,1802")
    page = portal.search(query)
    assert [query_of(c)["s_page"] for c in responses.calls] == ["1", "1", "2"]
    assert [r.id for r in page.registers] == ["an_ua1", "an_ua2", "an_ua51", "an_ua52"]
    assert page.total == 4


@pytest.mark.parametrize(
    "query",
    [
        SearchQuery(archivio=145, tipologia="Nati", anno="1911"),  # no other filter
        SearchQuery(serie=1, anno="1911"),  # no tipologia
        SearchQuery(serie=1, tipologia="Nati"),  # no year
    ],
)
@responses.activate
def test_search_no_fallback_when_bug_not_triggered(portal, query):
    add_html(SEARCH_URL, load_text("search_empty.html"))
    assert portal.search(query).total == 0
    assert len(responses.calls) == 1


@responses.activate
def test_search_no_fallback_when_results_found(portal):
    add_html(SEARCH_URL, load_text("search_serie.html"))
    portal.search(SearchQuery(serie=37842016, tipologia="Morti", anno="1911"))
    assert len(responses.calls) == 1


# --------------------------------------------------------------------------- iter_registers
def _paged_search(total: int, size: int) -> list[list[tuple[int, str, str]]]:
    regs = [(i, f"Registro: {1850 + i}", "Nati") for i in range(1, total + 1)]
    return [regs[i : i + size] for i in range(0, total, size)]


@responses.activate
def test_iter_registers_paginates(portal):
    for chunk in _paged_search(5, 2):
        add_html(SEARCH_URL, search_html(chunk, total=5))
    ids = [r.id for r in portal.iter_registers(SearchQuery(serie=1), size=2)]
    assert ids == [f"an_ua{i}" for i in range(1, 6)]
    assert [query_of(c)["s_page"] for c in responses.calls] == ["1", "2", "3"]
    assert {query_of(c)["s_size"] for c in responses.calls} == {"2"}


@responses.activate
def test_iter_registers_limit_stops_early(portal):
    for chunk in _paged_search(6, 2):
        add_html(SEARCH_URL, search_html(chunk, total=6))
    ids = [r.id for r in portal.iter_registers(SearchQuery(serie=1), limit=3, size=2)]
    assert ids == ["an_ua1", "an_ua2", "an_ua3"]
    assert len(responses.calls) == 2


@responses.activate
def test_iter_registers_limit_zero_yields_nothing(portal):
    add_html(SEARCH_URL, search_html([(1, "Registro: 1900", "Nati")]))
    assert list(portal.iter_registers(SearchQuery(serie=1), limit=0)) == []


@responses.activate
def test_iter_registers_default_page_size(portal):
    add_html(SEARCH_URL, search_html([(1, "Registro: 1900", "Nati")]))
    assert len(list(portal.iter_registers(SearchQuery(serie=1)))) == 1
    assert query_of(responses.calls[0])["s_size"] == str(MAX_PAGE_SIZE)


@responses.activate
def test_iter_registers_stops_on_repeated_page(portal):
    # the portal claims more results but keeps returning the same page
    page = search_html([(1, "Registro: 1900", "Nati"), (2, "Registro: 1901", "Nati")], total=10)
    for _ in range(5):
        add_html(SEARCH_URL, page)
    ids = [r.id for r in portal.iter_registers(SearchQuery(serie=1), size=2)]
    assert ids == ["an_ua1", "an_ua2"]
    assert len(responses.calls) == 2


@responses.activate
def test_iter_registers_empty(portal):
    add_html(SEARCH_URL, load_text("search_empty.html"))
    assert list(portal.iter_registers(SearchQuery(serie=1))) == []


# --------------------------------------------------------------------------- manifests
def add_register_mocks() -> None:
    add_html(ARK_URL, load_text("register.html"))
    responses.get(MANIFEST_URL, json=load_json("manifest.json"))


@responses.activate
def test_manifest_url_cached(portal):
    add_html(ARK_URL, load_text("register.html"))
    assert portal.manifest_url(REGISTER_ID) == MANIFEST_URL
    assert portal.manifest_url(REGISTER_ID) == MANIFEST_URL
    assert len(responses.calls) == 1


@responses.activate
def test_manifest_url_missing_in_page(portal):
    add_html(ARK_URL, "<html>no viewer</html>")
    with pytest.raises(PortalError, match="Manifest URL not found"):
        portal.manifest_url(REGISTER_ID)


@pytest.mark.parametrize(
    "ref",
    [
        REGISTER_ID,
        "37845354",
        ARK_URL,
        f"{ARK_URL}LzblN79",
        RegisterSummary(REGISTER_ID, "Registro: 1911"),
    ],
)
@responses.activate
def test_manifest_from_register_refs(portal, ref):
    add_register_mocks()
    m = portal.manifest(ref)
    assert m.register_id == REGISTER_ID
    assert m.url == MANIFEST_URL
    assert len(m.pages) == 55
    assert [c.request.url for c in responses.calls] == [ARK_URL, MANIFEST_URL]
    assert responses.calls[1].request.headers["Referer"] == f"{PORTAL}/"
    # second load is served from the cache
    assert portal.manifest(ref) is m
    assert len(responses.calls) == 2


@responses.activate
def test_manifest_from_manifest_url_skips_ark_page(portal):
    add_register_mocks()
    m = portal.manifest(MANIFEST_URL)
    assert m.register_id == REGISTER_ID
    assert [c.request.url for c in responses.calls] == [MANIFEST_URL]
    # the register id -> manifest URL mapping is remembered too
    assert portal.manifest(REGISTER_ID) is m
    assert len(responses.calls) == 1


@responses.activate
def test_manifest_without_register_id_uses_ref(portal):
    data = manifest_json("an_ua77", pages=2)
    for canvas in data["sequences"][0]["canvases"]:
        canvas["@id"] = "https://dam/canvas"
    url = data["@id"]
    add_html(f"{PORTAL}/ark:/12657/an_ua77/", f"<script>manifestId: '{url}'</script>")
    responses.get(url, json=data)
    assert portal.manifest("an_ua77").register_id == "an_ua77"


@responses.activate
def test_manifest_invalid_ref(portal):
    with pytest.raises(ValueError):
        portal.manifest("not a register")
    assert len(responses.calls) == 0


@responses.activate
def test_manifest_blocked(portal):
    responses.get(ARK_URL, body=CLOUDFLARE_PAGE, status=403)
    with pytest.raises(BlockedError):
        portal.manifest(REGISTER_ID)


@responses.activate
def test_image_info(portal):
    page = Page(1, "pag. 1", "LzblN79", f"{IIIF}/LzblN79")
    responses.get(f"{IIIF}/LzblN79/info.json", json=load_json("info.json"))
    info = portal.image_info(page)
    assert (info["width"], info["height"]) == (6303, 3826)


@responses.activate
def test_thumbnail(portal):
    page = Page(1, "pag. 1", "LzblN79", f"{IIIF}/LzblN79")
    responses.get(f"{IIIF}/LzblN79/full/,150/0/default.jpg", body=JPEG)
    assert portal.thumbnail(page, height=150) == JPEG
