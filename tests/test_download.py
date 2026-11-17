"""Download engine with mocked HTTP, writing into ``tmp_path``."""

from __future__ import annotations

import json
import re
import shutil
import sys
import threading
import types
from collections import namedtuple
from datetime import datetime
from pathlib import Path

import pytest
import responses

from antenati_dl.download import Downloader, DownloadOptions, DownloadTarget, Estimate
from antenati_dl.models import EventKind, Manifest, ProgressEvent, RegisterSummary, SearchQuery
from antenati_dl.portal import SEARCH_URL, parse_manifest
from antenati_dl.utils import AVERAGE_PAGE_BYTES
from conftest import (
    CLOUDFLARE_PAGE,
    HTML_TYPE,
    IIIF,
    JPEG,
    PORTAL,
    load_json,
    load_text,
    make_manifest,
    manifest_json,
    search_html,
)

REGISTER_ID = "an_ua37845354"
ARK_URL = f"{PORTAL}/ark:/12657/{REGISTER_ID}/"
FIXTURE_DIR = Path(
    "Archivio di Stato di Vibo Valentia", "Stato civile italiano", "Acquaro", "Morti", f"1911 ({REGISTER_ID})"
)


# --------------------------------------------------------------------------- helpers
class ImageServer:
    """Callback for every IIIF image URL; records requests, can fail chosen image ids."""

    def __init__(self, fail: set[str] | None = None) -> None:
        self.fail = fail or set()
        self.urls: list[str] = []
        self._lock = threading.Lock()
        responses.add_callback(responses.GET, re.compile(re.escape(IIIF) + r"/.+"), callback=self)

    def __call__(self, request):
        with self._lock:
            self.urls.append(request.url)
        image_id = request.url[len(IIIF) + 1 :].split("/", 1)[0]
        if image_id in self.fail:
            return 200, {"Content-Type": "image/jpeg"}, b""  # the server's "empty 200" glitch
        return 200, {"Content-Type": "image/jpeg"}, JPEG

    @property
    def image_ids(self) -> list[str]:
        return sorted(u[len(IIIF) + 1 :].split("/", 1)[0] for u in self.urls)


@pytest.fixture
def fixture_manifest() -> Manifest:
    return parse_manifest(load_json("manifest.json"))


class Recorder:
    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []
        self.threads: set[int] = set()

    def __call__(self, event: ProgressEvent) -> None:
        self.events.append(event)
        self.threads.add(threading.get_ident())

    @property
    def kinds(self) -> list[EventKind]:
        return [e.kind for e in self.events]

    def of(self, kind: EventKind) -> list[ProgressEvent]:
        return [e for e in self.events if e.kind is kind]


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def make_downloader(portal, tmp_path, recorder):
    def factory(cancel_event: threading.Event | None = None, **options) -> Downloader:
        options.setdefault("output_dir", tmp_path)
        options.setdefault("workers", 2)
        options.setdefault("check_disk_space", False)
        return Downloader(portal, DownloadOptions(**options), on_event=recorder, cancel_event=cancel_event)

    return factory


def files_in(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


def mock_registers(specs: list[tuple[int, int]]) -> None:
    """Mock ark page + manifest for registers given as ``(numeric id, page count)``."""
    for num, pages in specs:
        data = manifest_json(f"an_ua{num}", pages=pages, title=f"18{num % 100:02d}", tipologia="Nati")
        responses.get(
            f"{PORTAL}/ark:/12657/an_ua{num}/",
            body=f"<script>viewer({{manifestId: '{data['@id']}'}})</script>",
            content_type=HTML_TYPE,
        )
        responses.get(data["@id"], json=data)


def mock_search(ids: list[int]) -> None:
    body = search_html([(i, f"Registro: 18{i % 100:02d}", "Nati") for i in ids])
    responses.get(SEARCH_URL, body=body, content_type=HTML_TYPE)


# --------------------------------------------------------------------------- layout & basics
@responses.activate
def test_downloads_into_hierarchical_layout(make_downloader, fixture_manifest, tmp_path, recorder):
    server = ImageServer()
    report = make_downloader().run([DownloadTarget(register=fixture_manifest, pages="1-3")])

    reg_dir = tmp_path / FIXTURE_DIR
    assert files_in(reg_dir) == ["0001.jpg", "0002.jpg", "0003.jpg", "register.json"]
    assert (reg_dir / "0001.jpg").read_bytes() == JPEG
    assert report.ok
    assert (report.registers_done, report.registers_failed) == (1, 0)
    assert (report.pages_downloaded, report.pages_skipped, report.pages_failed) == (3, 0, 0)
    assert report.bytes_downloaded == 3 * len(JPEG)
    assert report.output_dirs == [str(reg_dir)]
    assert server.image_ids == sorted(p.image_id for p in fixture_manifest.pages[:3])
    # original resolution is requested as pct:100 (full/full is blocked by the firewall)
    assert f"{IIIF}/LzblN79/full/pct:100/0/default.jpg" in server.urls
    assert all("/full/pct:100/0/default.jpg" in u for u in server.urls)
    assert not list(reg_dir.glob("*.part"))


@pytest.mark.parametrize(("size", "fragment"), [("max", "/full/max/0/"), ("800", "/full/800,/0/")])
@responses.activate
def test_image_size_option(make_downloader, fixture_manifest, size, fragment):
    server = ImageServer()
    make_downloader(size=size).run([DownloadTarget(register=fixture_manifest, pages="1")])
    assert server.urls == [f"{IIIF}/LzblN79{fragment}default.jpg"]


def test_register_dir_and_page_path(make_downloader, fixture_manifest, tmp_path):
    dl = make_downloader()
    assert dl.register_dir(fixture_manifest) == tmp_path / FIXTURE_DIR
    assert dl.page_path(fixture_manifest, fixture_manifest.pages[11]) == tmp_path / FIXTURE_DIR / "0012.jpg"


def test_register_dir_sanitizes_and_falls_back(make_downloader, tmp_path):
    dl = make_downloader()
    m = make_manifest(
        "an_ua9",
        context='Archivio: "A" > Fondo/B > Paese?',
        tipologia="Matrimoni, pubblicazioni",
        title="1860",
    )
    assert (
        dl.register_dir(m)
        == tmp_path / "Archivio_ _A_" / "Fondo_B" / "Paese_" / ("Matrimoni, pubblicazioni") / "1860 (an_ua9)"
    )
    bare = Manifest(url="u", label="")
    assert dl.register_dir(bare) == tmp_path / "Registro" / "registro"
    no_id = Manifest(url="u", label="A > B > Reg", metadata={"Tipologia": "Nati"})
    assert dl.register_dir(no_id) == tmp_path / "A" / "B" / "Reg" / "Nati" / "Reg"


# --------------------------------------------------------------------------- page selection
@pytest.mark.parametrize(
    ("pages", "expected"),
    [
        (None, list(range(1, 56))),
        ("all", list(range(1, 56))),
        ("2-4,10", [2, 3, 4, 10]),
        ("53-", [53, 54, 55]),
        ("last", [55]),
        ([1, 5, 5, 55], [1, 5, 55]),
    ],
)
def test_select_pages(make_downloader, fixture_manifest, pages, expected):
    selected = make_downloader().select_pages(fixture_manifest, DownloadTarget(register="x", pages=pages))
    assert [p.index for p in selected] == expected


def test_select_pages_by_image_id(make_downloader, fixture_manifest):
    target = DownloadTarget(register="x", page_image_id=fixture_manifest.pages[6].image_id)
    assert [p.index for p in make_downloader().select_pages(fixture_manifest, target)] == [7]


@pytest.mark.parametrize(
    "target", [DownloadTarget(pages=[0, 1]), DownloadTarget(pages=[56]), DownloadTarget(pages="60")]
)
def test_select_pages_out_of_range(make_downloader, fixture_manifest, target):
    with pytest.raises(ValueError):
        make_downloader().select_pages(fixture_manifest, target)


def test_select_pages_unknown_image_id(make_downloader, fixture_manifest):
    from antenati_dl.http import PortalError

    with pytest.raises(PortalError, match="nope"):
        make_downloader().select_pages(fixture_manifest, DownloadTarget(page_image_id="nope"))


@responses.activate
def test_page_url_target_downloads_single_page(make_downloader, fixture_manifest, tmp_path):
    responses.get(ARK_URL, body=load_text("register.html"), content_type=HTML_TYPE)
    responses.get(fixture_manifest.url, json=load_json("manifest.json"))
    server = ImageServer()
    page = fixture_manifest.pages[1]
    target = DownloadTarget.from_ref(f"{ARK_URL}{page.image_id}")
    assert target.page_image_id == page.image_id
    report = make_downloader().run([target])
    assert report.ok
    assert server.image_ids == [page.image_id]
    assert files_in(tmp_path / FIXTURE_DIR) == ["0002.jpg", "register.json"]


@responses.activate
def test_explicit_page_list(make_downloader, fixture_manifest, tmp_path):
    ImageServer()
    make_downloader().run([DownloadTarget(register=fixture_manifest, pages=[1, 3, 55])])
    assert files_in(tmp_path / FIXTURE_DIR) == ["0001.jpg", "0003.jpg", "0055.jpg", "register.json"]


@responses.activate
def test_invalid_page_selection_fails_register(make_downloader, fixture_manifest, recorder):
    server = ImageServer()
    report = make_downloader().run([DownloadTarget(register=fixture_manifest, pages="70")])
    assert report.registers_failed == 1
    assert not report.ok
    assert server.urls == []
    assert recorder.kinds == [EventKind.REGISTER_FAILED, EventKind.FINISHED]
    assert "outside" in recorder.events[0].error


# --------------------------------------------------------------------------- DownloadTarget
def test_download_target_from_ref():
    t = DownloadTarget.from_ref("https://antenati.cultura.gov.it/ark:/12657/an_ua1/AbC9")
    assert (t.register, t.page_image_id, t.pages) == (
        "https://antenati.cultura.gov.it/ark:/12657/an_ua1/AbC9",
        "AbC9",
        None,
    )
    with_pages = DownloadTarget.from_ref(
        "https://antenati.cultura.gov.it/ark:/12657/an_ua1/AbC9", pages="1-2"
    )
    assert with_pages.page_image_id is None
    assert with_pages.pages == "1-2"
    assert DownloadTarget.from_ref("an_ua1").page_image_id is None
    with pytest.raises(ValueError):
        DownloadTarget.from_ref("garbage")


def test_download_target_describe(fixture_manifest):
    assert DownloadTarget(register="an_ua1").describe() == "an_ua1"
    assert DownloadTarget(register=fixture_manifest).describe() == fixture_manifest.label
    assert (
        DownloadTarget(register=RegisterSummary("an_ua1", "Registro: 1900")).describe()
        == "Registro: 1900 an_ua1"
    )
    q = DownloadTarget.from_query(SearchQuery(serie=5), limit=3)
    assert q.limit == 3
    assert "'serie': '5'" in q.describe()


# --------------------------------------------------------------------------- resume / overwrite
@responses.activate
def test_resume_skips_existing_pages(make_downloader, fixture_manifest, tmp_path, recorder):
    reg_dir = tmp_path / FIXTURE_DIR
    reg_dir.mkdir(parents=True)
    (reg_dir / "0001.jpg").write_bytes(b"already here")
    (reg_dir / "0002.jpg").write_bytes(b"")  # empty file from a crash: downloaded again
    server = ImageServer()
    report = make_downloader().run([DownloadTarget(register=fixture_manifest, pages="1-3")])
    assert (report.pages_skipped, report.pages_downloaded) == (1, 2)
    assert report.ok
    assert (reg_dir / "0001.jpg").read_bytes() == b"already here"
    assert (reg_dir / "0002.jpg").read_bytes() == JPEG
    assert "LzblN79" not in server.image_ids
    assert len(server.urls) == 2
    skipped = recorder.of(EventKind.PAGE_SKIPPED)
    assert [e.page.index for e in skipped] == [1]
    assert skipped[0].path == str(reg_dir / "0001.jpg")


@responses.activate
def test_second_run_downloads_nothing(make_downloader, fixture_manifest):
    server = ImageServer()
    make_downloader().run([DownloadTarget(register=fixture_manifest, pages="1-4")])
    assert len(server.urls) == 4
    report = make_downloader().run([DownloadTarget(register=fixture_manifest, pages="1-4")])
    assert len(server.urls) == 4
    assert (report.pages_skipped, report.pages_downloaded, report.registers_done) == (4, 0, 1)


@responses.activate
def test_overwrite_downloads_again(make_downloader, fixture_manifest, tmp_path):
    reg_dir = tmp_path / FIXTURE_DIR
    reg_dir.mkdir(parents=True)
    (reg_dir / "0001.jpg").write_bytes(b"old")
    server = ImageServer()
    report = make_downloader(overwrite=True).run([DownloadTarget(register=fixture_manifest, pages="1")])
    assert (report.pages_skipped, report.pages_downloaded) == (0, 1)
    assert (reg_dir / "0001.jpg").read_bytes() == JPEG
    assert len(server.urls) == 1


# --------------------------------------------------------------------------- failures
@responses.activate
def test_failed_pages_are_counted_and_reported(make_downloader, fixture_manifest, tmp_path, recorder):
    bad = fixture_manifest.pages[1].image_id
    server = ImageServer(fail={bad})
    report = make_downloader().run([DownloadTarget(register=fixture_manifest, pages="1-3")])

    assert (report.pages_downloaded, report.pages_failed) == (2, 1)
    assert (report.registers_done, report.registers_failed) == (0, 1)
    assert not report.ok
    assert any(f"{REGISTER_ID} page 2" in err for err in report.errors)
    # retried (HttpClient(retries=2)) before giving up
    assert sum(1 for u in server.urls if f"/{bad}/" in u) == 2
    reg_dir = tmp_path / FIXTURE_DIR
    assert files_in(reg_dir) == ["0001.jpg", "0003.jpg", "register.json"]

    failed = recorder.of(EventKind.PAGE_FAILED)
    assert len(failed) == 1
    assert failed[0].page.index == 2
    assert "empty or invalid image body" in failed[0].error
    assert EventKind.REGISTER_DONE not in recorder.kinds
    reg_failed = recorder.of(EventKind.REGISTER_FAILED)
    assert len(reg_failed) == 1
    assert reg_failed[0].error == "1 pages not downloaded"
    assert recorder.kinds[-1] is EventKind.FINISHED
    assert "1 failed" in recorder.events[-1].message


@responses.activate
def test_failed_pages_succeed_on_rerun(make_downloader, fixture_manifest, tmp_path):
    bad = fixture_manifest.pages[0].image_id
    server = ImageServer(fail={bad})
    assert not make_downloader().run([DownloadTarget(register=fixture_manifest, pages="1-2")]).ok
    server.fail.clear()
    report = make_downloader().run([DownloadTarget(register=fixture_manifest, pages="1-2")])
    assert report.ok
    assert (report.pages_skipped, report.pages_downloaded) == (1, 1)


@responses.activate
def test_register_not_available_does_not_stop_job(make_downloader, tmp_path, recorder):
    mock_registers([(1001, 2)])
    responses.get(f"{PORTAL}/ark:/12657/an_ua1002/", status=404)
    ImageServer()
    targets = [DownloadTarget(register="an_ua1002"), DownloadTarget(register="an_ua1001")]
    report = make_downloader().run(targets)
    assert (report.registers_done, report.registers_failed) == (1, 1)
    assert report.pages_downloaded == 2
    first = recorder.events[0]
    assert first.kind is EventKind.REGISTER_FAILED
    assert (first.register_index, first.register_total) == (1, 2)
    assert first.message.startswith("Register not available")


@responses.activate
def test_blocked_stops_the_job(make_downloader, recorder):
    responses.get(f"{PORTAL}/ark:/12657/an_ua1/", body=CLOUDFLARE_PAGE, status=403)
    mock_registers([(1001, 2)])
    server = ImageServer()
    report = make_downloader().run([DownloadTarget(register="an_ua1"), DownloadTarget(register="an_ua1001")])
    assert report.registers_failed == 1
    assert report.registers_done == 0
    assert server.urls == []
    assert recorder.kinds == [EventKind.REGISTER_FAILED, EventKind.FINISHED]


# --------------------------------------------------------------------------- events
@responses.activate
def test_event_sequence(make_downloader, fixture_manifest, tmp_path, recorder):
    ImageServer()
    make_downloader(workers=3).run([DownloadTarget(register=fixture_manifest, pages="1-4")])
    assert recorder.kinds == [
        EventKind.REGISTER_START,
        *[EventKind.PAGE_DONE] * 4,
        EventKind.REGISTER_DONE,
        EventKind.FINISHED,
    ]
    start = recorder.events[0]
    assert (start.register_index, start.register_total, start.pages_total) == (1, 1, 4)
    assert start.register is fixture_manifest
    assert start.path == str(tmp_path / FIXTURE_DIR)
    page_events = recorder.of(EventKind.PAGE_DONE)
    assert [e.pages_done for e in page_events] == [1, 2, 3, 4]
    assert sorted(e.page.index for e in page_events) == [1, 2, 3, 4]
    assert all(e.bytes == len(JPEG) and e.pages_total == 4 for e in page_events)
    assert all(Path(e.path).name == f"{e.page.index:04d}.jpg" for e in page_events)
    done = recorder.of(EventKind.REGISTER_DONE)[0]
    assert (done.pages_done, done.pages_total) == (4, 4)
    finished = recorder.events[-1]
    assert finished.bytes == 4 * len(JPEG)
    assert finished.message.startswith("4 pages downloaded")
    # events are always emitted from the thread that called run()
    assert recorder.threads == {threading.get_ident()}


@responses.activate
def test_event_sequence_with_skipped_pages(make_downloader, fixture_manifest, tmp_path, recorder):
    reg_dir = tmp_path / FIXTURE_DIR
    reg_dir.mkdir(parents=True)
    (reg_dir / "0002.jpg").write_bytes(JPEG)
    ImageServer()
    make_downloader(workers=1).run([DownloadTarget(register=fixture_manifest, pages="1-3")])
    assert recorder.kinds == [
        EventKind.REGISTER_START,
        EventKind.PAGE_SKIPPED,
        EventKind.PAGE_DONE,
        EventKind.PAGE_DONE,
        EventKind.REGISTER_DONE,
        EventKind.FINISHED,
    ]
    assert [e.pages_done for e in recorder.events[1:4]] == [1, 2, 3]


@responses.activate
def test_multiple_registers_indexes(make_downloader, recorder):
    ImageServer()
    m1, m2 = make_manifest("an_ua1", 2), make_manifest("an_ua2", 1)
    report = make_downloader().run([DownloadTarget(register=m1), DownloadTarget(register=m2)])
    assert report.registers_done == 2
    starts = recorder.of(EventKind.REGISTER_START)
    assert [(e.register_index, e.register_total, e.pages_total) for e in starts] == [(1, 2, 2), (2, 2, 1)]


@responses.activate
def test_broken_callback_does_not_stop_download(portal, tmp_path, fixture_manifest):
    ImageServer()

    def explode(_event):
        raise RuntimeError("UI died")

    dl = Downloader(portal, DownloadOptions(output_dir=tmp_path, check_disk_space=False), on_event=explode)
    assert dl.run([DownloadTarget(register=fixture_manifest, pages="1-2")]).pages_downloaded == 2


# --------------------------------------------------------------------------- cancellation
@responses.activate
def test_cancel_during_download(make_downloader, fixture_manifest, recorder):
    server = ImageServer()
    cancel = threading.Event()

    def on_event(event):
        recorder(event)
        if event.kind is EventKind.PAGE_DONE:
            cancel.set()

    dl = make_downloader(cancel_event=cancel, workers=1)
    dl.on_event = on_event
    other = make_manifest("an_ua2", 3)
    report = dl.run([DownloadTarget(register=fixture_manifest, pages="1-20"), DownloadTarget(register=other)])

    assert report.cancelled
    assert not report.ok
    assert 1 <= report.pages_downloaded < 20
    assert report.pages_failed == 0  # interrupted pages are not failures
    assert report.registers_done == 0
    assert recorder.kinds[-2:] == [EventKind.CANCELLED, EventKind.FINISHED]
    assert EventKind.REGISTER_DONE not in recorder.kinds
    assert len(recorder.of(EventKind.REGISTER_START)) == 1  # the second register never started
    assert not any("an_ua2" in u for u in server.urls)
    assert dl.cancelled


@responses.activate
def test_cancel_before_start(make_downloader, fixture_manifest, recorder):
    server = ImageServer()
    dl = make_downloader()
    dl.cancel()
    report = dl.run([DownloadTarget(register=fixture_manifest)])
    assert report.cancelled
    assert server.urls == []
    assert recorder.kinds == [EventKind.CANCELLED, EventKind.FINISHED]


# --------------------------------------------------------------------------- disk space
DiskUsage = namedtuple("DiskUsage", "total used free")


@responses.activate
def test_insufficient_disk_space(make_downloader, fixture_manifest, tmp_path, recorder, monkeypatch):
    probed: list[Path] = []

    def fake_usage(path):
        probed.append(Path(path))
        return DiskUsage(10**9, 10**9, 1024)

    monkeypatch.setattr(shutil, "disk_usage", fake_usage)
    server = ImageServer()
    second = make_manifest("an_ua2", 2)
    report = make_downloader(check_disk_space=True).run(
        [DownloadTarget(register=fixture_manifest), DownloadTarget(register=second)]
    )
    assert report.registers_failed == 1
    assert report.pages_downloaded == 0
    assert server.urls == []
    assert any("Not enough disk space" in e for e in report.errors)
    assert recorder.kinds == [EventKind.REGISTER_FAILED, EventKind.FINISHED]  # job stopped
    assert not (tmp_path / FIXTURE_DIR).exists()
    assert probed == [tmp_path]  # nearest existing parent of the register directory


@responses.activate
def test_enough_disk_space(make_downloader, fixture_manifest, monkeypatch):
    needed = 2 * AVERAGE_PAGE_BYTES["full"] + 200 * 1024 * 1024
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: DiskUsage(0, 0, needed))
    ImageServer()
    report = make_downloader(check_disk_space=True).run(
        [DownloadTarget(register=fixture_manifest, pages="1-2")]
    )
    assert report.ok


@responses.activate
def test_disk_space_check_disabled_or_nothing_to_do(make_downloader, fixture_manifest, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(shutil, "disk_usage", lambda p: calls.append(p) or DiskUsage(0, 0, 0))
    ImageServer()
    assert (
        make_downloader(check_disk_space=False).run([DownloadTarget(register=fixture_manifest, pages="1")]).ok
    )
    assert calls == []
    # every page already present: no space needed, so no check
    assert (
        make_downloader(check_disk_space=True).run([DownloadTarget(register=fixture_manifest, pages="1")]).ok
    )
    assert calls == []


# --------------------------------------------------------------------------- metadata & pdf
@responses.activate
def test_metadata_register_json(make_downloader, fixture_manifest, tmp_path):
    ImageServer()
    make_downloader(size="max").run([DownloadTarget(register=fixture_manifest, pages="1")])
    info = json.loads((tmp_path / FIXTURE_DIR / "register.json").read_text("utf-8"))
    assert info["register_id"] == REGISTER_ID
    assert info["register_url"] == f"https://antenati.cultura.gov.it/ark:/12657/{REGISTER_ID}/"
    assert info["manifest_url"] == fixture_manifest.url
    assert info["label"] == fixture_manifest.label
    assert info["metadata"]["Tipologia"] == "Morti"
    assert info["metadata"]["Contesto archivistico"].endswith("Acquaro")
    assert info["image_size"] == "max"
    assert datetime.fromisoformat(info["downloaded_at"]).tzinfo is not None
    assert len(info["pages"]) == 55  # the whole register is described, not only the selection
    assert info["pages"][0] == {"index": 1, "label": "pag. 1", "image_id": "LzblN79", "file": "0001.jpg"}


@responses.activate
def test_metadata_without_register_id(make_downloader, tmp_path):
    ImageServer()
    m = Manifest(url="https://m", label="A > B > R", pages=make_manifest("an_ua1", 1).pages)
    make_downloader().run([DownloadTarget(register=m)])
    info = json.loads((tmp_path / "A" / "B" / "R" / "R" / "register.json").read_text("utf-8"))
    assert info["register_id"] is None
    assert info["register_url"] is None


@responses.activate
def test_metadata_disabled(make_downloader, fixture_manifest, tmp_path):
    ImageServer()
    make_downloader(write_metadata=False).run([DownloadTarget(register=fixture_manifest, pages="1")])
    assert files_in(tmp_path / FIXTURE_DIR) == ["0001.jpg"]


@responses.activate
def test_pdf_built_with_img2pdf(make_downloader, fixture_manifest, tmp_path, recorder, monkeypatch):
    converted: list[list[str]] = []
    fake = types.ModuleType("img2pdf")
    fake.convert = lambda files: converted.append(files) or b"%PDF-1.4 fake"
    monkeypatch.setitem(sys.modules, "img2pdf", fake)
    ImageServer()
    report = make_downloader(pdf=True).run([DownloadTarget(register=fixture_manifest, pages="1-2")])
    reg_dir = tmp_path / FIXTURE_DIR
    assert report.ok
    assert (reg_dir / "register.pdf").read_bytes() == b"%PDF-1.4 fake"
    assert converted == [[str(reg_dir / "0001.jpg"), str(reg_dir / "0002.jpg")]]
    assert any(e.kind is EventKind.LOG and "PDF created" in e.message for e in recorder.events)


@responses.activate
def test_pdf_failure_is_logged_not_fatal(make_downloader, fixture_manifest, recorder, monkeypatch):
    fake = types.ModuleType("img2pdf")

    def broken(_files):
        raise ValueError("bad jpeg")

    fake.convert = broken
    monkeypatch.setitem(sys.modules, "img2pdf", fake)
    ImageServer()
    report = make_downloader(pdf=True).run([DownloadTarget(register=fixture_manifest, pages="1")])
    assert report.ok
    logs = recorder.of(EventKind.LOG)
    assert logs and "bad jpeg" in logs[0].error


@responses.activate
def test_pdf_without_img2pdf(make_downloader, fixture_manifest, recorder, monkeypatch):
    monkeypatch.setitem(sys.modules, "img2pdf", None)  # import raises ImportError
    ImageServer()
    report = make_downloader(pdf=True).run([DownloadTarget(register=fixture_manifest, pages="1")])
    assert report.ok
    assert any("img2pdf" in e.message for e in recorder.of(EventKind.LOG))


# --------------------------------------------------------------------------- query targets
@responses.activate
def test_query_target_downloads_every_register(make_downloader, tmp_path, recorder):
    mock_search([1001, 1002, 1003])
    mock_registers([(1001, 2), (1002, 1), (1003, 3)])
    server = ImageServer()
    report = make_downloader().run([DownloadTarget.from_query(SearchQuery(serie=1))])
    assert report.registers_done == 3
    assert report.pages_downloaded == 6
    assert len(server.urls) == 6
    starts = recorder.of(EventKind.REGISTER_START)
    assert [(e.register.register_id, e.register_index, e.register_total) for e in starts] == [
        ("an_ua1001", 1, 3),
        ("an_ua1002", 2, 3),
        ("an_ua1003", 3, 3),
    ]
    base = tmp_path / "Archivio di Stato di Test" / "Stato civile italiano" / "Paese" / "Nati"
    assert sorted(p.name for p in base.iterdir()) == [
        "1801 (an_ua1001)",
        "1802 (an_ua1002)",
        "1803 (an_ua1003)",
    ]
    assert files_in(base / "1803 (an_ua1003)") == ["0001.jpg", "0002.jpg", "0003.jpg", "register.json"]


@responses.activate
def test_query_target_limit(make_downloader, recorder):
    mock_search([1001, 1002, 1003])
    mock_registers([(1001, 1), (1002, 1), (1003, 1)])
    ImageServer()
    report = make_downloader().run([DownloadTarget.from_query(SearchQuery(serie=1), limit=2)])
    assert report.registers_done == 2
    assert {e.register_total for e in recorder.of(EventKind.REGISTER_START)} == {2}


@responses.activate
def test_query_target_bad_register_does_not_stop_job(make_downloader, recorder):
    mock_search([1001, 1002, 1003])
    mock_registers([(1001, 1), (1003, 1)])
    responses.get(f"{PORTAL}/ark:/12657/an_ua1002/", status=404)
    ImageServer()
    report = make_downloader().run([DownloadTarget.from_query(SearchQuery(serie=1))])
    assert (report.registers_done, report.registers_failed) == (2, 1)
    assert [e.register_index for e in recorder.of(EventKind.REGISTER_START)] == [1, 3]
    assert recorder.of(EventKind.REGISTER_FAILED)[0].register_index == 2


@responses.activate
def test_query_count_failure_aborts_before_start(make_downloader, recorder):
    responses.get(SEARCH_URL, status=404)
    report = make_downloader().run([DownloadTarget.from_query(SearchQuery(serie=1))])
    assert report.registers_failed == 1
    assert recorder.kinds == [EventKind.REGISTER_FAILED, EventKind.FINISHED]
    assert recorder.events[-1].message == "Download not started"


@responses.activate
def test_count_registers(make_downloader):
    mock_search([1001, 1002, 1003])
    dl = make_downloader()
    targets = [
        DownloadTarget(register="an_ua1"),
        DownloadTarget.from_query(SearchQuery(serie=1)),
        DownloadTarget.from_query(SearchQuery(serie=1), limit=2),
    ]
    assert dl.count_registers(targets) == 1 + 3 + 2


# --------------------------------------------------------------------------- estimate
def test_estimate_single_register(make_downloader, fixture_manifest):
    est = make_downloader().estimate([DownloadTarget(register=fixture_manifest, pages="1-10")])
    assert est == Estimate(registers=1, pages=10, bytes=10 * AVERAGE_PAGE_BYTES["full"], exact=True)
    assert est.describe() == "1 register, 10 pages, 6.2 MB"


def test_estimate_uses_size_option(make_downloader, fixture_manifest):
    est = make_downloader(size="max").estimate([DownloadTarget(register=fixture_manifest)])
    assert (est.pages, est.bytes) == (55, 55 * AVERAGE_PAGE_BYTES["max"])


@responses.activate
def test_estimate_query_extrapolates_from_sample(make_downloader):
    mock_search([1001, 1002, 1003, 1004])
    mock_registers([(1001, 2), (1002, 4), (1003, 100), (1004, 100)])
    est = make_downloader().estimate([DownloadTarget.from_query(SearchQuery(serie=1))], sample=2)
    assert (est.registers, est.pages, est.exact) == (4, 12, False)  # avg 3 pages x 4 registers
    assert est.bytes == 12 * AVERAGE_PAGE_BYTES["full"]
    assert est.describe().startswith("4 registers, ~12 pages, ~")


@responses.activate
def test_estimate_query_exact_when_sample_covers_all(make_downloader, fixture_manifest):
    mock_search([1001, 1002])
    mock_registers([(1001, 2), (1002, 4)])
    targets = [
        DownloadTarget.from_query(SearchQuery(serie=1)),
        DownloadTarget(register=fixture_manifest, pages="1"),
    ]
    est = make_downloader().estimate(targets, sample=5)
    assert (est.registers, est.pages, est.exact) == (3, 7, True)


@responses.activate
def test_estimate_query_limit_and_empty(make_downloader):
    mock_search([1001, 1002, 1003])
    mock_registers([(1001, 5), (1002, 5), (1003, 5)])
    est = make_downloader().estimate([DownloadTarget.from_query(SearchQuery(serie=1), limit=1)])
    assert (est.registers, est.pages, est.exact) == (1, 5, True)

    responses.replace(responses.GET, SEARCH_URL, body=load_text("search_empty.html"), content_type=HTML_TYPE)
    est = make_downloader().estimate([DownloadTarget.from_query(SearchQuery(serie=2))])
    assert est == Estimate(0, 0, 0, True)


# --------------------------------------------------------------------------- iter_jobs
@responses.activate
def test_iter_jobs(make_downloader, fixture_manifest):
    mock_search([1001, 1002])
    mock_registers([(1001, 2), (1002, 3)])
    jobs = list(
        make_downloader().iter_jobs(
            [
                DownloadTarget(register=fixture_manifest, pages="1-2"),
                DownloadTarget.from_query(SearchQuery(serie=1)),
            ]
        )
    )
    assert [(m.register_id, None if p is None else [x.index for x in p]) for m, p in jobs] == [
        (REGISTER_ID, [1, 2]),
        ("an_ua1001", None),  # query targets: every page
        ("an_ua1002", None),
    ]


def test_iter_jobs_raises_on_bad_register(make_downloader, fixture_manifest):
    jobs = make_downloader().iter_jobs([DownloadTarget(register=fixture_manifest, pages="99")])
    with pytest.raises(ValueError):
        next(jobs)


@responses.activate
def test_failing_search_skips_only_that_target(make_downloader, fixture_manifest):
    responses.get(SEARCH_URL, body=search_html([(1001, "Registro: 1801", "Nati")]), content_type=HTML_TYPE)
    responses.get(f"{PORTAL}/ark:/12657/an_ua1001/", status=500)
    ImageServer()
    targets = [
        DownloadTarget.from_query(SearchQuery(serie=1)),
        DownloadTarget(register=fixture_manifest, pages="1"),
    ]
    report = make_downloader().run(targets)
    assert (report.registers_done, report.registers_failed) == (1, 1)
