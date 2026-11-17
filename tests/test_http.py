from __future__ import annotations

import pytest
import requests
import responses

from antenati_dl.http import USER_AGENT, BlockedError, HttpClient, PortalError, is_image
from conftest import CLOUDFLARE_PAGE, IIIF, JPEG, PORTAL

IMG_URL = f"{IIIF}/LzblN79/full/pct:100/0/default.jpg"


# --------------------------------------------------------------------------- is_image
@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (JPEG, True),
        (JPEG + b"\x00" * 10, True),  # trailing padding after EOI
        (b"\xff\xd8" + b"\x00" * 100, False),  # truncated JPEG (no EOI)
        (b"\xff\xd8\xff\xd9", False),  # too short
        (b"", False),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, True),
        (b"GIF89a" + b"\x00" * 100, True),
        (b"II*\x00" + b"\x00" * 100, True),
        (b"MM\x00*" + b"\x00" * 100, True),
        (b"<html>" + b" " * 100 + b"</html>", False),
    ],
)
def test_is_image(data, expected):
    assert is_image(data) is expected


# --------------------------------------------------------------------------- headers / errors
def test_session_headers():
    client = HttpClient()
    headers = client.session.headers
    assert headers["User-Agent"] == USER_AGENT
    assert USER_AGENT.startswith("Mozilla/5.0")
    assert "antenati-dl/" in USER_AGENT
    assert headers["Referer"] == f"{PORTAL}/"
    assert headers["Accept-Language"].startswith("it-IT")


def test_custom_session_gets_portal_headers():
    session = requests.Session()
    client = HttpClient(session=session)
    assert client.session is session
    assert session.headers["Referer"] == f"{PORTAL}/"


@responses.activate
def test_get_sends_browser_headers(http_client):
    responses.get(f"{PORTAL}/esplora-gli-archivi/", body="ok")
    http_client.get(f"{PORTAL}/esplora-gli-archivi/")
    sent = responses.calls[0].request.headers
    assert sent["User-Agent"] == USER_AGENT
    assert sent["Referer"] == f"{PORTAL}/"


@responses.activate
def test_get_json_sends_origin_and_accept(http_client):
    url = "https://dam-antenati.cultura.gov.it/antenati/containers/x/manifest"
    responses.get(url, json={"a": 1})
    assert http_client.get_json(url) == {"a": 1}
    sent = responses.calls[0].request.headers
    assert sent["Origin"] == PORTAL
    assert sent["Accept"] == "application/json"
    assert sent["Referer"] == f"{PORTAL}/"


@responses.activate
def test_get_text_decodes_utf8(http_client):
    responses.get(f"{PORTAL}/x", body="Località ’".encode(), content_type="text/html; charset=UTF-8")
    assert http_client.get_text(f"{PORTAL}/x") == "Località ’"


@responses.activate
def test_get_passes_params(http_client):
    responses.get(f"{PORTAL}/archive/", body="ok")
    http_client.get_text(f"{PORTAL}/archive/", params={"archivio": 145})
    assert responses.calls[0].request.url == f"{PORTAL}/archive/?archivio=145"


@responses.activate
def test_cloudflare_403_raises_blocked(http_client):
    responses.get(f"{PORTAL}/x", body=CLOUDFLARE_PAGE, status=403)
    with pytest.raises(BlockedError, match="firewall"):
        http_client.get(f"{PORTAL}/x")
    assert issubclass(BlockedError, PortalError)


@responses.activate
def test_plain_403_raises_http_error(http_client):
    responses.get(f"{PORTAL}/x", body="forbidden", status=403)
    with pytest.raises(requests.HTTPError):
        http_client.get(f"{PORTAL}/x")


@responses.activate
def test_404_raises_http_error(http_client):
    responses.get(f"{PORTAL}/x", status=404)
    with pytest.raises(requests.HTTPError):
        http_client.get(f"{PORTAL}/x")


def test_throttle_waits_between_requests(no_sleep):
    client = HttpClient(min_interval=10)
    client._throttle()
    assert no_sleep == []
    client._throttle()
    assert len(no_sleep) == 1
    assert 0 < no_sleep[0] <= 10


def test_throttle_disabled_by_default(no_sleep):
    client = HttpClient()
    client._throttle()
    client._throttle()
    assert no_sleep == []


# --------------------------------------------------------------------------- get_image
@responses.activate
def test_get_image_success(http_client):
    responses.get(IMG_URL, body=JPEG, content_type="image/jpeg")
    assert http_client.get_image(IMG_URL) == JPEG
    assert len(responses.calls) == 1


@responses.activate
def test_get_image_retries_empty_body_then_succeeds(no_sleep):
    client = HttpClient(retries=5, backoff=1.0)
    responses.get(IMG_URL, body=b"", content_type="image/jpeg")
    responses.get(IMG_URL, body=b"\xff\xd8 truncated", content_type="image/jpeg")
    responses.get(IMG_URL, body=JPEG, content_type="image/jpeg")
    assert client.get_image(IMG_URL) == JPEG
    assert len(responses.calls) == 3
    assert no_sleep == [1.0, 2.0]  # exponential backoff between attempts


@responses.activate
def test_get_image_gives_up_after_attempts(no_sleep):
    client = HttpClient(retries=5, backoff=0.5)
    responses.get(IMG_URL, body=b"", content_type="image/jpeg")
    with pytest.raises(PortalError, match="empty or invalid image body"):
        client.get_image(IMG_URL, attempts=3)
    assert len(responses.calls) == 3
    assert no_sleep == [0.5, 1.0]  # no sleep after the last attempt


@responses.activate
def test_get_image_default_attempts_is_retries(no_sleep):
    client = HttpClient(retries=4, backoff=0)
    responses.get(IMG_URL, body=b"")
    with pytest.raises(PortalError):
        client.get_image(IMG_URL)
    assert len(responses.calls) == 4


@responses.activate
def test_get_image_with_zero_retries_still_tries_once(no_sleep):
    client = HttpClient(retries=0)
    responses.get(IMG_URL, body=JPEG)
    assert client.get_image(IMG_URL) == JPEG
    assert len(responses.calls) == 1


@responses.activate
def test_get_image_retries_http_errors(no_sleep):
    client = HttpClient(retries=0, backoff=0)  # no urllib3-level retries
    responses.get(IMG_URL, status=500)
    responses.get(IMG_URL, body=JPEG)
    assert client.get_image(IMG_URL, attempts=2) == JPEG
    assert len(responses.calls) == 2


@responses.activate
def test_get_image_does_not_retry_not_found(no_sleep):
    client = HttpClient(retries=0, backoff=0)
    responses.get(IMG_URL, status=404)
    with pytest.raises(PortalError, match="HTTP 404"):
        client.get_image(IMG_URL, attempts=5)
    assert len(responses.calls) == 1


@responses.activate
def test_get_image_blocked_is_not_retried(no_sleep):
    client = HttpClient(retries=5, backoff=0)
    responses.get(IMG_URL, body=CLOUDFLARE_PAGE, status=403)
    with pytest.raises(BlockedError):
        client.get_image(IMG_URL)
    assert len(responses.calls) == 1


@responses.activate
def test_get_image_cancelled_before_request(no_sleep):
    client = HttpClient(retries=3, backoff=0)
    with pytest.raises(PortalError, match="cancelled"):
        client.get_image(IMG_URL, cancelled=lambda: True)
    assert len(responses.calls) == 0


@responses.activate
def test_get_image_cancelled_between_attempts(no_sleep):
    client = HttpClient(retries=5, backoff=0)
    responses.get(IMG_URL, body=b"")
    checks = iter([False, True])
    with pytest.raises(PortalError, match="cancelled"):
        client.get_image(IMG_URL, cancelled=lambda: next(checks))
    assert len(responses.calls) == 1


@responses.activate
def test_get_image_backoff_sleep_is_interrupted_by_cancel(monkeypatch):
    # the backoff between attempts (up to 8 s with the defaults) used to be a plain sleep, delaying
    # the cancellation of a download (and the GUI shutdown that waits for it)
    client = HttpClient(retries=5, backoff=4.0)
    responses.get(IMG_URL, body=b"")
    state = {"cancel": False}
    slept: list[float] = []

    def fake_sleep(seconds):
        slept.append(seconds)
        if len(slept) == 2:
            state["cancel"] = True  # the user cancels while we wait

    monkeypatch.setattr("antenati_dl.http.time.sleep", fake_sleep)
    with pytest.raises(PortalError, match="cancelled"):
        client.get_image(IMG_URL, cancelled=lambda: state["cancel"])
    assert len(responses.calls) == 1
    assert sum(slept) < 4.0
