"""Shared helpers and fixtures for the offline test suite.

Every HTTP call is mocked with ``responses``; the files in ``tests/fixtures`` were saved from the
real portal. Synthetic builders (:func:`search_html`, :func:`manifest_json`) cover cases the saved
pages do not (pagination, failures, several registers).
"""

from __future__ import annotations

import html
import json
from pathlib import Path

import pytest

from antenati_dl.http import HttpClient
from antenati_dl.models import Manifest
from antenati_dl.portal import Portal, parse_manifest

FIXTURES = Path(__file__).parent / "fixtures"

PORTAL = "https://antenati.cultura.gov.it"
DAM = "https://dam-antenati.cultura.gov.it/antenati/containers"
IIIF = "https://iiif-antenati.cultura.gov.it/iiif/2"
HTML_TYPE = "text/html; charset=UTF-8"

#: Smallest body accepted by :func:`antenati_dl.http.is_image` as a JPEG (SOI ... EOI, >= 64 bytes).
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 120 + b"\xff\xd9"

#: Body of the firewall's block page (served with HTTP 403).
CLOUDFLARE_PAGE = b"<html><title>Attention Required! | Cloudflare</title></html>"


def load_text(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


def load_json(name: str) -> dict:
    return json.loads(load_text(name))


def search_html(
    registers: list[tuple[int, str, str]],
    total: int | None = None,
    facets: dict[str, list[tuple[str, int]]] | None = None,
    context: str = "Stato civile italiano > Acquaro",
    archive: str = "Archivio di Stato di Vibo Valentia",
) -> str:
    """A minimal ``/search-registry/`` page shaped like the real one.

    ``registers``: ``(numeric id, title, tipologia)``; ``facets``: ``{field: [(raw value, count)]}``
    where the raw value is what follows ``field:`` in ``data-facet-term`` (quotes included).
    """
    total = len(registers) if total is None else total
    facet_items = []
    for field, terms in (facets or {}).items():
        links = "".join(
            f'<li class="facet-term"><a href="#" data-facet-term="{html.escape(f"{field}:{raw}")}">'
            f"<span>{html.escape(raw.strip(chr(34)))}</span> <small>({count})</small></a></li>"
            for raw, count in terms
        )
        facet_items.append(f'<li class="facet"><h4 class="facet-name">{field}</h4><ul>{links}</ul></li>')
    items = "".join(
        f'<li class="search-item" data-id="{rid}"><div>'
        f'<h3 class="text-primary"><a href="/ark:/12657/an_ua{rid}">{html.escape(title)}</a></h3>'
        f"<p><strong>{html.escape(tipologia)}</strong></p>"
        f"<p><strong>Segnatura attuale:</strong> {rid % 100}</p>"
        f"<p>{html.escape(context)}</p>"
        f"<p><small>Conservato da: <a href='#'><strong>{html.escape(archive)}</strong></a></small></p>"
        f"</div></li>"
        for rid, title, tipologia in registers
    )
    return (
        "<html><body>"
        f'<aside id="facets"><ul>{"".join(facet_items)}</ul></aside>'
        f'<div class="search-query flex-1"><strong>{total}</strong> risultati</div>'
        f'<ul id="search_list" class="no-appearance">{items}</ul>'
        "</body></html>"
    )


def manifest_json(
    register_id: str = "an_ua1001",
    pages: int = 3,
    title: str = "1911",
    tipologia: str = "Morti",
    context: str = "Archivio di Stato di Test > Stato civile italiano > Paese",
    container: str | None = None,
) -> dict:
    """A small IIIF Presentation 2 manifest shaped like the portal's."""
    container = container or f"c{register_id}"
    url = f"{DAM}/{container}/manifest"
    canvases = []
    for i in range(1, pages + 1):
        image_id = f"{register_id}p{i}"
        canvases.append(
            {
                "@id": f"{PORTAL}/ark:/12657/{register_id}/{image_id}",
                "label": f"pag. {i}",
                "images": [
                    {
                        "resource": {
                            "@id": f"{IIIF}/{image_id}/full/full/0/default.jpg",
                            "service": {"@id": f"{IIIF}/{image_id}"},
                        }
                    }
                ],
            }
        )
    return {
        "@id": url,
        "label": f"{context} > {title}",
        "metadata": [
            {"label": "Titolo", "value": title},
            {"label": "Tipologia", "value": tipologia},
            {"label": "Datazione", "value": f"{title} - {title}"},
            {"label": "Contesto archivistico", "value": context},
            {"label": "Conservato da", "value": context.split(" > ")[0]},
        ],
        "sequences": [{"canvases": canvases}],
    }


def make_manifest(register_id: str = "an_ua1001", pages: int = 3, **kwargs) -> Manifest:
    return parse_manifest(manifest_json(register_id, pages, **kwargs))


@pytest.fixture
def http_client() -> HttpClient:
    """Fast client: no backoff, few retries (urllib3 retries are honoured by ``responses``)."""
    return HttpClient(retries=2, backoff=0, timeout=(2, 2))


@pytest.fixture
def portal(http_client: HttpClient) -> Portal:
    return Portal(http_client)


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Make ``time.sleep`` in the HTTP layer instant; returns the list of requested delays."""
    delays: list[float] = []
    monkeypatch.setattr("antenati_dl.http.time.sleep", delays.append)
    return delays
