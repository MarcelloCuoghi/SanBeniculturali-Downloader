"""Offscreen smoke test of the Qt GUI with a fake portal and a fake downloader (no network)."""

from __future__ import annotations

import logging
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSettings  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from antenati_dl.download import DownloadTarget, Estimate  # noqa: E402
from antenati_dl.models import (  # noqa: E402
    Archive,
    DownloadReport,
    EventKind,
    Facet,
    FacetTerm,
    Fondo,
    Manifest,
    Page,
    ProgressEvent,
    RegisterSummary,
    SearchPage,
    SearchQuery,
    Serie,
)

N_PAGES = 12


def _jpeg() -> bytes:
    img = QImage(60, 80, QImage.Format.Format_RGB32)
    img.fill(QColor("#8a6d3b"))
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "JPG")
    buf.close()
    return bytes(data.data())


class FakeHttp:
    def __init__(self, image: bytes) -> None:
        self.image = image
        self.urls: list[str] = []

    def get_image(self, url: str, attempts=None, cancelled=None) -> bytes:
        self.urls.append(url)
        return self.image


class FakePortal:
    def __init__(self, image: bytes) -> None:
        self.http = FakeHttp(image)
        self.thumbnail_calls: list[str] = []
        self.serie = Serie(37842016, "Acquaro")

    def archives(self, refresh: bool = False) -> list[Archive]:
        return [Archive(1, "Archivio di Stato di Bari"), Archive(145, "Archivio di Stato di Vibo Valentia")]

    def fondi(self, archive_id: int) -> list[Fondo]:
        return [Fondo(10, "Stato civile italiano", archive_id, (self.serie, Serie(2, "Arena")))]

    @staticmethod
    def _term(field: str, value: str, count: int) -> FacetTerm:
        return FacetTerm(field, value, count, f"{field}:{value}")

    def search(self, query: SearchQuery, page: int = 1, size: int = 100) -> SearchPage:
        facets = {
            "primo_livello_label_s": Facet(
                "primo_livello_label_s",
                "Fondo",
                [self._term("primo_livello_label_s", "Stato civile italiano", 9)],
            ),
            "tipologia_ss": Facet(
                "tipologia_ss",
                "Tipologia",
                [self._term("tipologia_ss", "Morti", 2), self._term("tipologia_ss", "Nati", 3)],
            ),
        }
        if any(f.startswith("tipologia_ss:") for f in query.facets):
            facets["anni_is"] = Facet(
                "anni_is", "Anno", [self._term("anni_is", "1911", 1), self._term("anni_is", "1912", 2)]
            )
        return SearchPage(query=query, total=3, page=page, size=size, registers=[], facets=facets)

    def iter_registers(self, query: SearchQuery, limit=None, size: int = 1000):
        yield RegisterSummary(
            "an_ua37845354", "Registro: 1911", "Nati", "46", "Stato civile italiano > Acquaro", "AS Vibo"
        )

    def count(self, query: SearchQuery) -> int:
        return 42

    def manifest(self, ref) -> Manifest:
        pages = [
            Page(i, f"pag. {i}", f"IMG{i}", f"https://iiif.example/iiif/2/IMG{i}")
            for i in range(1, N_PAGES + 1)
        ]
        return Manifest(
            url="https://dam.example/containers/x/manifest",
            label="Archivio di Stato di Vibo Valentia > Stato civile italiano > Acquaro > Nati > 1911",
            metadata={
                "Titolo": "Registro: 1911",
                "Tipologia": "Nati",
                "Datazione": "1911",
                "Contesto archivistico": "AS Vibo Valentia > Stato civile italiano > Acquaro",
            },
            pages=pages,
            register_id="an_ua37845354",
        )

    def thumbnail(self, page: Page, height: int = 200) -> bytes:
        self.thumbnail_calls.append(page.image_id)
        return self.http.image


class FakeDownloader:
    instances: list[FakeDownloader] = []

    def __init__(self, portal, options=None, on_event=None, cancel_event=None) -> None:
        self.portal = portal
        self.options = options
        self.on_event = on_event or (lambda _e: None)
        self.targets: list[DownloadTarget] = []
        FakeDownloader.instances.append(self)

    def estimate(self, targets, sample: int = 5) -> Estimate:
        return Estimate(42, 4200, 4200 * 650_000, False)

    def run(self, targets: list[DownloadTarget]) -> DownloadReport:
        self.targets = list(targets)
        report = DownloadReport()
        for i, target in enumerate(targets, start=1):
            manifest = target.register if isinstance(target.register, Manifest) else None
            if manifest is None:  # query target: pretend one small register
                manifest = self.portal.manifest("an_ua37845354")
            pages = target.pages if isinstance(target.pages, list) else [p.index for p in manifest.pages]
            common = {"register": manifest, "register_index": i, "register_total": len(targets)}
            self.on_event(ProgressEvent(EventKind.REGISTER_START, pages_total=len(pages), path="x", **common))
            for done, _idx in enumerate(pages, start=1):
                self.on_event(
                    ProgressEvent(
                        EventKind.PAGE_DONE, pages_done=done, pages_total=len(pages), bytes=1000, **common
                    )
                )
                report.pages_downloaded += 1
                report.bytes_downloaded += 1000
            self.on_event(
                ProgressEvent(
                    EventKind.REGISTER_DONE, pages_done=len(pages), pages_total=len(pages), **common
                )
            )
            report.registers_done += 1
        self.on_event(ProgressEvent(EventKind.FINISHED, message="done"))
        return report


def wait_until(condition, timeout: float = 5.0, widget=None) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if condition():
            return
        if widget is not None:
            widget.grab()  # force a paint (drives the lazy thumbnail loading)
        time.sleep(0.005)
    QApplication.processEvents()
    assert condition(), "condition not reached in time"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qapp, tmp_path):
    from antenati_dl.gui.main_window import MainWindow

    FakeDownloader.instances.clear()
    settings = QSettings(str(tmp_path / "gui.ini"), QSettings.Format.IniFormat)
    settings.setValue("download/output_dir", str(tmp_path / "out"))
    portal = FakePortal(_jpeg())
    win = MainWindow(portal=portal, settings=settings, downloader_factory=FakeDownloader)
    win.show()
    yield win
    win.close()
    win.deleteLater()
    QApplication.processEvents()


def _child(win, parent, text: str):
    """Proxy index of the child of ``parent`` whose label starts with ``text``."""
    proxy = win.proxy
    for row in range(proxy.rowCount(parent)):
        idx = proxy.index(row, 0, parent)
        if str(idx.data()).startswith(text):
            return idx
    return None


def _expand(win, parent, text: str):
    idx = _child(win, parent, text)
    assert idx is not None, f"{text!r} not found"
    win.tree.expand(idx)
    wait_until(lambda: _child(win, win.proxy.index(idx.row(), 0, parent), "Loading") is None)
    return win.proxy.index(idx.row(), 0, parent)


def test_browse_select_and_download(window):
    from antenati_dl.gui.downloads import JobState
    from antenati_dl.gui.main_window import PAGE_NODE, PAGE_REGISTER
    from antenati_dl.gui.navigator import NodeKind

    win = window
    root = win.proxy.index(0, 0).parent()
    wait_until(lambda: win.nav_model.rowCount() == 2)

    archive = _expand(win, root, "Archivio di Stato di Vibo")
    fondo = _expand(win, archive, "Stato civile italiano")
    serie = _expand(win, fondo, "Acquaro")
    tipologia = _expand(win, serie, "Nati")
    assert str(tipologia.data()) == "Nati (3)"
    anno = _expand(win, tipologia, "1911")
    register = _child(win, anno, "Registro: 1911")
    assert register is not None
    assert win.nav_model.node_at(win.proxy.mapToSource(register)).kind is NodeKind.REGISTER

    # selecting a register shows its pages
    win.tree.setCurrentIndex(register)
    wait_until(lambda: win.register_view.model.rowCount() == N_PAGES)
    assert win.stack.currentIndex() == PAGE_REGISTER
    assert "Registro: 1911" in win.register_view.title.text()
    assert "an_ua37845354" in win.register_view.details.text()

    # thumbnails are loaded lazily for the visible items
    wait_until(lambda: win.thumbs.cached("IMG1") is not None, widget=win)
    assert len(win.portal.thumbnail_calls) <= N_PAGES

    # range selection + download of the selected pages
    win.register_view.range_edit.setText("1-3,5")
    assert win.register_view.apply_range()
    assert [p.index for p in win.register_view.selected_pages()] == [1, 2, 3, 5]
    assert win.act_download_selected.isEnabled()
    win.act_download_selected.trigger()
    assert len(win.manager.jobs) == 1
    job = win.manager.jobs[0]
    wait_until(lambda: job.state is JobState.DONE)
    assert job.pages_done == 4 and job.registers_done == 1 and job.bytes == 4000
    assert FakeDownloader.instances[-1].targets[0].pages == [1, 2, 3, 5]
    assert win.queue.model.rowCount() == 1
    assert "4/4" in win.queue.model.index(0, 3).data()

    # whole register
    win.act_download_register.trigger()
    wait_until(lambda: win.manager.jobs[-1].state is JobState.DONE)
    assert win.manager.jobs[-1].pages_done == N_PAGES

    # preview dialog with prev/next
    win.preview_page(0)
    dlg = win._preview
    wait_until(lambda: dlg._image is not None)
    dlg.next_btn.click()
    wait_until(lambda: dlg.row == 1 and dlg._image is not None)
    dlg.download_btn.click()
    assert win.manager.jobs[-1].targets[0].pages == [2]
    dlg.close()

    # non-register node: summary + bulk download with confirmation
    win.tree.setCurrentIndex(serie)
    assert win.stack.currentIndex() == PAGE_NODE
    wait_until(lambda: "42" in win.node_pane.count.text())
    win.download_node()
    bulk = win._bulk_dialog
    assert bulk is not None
    wait_until(lambda: bulk.start_btn.isEnabled())
    assert "42 registers" in bulk.estimate_label.text()
    bulk.accept()
    target = win.manager.jobs[-1].targets[0]
    assert target.query is not None and target.query.serie == 37842016
    wait_until(lambda: not win.manager.has_active())

    # tipologia nodes use the facet count, no network call needed
    win.tree.setCurrentIndex(tipologia)
    assert "3" in win.node_pane.count.text()


def test_filter_and_go_to_register(window):
    win = window
    wait_until(lambda: win.nav_model.rowCount() == 2)
    win.filter_edit.setText("bari")
    win._apply_filter()
    assert win.proxy.rowCount() == 1
    win.filter_edit.clear()
    win._apply_filter()
    assert win.proxy.rowCount() == 2

    win.go_edit.setText("https://antenati.cultura.gov.it/ark:/12657/an_ua37845354/IMG3")
    win.go_to_register()
    wait_until(lambda: win.current_manifest is not None)
    assert [p.index for p in win.register_view.selected_pages()] == [3]


def test_settings_roundtrip(tmp_path, qapp):
    from antenati_dl.gui.settings import SIZE_CUSTOM, AppSettings

    qs = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s = AppSettings(output_dir=tmp_path / "dl", size_mode=SIZE_CUSTOM, custom_width=1600, workers=7, pdf=True)
    s.save(qs)
    loaded = AppSettings.load(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    assert loaded == s
    opts = loaded.download_options()
    assert opts.size == "1600" and opts.workers == 7 and opts.pdf and opts.output_dir == tmp_path / "dl"


def test_close_with_running_download_cancels(window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from antenati_dl.gui.downloads import JobState

    win = window
    started = []

    class SlowDownloader(FakeDownloader):
        def run(self, targets):
            started.append(True)
            while not self.cancel_event.is_set():
                time.sleep(0.01)
            return DownloadReport(cancelled=True)

        def __init__(self, portal, options=None, on_event=None, cancel_event=None):
            super().__init__(portal, options, on_event, cancel_event)
            self.cancel_event = cancel_event

    win.manager.factory = SlowDownloader
    job = win.manager.enqueue("slow", [DownloadTarget.from_ref("an_ua1")], win.settings.download_options())
    wait_until(lambda: bool(started))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    assert win.close()
    assert job.cancel_event.is_set()
    QApplication.processEvents()
    assert job.state in (JobState.CANCELLING, JobState.CANCELLED)


def test_rapid_selection_skips_stale_requests(window):
    """Moving quickly through registers must not fetch every stale one before the selected one."""
    win = window
    wait_until(lambda: win.nav_model.rowCount() == 2 and win.runner.pending_count == 0)
    gate = threading.Event()
    calls: list[str] = []
    real_manifest = win.portal.manifest

    def manifest(ref):
        calls.append(ref)
        if len(calls) == 1:
            gate.wait(5)
        return real_manifest(ref)

    win.portal.manifest = manifest
    win.runner.pool.setMaxThreadCount(1)
    win.load_register("an_ua1")
    wait_until(lambda: len(calls) == 1)
    for ref in ("an_ua2", "an_ua3", "an_ua4"):
        win.load_register(ref)
    gate.set()
    wait_until(lambda: win.current_manifest is not None and win.runner.pending_count == 0)
    assert calls == ["an_ua1", "an_ua4"]
    assert win.register_view.model.rowCount() == N_PAGES


def test_preview_closed_while_loading(window, caplog):
    """A result arriving after the preview was closed must not touch its deleted widgets."""
    win = window
    wait_until(lambda: win.nav_model.rowCount() == 2 and win.runner.pending_count == 0)
    win.load_register("an_ua1")
    wait_until(lambda: win.current_manifest is not None)
    gate = threading.Event()
    http = win.portal.http
    real_get_image = http.get_image

    def slow_get_image(url, attempts=None, cancelled=None):
        gate.wait(5)
        return real_get_image(url)

    http.get_image = slow_get_image
    from antenati_dl.gui.dialogs import PreviewDialog

    PreviewDialog._cache.clear()
    win.preview_page(5)
    dlg = win._preview
    destroyed = []
    dlg.destroyed.connect(lambda *_: destroyed.append(True))
    dlg.close()
    wait_until(lambda: bool(destroyed))
    caplog.set_level(logging.WARNING)
    gate.set()
    wait_until(lambda: win.runner.pending_count == 0)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], caplog.text


def test_background_work_running_tracks_stuck_download(window):
    win = window
    wait_until(lambda: win.runner.pending_count == 0)
    release = threading.Event()

    class StuckDownloader(FakeDownloader):
        def run(self, targets):  # e.g. a request hanging on a dead connection: ignores the cancel
            release.wait(5)
            return DownloadReport(cancelled=True)

    win.manager.factory = StuckDownloader
    job = win.manager.enqueue("stuck", [DownloadTarget.from_ref("an_ua1")], win.settings.download_options())
    wait_until(lambda: win.manager.threads_running())
    win.manager.cancel(job.id)
    assert win.background_work_running()
    release.set()
    assert win.manager.wait(5000)
    assert not win.background_work_running()
    wait_until(lambda: not win.manager.has_active())  # else closing the window asks for confirmation


def test_app_exits_without_destructors_when_threads_are_stuck(qapp, monkeypatch):
    """Destroying a running QThread aborts the process: app.main must leave with os._exit instead."""
    from antenati_dl.gui import app as app_module
    from antenati_dl.gui import main_window

    class FakeWindow:
        busy = True

        def show(self):
            pass

        def background_work_running(self):
            return FakeWindow.busy

    exits: list[int] = []

    class Exited(Exception):
        pass

    def fake_exit(code):
        exits.append(code)
        raise Exited

    monkeypatch.setattr(main_window, "MainWindow", FakeWindow)
    monkeypatch.setattr(QApplication, "exec", lambda *_a: 0)
    monkeypatch.setattr(app_module.os, "_exit", fake_exit)
    names = (qapp.applicationName(), qapp.organizationName(), qapp.applicationDisplayName())
    with pytest.raises(Exited):
        app_module.main([])
    assert exits == [0]
    FakeWindow.busy = False
    assert app_module.main([]) == 0 and exits == [0]
    qapp.setApplicationName(names[0])
    qapp.setOrganizationName(names[1])
    qapp.setApplicationDisplayName(names[2])


def test_failed_thumbnails_are_retried_when_the_register_is_shown_again(qapp):
    from PySide6.QtCore import Qt

    from antenati_dl.gui.register_view import ThumbnailLoader, ThumbnailModel

    portal = FakePortal(_jpeg())
    fail = {"IMG1"}

    def thumbnail(page, height=200):
        portal.thumbnail_calls.append(page.image_id)
        if page.image_id in fail:
            raise OSError("empty body")
        return portal.http.image

    portal.thumbnail = thumbnail
    loader = ThumbnailLoader(portal)
    model = ThumbnailModel(loader)
    pages = portal.manifest("an_ua1").pages[:1]
    model.set_pages(pages)
    model.data(model.index(0), Qt.ItemDataRole.DecorationRole)
    wait_until(lambda: loader.has_failed("IMG1"))
    fail.clear()
    model.set_pages(pages)  # the register is selected again
    model.data(model.index(0), Qt.ItemDataRole.DecorationRole)
    wait_until(lambda: loader.cached("IMG1") is not None)
    assert portal.thumbnail_calls == ["IMG1", "IMG1"]
    loader.shutdown()


def test_window_flags_and_title(window):
    assert window.windowTitle() == "Antenati Downloader"
    assert window.tree.model() is window.proxy
    assert (
        window.register_view.view.selectionMode() == window.register_view.view.SelectionMode.ExtendedSelection
    )
    assert window.register_view.view.viewMode() == window.register_view.view.ViewMode.IconMode


def test_settings_dialog(qapp, tmp_path):
    from antenati_dl.gui.dialogs import SettingsDialog
    from antenati_dl.gui.settings import SIZE_MAX, AppSettings

    dlg = SettingsDialog(AppSettings(output_dir=tmp_path, workers=3))
    assert not dlg.width_spin.isEnabled()
    dlg.size_combo.setCurrentIndex(dlg.size_combo.findData(SIZE_MAX))
    dlg.workers_spin.setValue(16)
    dlg.overwrite_check.setChecked(True)
    result = dlg.result_settings()
    assert result.size_mode == SIZE_MAX and result.image_size == "max"
    assert result.workers == 16 and result.overwrite and result.output_dir == tmp_path
    dlg.deleteLater()
