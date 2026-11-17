"""HTTP layer.

The portal sits behind an AWS load balancer + Cloudflare that rejects non-browser clients:

* ``antenati.cultura.gov.it`` answers 403 unless the request carries a browser-like User-Agent;
* ``dam-antenati`` (IIIF manifests) and ``iiif-antenati`` (images) also require a ``Referer``
  pointing to the portal;
* the image server sometimes answers ``200`` with an empty body: such responses are retried.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import __version__
from .models import PORTAL_URL

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    f"Chrome/130.0 Safari/537.36 antenati-dl/{__version__}"
)
DEFAULT_TIMEOUT = (15, 120)  # connect, read


class PortalError(RuntimeError):
    """Raised when the portal returns something unexpected."""


class BlockedError(PortalError):
    """Raised when the firewall blocks a request (Cloudflare "Attention Required" page)."""


class HttpClient:
    """Thin wrapper around :class:`requests.Session` with portal-specific behaviour.

    Thread-safe for concurrent ``get`` calls (requests' Session is safe enough for
    simple GETs; connection pool is sized for the download workers).
    """

    def __init__(
        self,
        retries: int = 5,
        backoff: float = 1.0,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
        pool_size: int = 16,
        min_interval: float = 0.0,
        session: requests.Session | None = None,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.min_interval = min_interval
        self._last_request = 0.0
        self._lock = threading.Lock()
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
                "Referer": f"{PORTAL_URL}/",
            }
        )
        retry = Retry(
            total=retries,
            connect=retries,
            read=retries,
            status=retries,
            backoff_factor=backoff,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=pool_size, pool_maxsize=pool_size)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    # ------------------------------------------------------------------ helpers
    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            wait = self._last_request + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def get(self, url: str, params: dict | None = None, **kwargs) -> requests.Response:
        self._throttle()
        headers = kwargs.pop("headers", None)
        resp = self.session.get(url, params=params, timeout=self.timeout, headers=headers, **kwargs)
        if resp.status_code == 403 and b"Cloudflare" in resp.content[:4096]:
            raise BlockedError(f"Request blocked by the portal firewall: {resp.url}")
        resp.raise_for_status()
        return resp

    def get_text(self, url: str, params: dict | None = None) -> str:
        resp = self.get(url, params=params)
        resp.encoding = resp.encoding or "utf-8"
        return resp.text

    def get_json(self, url: str) -> dict:
        resp = self.get(url, headers={"Origin": PORTAL_URL, "Accept": "application/json"})
        try:
            return resp.json()
        except ValueError as exc:  # pragma: no cover - defensive
            raise PortalError(f"Invalid JSON from {url}") from exc

    def get_image(
        self,
        url: str,
        attempts: int | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> bytes:
        """Download an image, retrying on empty/invalid bodies. Returns the JPEG/PNG bytes."""
        attempts = attempts or self.retries
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            if cancelled and cancelled():
                raise PortalError("cancelled")
            try:
                resp = self.get(url)
                data = resp.content
                if is_image(data):
                    return data
                last_error = PortalError(f"empty or invalid image body ({len(data)} bytes)")
            except BlockedError:
                raise
            except (requests.RequestException, PortalError) as exc:
                last_error = exc
            delay = self.backoff * (2 ** (attempt - 1))
            log.debug("Image %s attempt %d failed (%s), retrying in %.1fs", url, attempt, last_error, delay)
            if attempt < attempts:
                time.sleep(delay)
        raise PortalError(f"Unable to download {url}: {last_error}")


def is_image(data: bytes) -> bool:
    """True if ``data`` looks like a complete JPEG or a PNG/GIF/TIFF image."""
    if len(data) < 64:
        return False
    if data[:2] == b"\xff\xd8":  # JPEG: check end-of-image marker (allow trailing padding)
        return b"\xff\xd9" in data[-64:]
    return data[:8] == b"\x89PNG\r\n\x1a\n" or data[:4] in (b"GIF8", b"II*\x00", b"MM\x00*")
