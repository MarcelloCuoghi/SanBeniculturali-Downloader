"""Smoke tests against the real Portale Antenati.

Deselected by default (see ``addopts`` in pyproject.toml); run with ``uv run pytest -m network``.
They detect layout changes of the portal that the offline fixtures cannot.
"""

from __future__ import annotations

import pytest

from antenati_dl.http import HttpClient, is_image
from antenati_dl.models import SearchQuery
from antenati_dl.portal import Portal

pytestmark = pytest.mark.network

ARCHIVE_ID = 145  # Archivio di Stato di Vibo Valentia
SERIE_ID = 37842016  # Acquaro
REGISTER_ID = "an_ua37845354"  # Morti 1911, 55 pages


@pytest.fixture(scope="module")
def live_portal() -> Portal:
    return Portal(HttpClient(retries=3, backoff=1.0))


def test_archives(live_portal):
    archives = live_portal.archives()
    assert len(archives) >= 90
    assert live_portal.get_archive(ARCHIVE_ID).name == "Archivio di Stato di Vibo Valentia"


def test_fondi(live_portal):
    fondi = live_portal.fondi(ARCHIVE_ID)
    civile = next(f for f in fondi if f.name == "Stato civile italiano")
    assert len(civile.series) >= 50
    assert any(s.id == SERIE_ID and s.name == "Acquaro" for s in civile.series)


def test_search_serie_facets(live_portal):
    page = live_portal.search(SearchQuery(serie=SERIE_ID), size=20)
    assert page.total >= 100
    assert len(page.registers) == 20
    assert all(r.id.startswith("an_ua") for r in page.registers)
    tipologie = {t.value: t for t in page.facets["tipologia_ss"].terms}
    assert {"Nati", "Morti", "Matrimoni"} <= set(tipologie)
    assert tipologie["Matrimoni, pubblicazioni"].query == 'tipologia_ss:"Matrimoni, pubblicazioni"'
    assert any(t.value == "1911" for t in page.facets["anni_is"].terms)


def test_search_tipologia_and_year(live_portal):
    # triggers the portal bug worked around by Portal.search (client-side year filtering)
    query = SearchQuery(serie=SERIE_ID).refine("tipologia_ss:Morti").refine("anni_is:1911")
    page = live_portal.search(query)
    assert page.total >= 1
    assert REGISTER_ID in {r.id for r in page.registers}
    assert all("1911" in r.title for r in page.registers)


def test_manifest(live_portal):
    manifest = live_portal.manifest(REGISTER_ID)
    assert manifest.register_id == REGISTER_ID
    assert len(manifest.pages) == 55
    assert manifest.pages[0].image_id == "LzblN79"
    assert manifest.tipologia == "Morti"
    assert manifest.context[-1] == "Acquaro"


def test_thumbnail_and_image_info(live_portal):
    page = live_portal.manifest(REGISTER_ID).pages[0]
    data = live_portal.thumbnail(page, height=120)
    assert is_image(data)
    assert data[:2] == b"\xff\xd8"
    info = live_portal.image_info(page)
    assert (info["width"], info["height"]) == (6303, 3826)


def test_small_width_image(live_portal):
    page = live_portal.manifest(REGISTER_ID).pages[0]
    assert is_image(live_portal.http.get_image(page.image_url(300)))
