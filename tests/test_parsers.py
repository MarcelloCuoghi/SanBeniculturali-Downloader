"""Pure parsing functions of :mod:`antenati_dl.portal`, run against pages saved from the portal."""

from __future__ import annotations

import json

import pytest

from antenati_dl.http import PortalError
from antenati_dl.models import FACET_FIELDS, SearchQuery
from antenati_dl.portal import (
    _has_other_filter,
    _has_tipologia,
    _parse_year_spec,
    parse_archives,
    parse_fondi,
    parse_manifest,
    parse_manifest_url,
    parse_register_ref,
    parse_search,
    register_years,
    requested_years,
    without_years,
)
from conftest import load_json, load_text, manifest_json, search_html

MANIFEST_URL = "https://dam-antenati.cultura.gov.it/antenati/containers/5dqWBYx/manifest"


# --------------------------------------------------------------------------- archives
@pytest.fixture(scope="module")
def archives():
    return parse_archives(load_text("explore_archives.html"))


def test_parse_archives_count_and_order(archives):
    assert len(archives) == 98
    names = [a.name.lower() for a in archives]
    assert names == sorted(names)
    assert len({a.id for a in archives}) == 98


def test_parse_archives_vibo_valentia(archives):
    vibo = next(a for a in archives if a.id == 145)
    assert vibo.name == "Archivio di Stato di Vibo Valentia"
    assert vibo.url == "https://antenati.cultura.gov.it/archivio/archivio-di-stato-di-vibo-valentia/"
    assert vibo.explore_url.endswith("/archive/?archivio=145")


def test_parse_archives_unescapes_entities(archives):
    assert all("&#" not in a.name and "&amp;" not in a.name for a in archives)
    assert any("Aquila" in a.name for a in archives)


def test_parse_archives_link_fallback():
    page = (
        "<a href='/search-registry/?archivio=7&#038;descrizione=Archivio di Stato di Foo'>x</a>"
        "<a href='/search-registry/?archivio=8&amp;descrizione=Archivio di Stato di Bar'>y</a>"
        "<h3>Archivio di Stato di Baz</h3> <a href='https://x/archivio/baz/'>Scopri</a>"
        "<a href='/archive/?archivio=9'>Esplora</a>"
    )
    archives = parse_archives(page)
    assert [(a.id, a.name, a.url) for a in archives] == [
        (8, "Archivio di Stato di Bar", None),
        (9, "Archivio di Stato di Baz", "https://x/archivio/baz/"),
        (7, "Archivio di Stato di Foo", None),
    ]


def test_parse_archives_empty_page():
    assert parse_archives("<html><body>nothing here</body></html>") == []


# --------------------------------------------------------------------------- fondi
def test_parse_fondi_archive_145():
    fondi = parse_fondi(load_text("archive_145.html"), 145)
    assert len(fondi) == 1
    fondo = fondi[0]
    assert fondo.name == "Stato civile italiano"
    assert fondo.archive_id == 145
    assert fondo.id == 37842015
    assert len(fondo.series) == 60
    assert (fondo.series[0].id, fondo.series[0].name) == (37842016, "Acquaro")
    assert fondo.series[-1].name == "Zungri"
    assert all(s.name for s in fondo.series)


def test_parse_fondi_without_series_and_descrizione_fallback():
    page = """
    <div id="archive_property">
      <div data-property="1"><h3>Fondo con serie</h3></div>
      <div data-property="2">Fondo vuoto</div>
    </div>
    <div class="property-toponym open" data-id="1">
      <a href="/search-registry/?serie=11&descrizione=Paese%20A"></a>
      <a href="/search-registry/?serie=abc">bad id</a>
      <a href="/search-registry/?serie=12">Paese B</a>
    </div>
    """
    fondi = parse_fondi(page, 5)
    assert [(f.id, f.name, f.archive_id) for f in fondi] == [(1, "Fondo con serie", 5), (2, "Fondo vuoto", 5)]
    assert [(s.id, s.name) for s in fondi[0].series] == [(11, "Paese A"), (12, "Paese B")]
    assert fondi[1].series == ()


# --------------------------------------------------------------------------- search
@pytest.fixture(scope="module")
def serie_page():
    return parse_search(load_text("search_serie.html"), SearchQuery(serie=37842016), page=1, size=20)


def test_parse_search_totals(serie_page):
    assert serie_page.total == 133
    assert serie_page.page == 1
    assert serie_page.size == 20
    assert serie_page.page_count == 7
    assert serie_page.query == SearchQuery(serie=37842016)


def test_parse_search_registers(serie_page):
    regs = serie_page.registers
    assert len(regs) == 20
    first = regs[0]
    assert first.id == "an_ua37845354"
    assert first.title == "Registro: 1911"
    assert first.tipologia == "Morti"
    assert first.segnatura == "46"
    assert first.context == "Stato civile italiano > Acquaro"
    assert first.archive == "Archivio di Stato di Vibo Valentia"
    assert {r.tipologia for r in regs} == {"Morti", "Nati", "Matrimoni", "Matrimoni, pubblicazioni"}
    assert all(r.id.startswith("an_ua") for r in regs)
    assert len({r.id for r in regs}) == 20


def test_parse_search_facets(serie_page):
    facets = serie_page.facets
    assert set(facets) == set(FACET_FIELDS)
    for field, facet in facets.items():
        assert facet.field == field
        assert facet.label == FACET_FIELDS[field]
        assert all(t.field == field for t in facet.terms)
    assert [t.query for t in facets["conservatore_s"].terms] == [
        'conservatore_s:"Archivio di Stato di Vibo Valentia"'
    ]
    assert facets["conservatore_s"].terms[0].count == 133


def test_parse_search_tipologia_facet_quoted_values(serie_page):
    terms = serie_page.facets["tipologia_ss"].terms
    assert len(terms) == 6
    assert [t.value for t in terms] == sorted((t.value for t in terms), key=str.lower)
    by_value = {t.value: t for t in terms}
    pub = by_value["Matrimoni, pubblicazioni"]
    assert pub.query == 'tipologia_ss:"Matrimoni, pubblicazioni"'
    assert pub.count == 31
    assert by_value["Morti"].query == "tipologia_ss:Morti"
    assert by_value["Morti"].count == 44
    assert "Morti, allegati" in by_value
    assert sum(t.count for t in terms) == 133


def test_parse_search_year_facet_sorted_numerically(serie_page):
    years = serie_page.facets["anni_is"].terms
    assert len(years) == 41
    values = [int(t.value) for t in years]
    assert values == sorted(values)
    assert years[0].value == "1911"
    assert years[0].query == "anni_is:1911"
    assert years[0].count == 4
    assert years[-1].value == "1951"


def test_parse_search_empty_result():
    page = parse_search(load_text("search_empty.html"), SearchQuery(serie=1))
    assert page.total == 0
    assert page.registers == []
    assert page.facets == {}
    assert page.page_count == 1


def test_parse_search_synthetic_page_and_data_id_fallback():
    page_html = search_html(
        [(1, "Registro: 1860", "Nati"), (2, "Registro: 1861-1862", "Morti")],
        total=1234,
        facets={
            "tipologia_ss": [('"Morti, allegati"', 3), ("Nati", 1)],
            "anni_is": [("1900", 2), ("1860", 5)],
        },
    )
    # a register whose link has no ark id: the numeric data-id is used instead
    page_html = page_html.replace('href="/ark:/12657/an_ua2"', 'href="/somewhere"')
    result = parse_search(page_html, SearchQuery(archivio=1), page=2, size=50)
    assert result.total == 1234
    assert [r.id for r in result.registers] == ["an_ua1", "an_ua2"]
    assert [t.value for t in result.facets["anni_is"].terms] == ["1860", "1900"]
    assert result.facets["tipologia_ss"].terms[0].value == "Morti, allegati"


def test_parse_search_skips_items_without_id():
    page_html = search_html([(1, "Registro: 1860", "Nati")]).replace('data-id="1"', 'data-id="x"')
    page_html = page_html.replace("an_ua1", "nothing")
    assert parse_search(page_html, SearchQuery(archivio=1)).registers == []


# --------------------------------------------------------------------------- register page / manifest
def test_parse_manifest_url():
    assert parse_manifest_url(load_text("register.html")) == MANIFEST_URL


@pytest.mark.parametrize("quote", ["'", '"'])
def test_parse_manifest_url_quotes(quote):
    page = f"<script>var x = {{ manifestId : {quote}{MANIFEST_URL}{quote} }}</script>"
    assert parse_manifest_url(page) == MANIFEST_URL


def test_parse_manifest_url_missing():
    with pytest.raises(PortalError):
        parse_manifest_url("<html>no viewer</html>")


@pytest.mark.parametrize("as_text", [False, True])
def test_parse_manifest_fixture(as_text):
    raw = load_text("manifest.json") if as_text else load_json("manifest.json")
    m = parse_manifest(raw)
    assert m.url == MANIFEST_URL
    assert m.label == "Archivio di Stato di Vibo Valentia > Stato civile italiano > Acquaro > 1911"
    assert m.register_id == "an_ua37845354"
    assert len(m.pages) == 55
    first, last = m.pages[0], m.pages[-1]
    assert (first.index, first.label, first.image_id) == (1, "pag. 1", "LzblN79")
    assert first.service_url == "https://iiif-antenati.cultura.gov.it/iiif/2/LzblN79"
    assert (last.index, last.label) == (55, "pag. 55")
    assert [p.index for p in m.pages] == list(range(1, 56))
    assert m.title == "1911"
    assert m.tipologia == "Morti"
    assert m.dating == "1911 - 1911"
    assert m.archive == "Archivio di Stato di Vibo Valentia"
    assert m.context == ["Archivio di Stato di Vibo Valentia", "Stato civile italiano", "Acquaro"]


def test_parse_manifest_strips_html_from_metadata():
    m = parse_manifest(load_json("manifest.json"))
    assert m.metadata["Licenza"] == "https://antenati.cultura.gov.it/licenze"
    assert m.metadata["Vedi il registro"] == "https://antenati.cultura.gov.it/ark:/12657/an_ua37845354"


def test_parse_manifest_explicit_url_wins():
    assert parse_manifest(load_json("manifest.json"), url="https://x/manifest").url == "https://x/manifest"


def test_parse_manifest_variants():
    data = manifest_json("an_ua5", pages=3)
    canvases = data["sequences"][0]["canvases"]
    # page 2 without images is skipped (index still follows canvas position)
    canvases[1]["images"] = []
    # page 3: service as list, no register id in the canvas id
    canvases[2]["images"][0]["resource"]["service"] = [{"@id": "https://iiif/x/IMG3/"}]
    canvases[2]["@id"] = "https://dam/canvas/3"
    # page 1: no service at all -> derived from the resource id; canvas id without ark
    canvases[0]["@id"] = "https://dam/canvas/1"
    canvases[0]["images"][0]["resource"].pop("service")
    data["metadata"].append({"label": "Vedi il registro", "value": "<a href='x'>.../an_ua5</a>"})
    data["metadata"].append({"label": "<b>Lingua</b>", "value": [{"@value": "it"}, {"@value": "la"}]})
    data["label"] = [{"@value": "Label &amp; more"}]
    m = parse_manifest(json.dumps(data))
    assert [(p.index, p.image_id) for p in m.pages] == [(1, "an_ua5p1"), (3, "IMG3")]
    assert m.pages[0].service_url == "https://iiif-antenati.cultura.gov.it/iiif/2/an_ua5p1"
    assert m.pages[1].service_url == "https://iiif/x/IMG3"
    assert m.register_id == "an_ua5"  # from the "Vedi il registro" metadata
    assert m.metadata["Lingua"] == "it; la"
    assert m.label == "Label & more"


def test_parse_manifest_empty():
    m = parse_manifest({})
    assert m.pages == []
    assert m.register_id is None
    assert m.url == ""


# --------------------------------------------------------------------------- parse_register_ref
@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("an_ua37845354", ("an_ua37845354", None, None)),
        ("  an_ua37845354  ", ("an_ua37845354", None, None)),
        ("37845354", ("an_ua37845354", None, None)),
        ("https://antenati.cultura.gov.it/ark:/12657/an_ua37845354/", ("an_ua37845354", None, None)),
        ("https://antenati.cultura.gov.it/ark:/12657/an_ua37845354", ("an_ua37845354", None, None)),
        (
            "https://antenati.cultura.gov.it/ark:/12657/an_ua37845354/LzblN79",
            ("an_ua37845354", "LzblN79", None),
        ),
        (
            "https://antenati.cultura.gov.it/ark:/12657/an_ua37845354/LzblN79/",
            ("an_ua37845354", "LzblN79", None),
        ),
        ("https://antenati.cultura.gov.it/ark:/12657/an_ua1/?lang=it", ("an_ua1", None, None)),
        ("https://antenati.cultura.gov.it/ark:/12657/an_ua1/#top", ("an_ua1", None, None)),
        ("see an_ua42 somewhere in text", ("an_ua42", None, None)),
        (MANIFEST_URL, (None, None, MANIFEST_URL)),
        (MANIFEST_URL.rsplit("/", 1)[0], (None, None, MANIFEST_URL)),
        (MANIFEST_URL.rsplit("/", 1)[0] + "/", (None, None, MANIFEST_URL)),
    ],
)
def test_parse_register_ref(ref, expected):
    assert parse_register_ref(ref) == expected


@pytest.mark.parametrize("ref", ["", "   ", "hello", "an_ua", "https://antenati.cultura.gov.it/", "12a"])
def test_parse_register_ref_invalid(ref):
    with pytest.raises(ValueError):
        parse_register_ref(ref)


# --------------------------------------------------------------------------- year helpers
@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("1911", {1911}),
        ("1860-1863", {1860, 1861, 1862, 1863}),
        ("1863-1860", {1860, 1861, 1862, 1863}),
        ("1860,1870", {1860, 1870}),
        ("1860; 1870 1880", {1860, 1870, 1880}),
        ("nothing", set()),
        ("999", set()),
    ],
)
def test_parse_year_spec(spec, expected):
    assert _parse_year_spec(spec) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Registro: 1911", {1911}),
        ("Registro: 1852-1857", set(range(1852, 1858))),
        ("1911", {1911}),
        ("Registro: s.d.", set()),
    ],
)
def test_register_years(title, expected):
    assert register_years(title) == expected


def test_requested_years():
    assert requested_years(SearchQuery(serie=1)) is None
    assert requested_years(SearchQuery(serie=1, anno="1911")) == {1911}
    assert requested_years(SearchQuery(serie=1, facets=("anni_is:1911", "tipologia_ss:Nati"))) == {1911}
    assert requested_years(SearchQuery(serie=1, facets=('anni_is:"1911"',))) == {1911}
    # several constraints are intersected
    q = SearchQuery(serie=1, anno="1910-1912", facets=("anni_is:1911",))
    assert requested_years(q) == {1911}
    assert requested_years(SearchQuery(serie=1, anno="1910", facets=("anni_is:1911",))) == set()


def test_filter_predicates():
    assert _has_tipologia(SearchQuery(tipologia="Nati"))
    assert _has_tipologia(SearchQuery(serie=1, facets=("tipologia_ss:Nati",)))
    assert not _has_tipologia(SearchQuery(serie=1, facets=("anni_is:1911",)))

    assert _has_other_filter(SearchQuery(serie=1))
    assert _has_other_filter(SearchQuery(localita="Acquaro"))
    assert _has_other_filter(SearchQuery(archivio=1, facets=("localita_fondo_s:Acquaro",)))
    assert not _has_other_filter(SearchQuery(archivio=1, tipologia="Nati", anno="1911"))
    assert not _has_other_filter(
        SearchQuery(archivio=1, facets=("tipologia_ss:Nati", "anni_is:1911", 'conservatore_s:"A"'))
    )


def test_without_years():
    q = SearchQuery(
        archivio=1,
        serie=2,
        localita="L",
        tipologia="Nati",
        anno="1911",
        facets=("tipologia_ss:Nati", "anni_is:1911", "localita_ss:L"),
        sort="estremoRemoto_i asc",
        nominativi=True,
    )
    stripped = without_years(q)
    assert stripped.anno is None
    assert stripped.facets == ("tipologia_ss:Nati", "localita_ss:L")
    assert (stripped.archivio, stripped.serie, stripped.localita, stripped.tipologia) == (1, 2, "L", "Nati")
    assert (stripped.sort, stripped.nominativi) == ("estremoRemoto_i asc", True)
    assert q.anno == "1911"  # original untouched
