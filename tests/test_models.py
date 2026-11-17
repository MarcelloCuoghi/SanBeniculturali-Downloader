from __future__ import annotations

from urllib.parse import parse_qs, urlencode

import pytest

from antenati_dl.models import (
    FACET_SEPARATOR,
    Archive,
    DownloadReport,
    FacetTerm,
    Manifest,
    Page,
    RegisterSummary,
    SearchPage,
    SearchQuery,
)

SERVICE = "https://iiif-antenati.cultura.gov.it/iiif/2/LzblN79"


# --------------------------------------------------------------------------- Page URLs
@pytest.fixture
def page() -> Page:
    return Page(1, "pag. 1", "LzblN79", SERVICE)


@pytest.mark.parametrize("size", ["full", "original", None])
def test_page_image_url_original_uses_pct_100(page, size):
    # canonical full/full is blocked by the firewall
    assert page.image_url(size) == f"{SERVICE}/full/pct:100/0/default.jpg"


def test_page_image_url_default_is_original(page):
    assert page.image_url() == f"{SERVICE}/full/pct:100/0/default.jpg"
    assert "/full/full/" not in page.image_url()


def test_page_image_url_max(page):
    assert page.image_url("max") == f"{SERVICE}/full/max/0/default.jpg"


@pytest.mark.parametrize("size", [800, "800"])
def test_page_image_url_width(page, size):
    assert page.image_url(size) == f"{SERVICE}/full/800,/0/default.jpg"


def test_page_image_url_invalid_size(page):
    with pytest.raises(ValueError):
        page.image_url("huge")


def test_page_thumbnail_url(page):
    assert page.thumbnail_url() == f"{SERVICE}/full/,200/0/default.jpg"
    assert page.thumbnail_url(120) == f"{SERVICE}/full/,120/0/default.jpg"


# --------------------------------------------------------------------------- simple models
def test_archive_explore_url():
    assert Archive(145, "Archivio di Stato di Vibo Valentia").explore_url == (
        "https://antenati.cultura.gov.it/archive/?archivio=145"
    )


def test_facet_term_label():
    term = FacetTerm("tipologia_ss", "Nati", 13, "tipologia_ss:Nati")
    assert term.label == "Nati (13)"


def test_register_summary_url_and_year():
    reg = RegisterSummary("an_ua37845354", "Registro: 1911")
    assert reg.url == "https://antenati.cultura.gov.it/ark:/12657/an_ua37845354/"
    assert reg.year == "1911"
    assert RegisterSummary("an_ua1", "1852-1857").year == "1852-1857"


@pytest.mark.parametrize(
    ("total", "size", "expected"),
    [(0, 20, 1), (1, 20, 1), (20, 20, 1), (21, 20, 2), (133, 20, 7), (133, 1000, 1), (5, 0, 1)],
)
def test_search_page_page_count(total, size, expected):
    assert SearchPage(SearchQuery(serie=1), total=total, page=1, size=size).page_count == expected


def test_download_report_ok():
    assert DownloadReport().ok
    assert not DownloadReport(pages_failed=1).ok
    assert not DownloadReport(registers_failed=1).ok
    assert not DownloadReport(cancelled=True).ok


# --------------------------------------------------------------------------- SearchQuery
def test_search_query_is_empty():
    assert SearchQuery().is_empty()
    assert SearchQuery(facets=("tipologia_ss:Nati",), sort="x", nominativi=True).is_empty()
    for kwargs in ({"archivio": 145}, {"serie": 1}, {"localita": "Acquaro"}, {"tipologia": "Nati"}):
        assert not SearchQuery(**kwargs).is_empty()
    assert not SearchQuery(anno="1911").is_empty()


def test_search_query_to_params_full():
    q = SearchQuery(
        archivio=145,
        serie=37842016,
        localita="Acquaro",
        tipologia="Nati",
        anno="1911",
        facets=('tipologia_ss:"Matrimoni, pubblicazioni"', "anni_is:1911"),
        sort="estremoRemoto_i asc",
        nominativi=True,
    )
    assert q.to_params(page=3, size=50) == {
        "archivio": "145",
        "serie": "37842016",
        "localita": "Acquaro",
        "tipologia": "Nati",
        "anno": "1911",
        "s_facet_query": 'tipologia_ss:"Matrimoni, pubblicazioni"%2Canni_is:1911',
        "s_sort": "estremoRemoto_i asc",
        "nominativi": "true",
        "s_page": "3",
        "s_size": "50",
    }


def test_search_query_to_params_minimal_defaults():
    assert SearchQuery(serie=1).to_params() == {"serie": "1", "s_page": "1", "s_size": "100"}


def test_facet_separator_is_literal_and_double_encoded():
    assert FACET_SEPARATOR == "%2C"
    params = SearchQuery(serie=1, facets=("tipologia_ss:Nati", "anni_is:1911")).to_params()
    encoded = urlencode(params)
    assert "s_facet_query=tipologia_ss%3ANati%252Canni_is%3A1911" in encoded
    # decoding once gives back the literal separator, not a comma
    assert parse_qs(encoded)["s_facet_query"] == ["tipologia_ss:Nati%2Canni_is:1911"]


def test_search_query_refine_returns_copy():
    base = SearchQuery(serie=1, sort="estremoRemoto_i desc", nominativi=True)
    term = FacetTerm("tipologia_ss", "Nati", 13, "tipologia_ss:Nati")
    refined = base.refine(term)
    assert refined is not base
    assert base.facets == ()
    assert refined.facets == ("tipologia_ss:Nati",)
    assert (refined.serie, refined.sort, refined.nominativi) == (1, "estremoRemoto_i desc", True)
    twice = refined.refine("anni_is:1911")
    assert twice.facets == ("tipologia_ss:Nati", "anni_is:1911")


def test_search_query_refine_same_term_is_noop():
    q = SearchQuery(serie=1).refine("anni_is:1911")
    assert q.refine("anni_is:1911") is q


# --------------------------------------------------------------------------- Manifest properties
def test_manifest_properties_from_metadata():
    m = Manifest(
        url="u",
        label="A > B > C > 1911",
        metadata={
            "Titolo": "1911",
            "Tipologia": "Morti",
            "Datazione": "1911 - 1911",
            "Contesto archivistico": "Archivio X >  Stato civile > Paese ",
            "Conservato da": "Archivio X",
        },
    )
    assert m.context == ["Archivio X", "Stato civile", "Paese"]
    assert m.title == "1911"
    assert m.tipologia == "Morti"
    assert m.dating == "1911 - 1911"
    assert m.archive == "Archivio X"


def test_manifest_properties_fallback_to_label():
    m = Manifest(url="u", label="A > B > Registro 3")
    assert m.context == ["A", "B", "Registro 3"]
    assert m.title == "Registro 3"
    assert m.tipologia == m.dating == m.archive == ""
