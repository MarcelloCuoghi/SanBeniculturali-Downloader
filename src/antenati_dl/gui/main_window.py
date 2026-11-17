"""Main window: navigator (left), register / node details (right), download queue (bottom)."""

from __future__ import annotations

import html
import logging
from pathlib import Path
from urllib.parse import urlencode

from PySide6.QtCore import QModelIndex, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStyle,
    QToolBar,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..download import Downloader, DownloadTarget
from ..models import ARK_PREFIX, PORTAL_URL, Manifest, Page, SearchQuery
from ..portal import Portal, parse_register_ref
from .dialogs import BulkDownloadDialog, PreviewDialog, SettingsDialog
from .downloads import DownloaderFactory, DownloadJob, DownloadManager, DownloadQueueWidget, JobState
from .navigator import (
    NavigatorFilter,
    NavigatorModel,
    Node,
    NodeKind,
    resolve_fondo_query,
)
from .register_view import RegisterView, ThumbnailLoader, compact_ranges, register_title
from .settings import AppSettings
from .workers import TaskRunner

log = logging.getLogger(__name__)

PAGE_WELCOME, PAGE_NODE, PAGE_REGISTER = 0, 1, 2


def _muted(label: QLabel) -> None:
    pal = label.palette()
    pal.setColor(QPalette.ColorRole.WindowText, pal.color(QPalette.ColorRole.PlaceholderText))
    label.setPalette(pal)


class NodePane(QWidget):
    """Summary of a non-register node with the bulk download button."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind_label = QLabel()
        _muted(self.kind_label)
        self.title = QLabel()
        font = self.title.font()
        font.setPointSizeF(font.pointSizeF() * 1.5)
        font.setBold(True)
        self.title.setFont(font)
        self.title.setWordWrap(True)
        self.path = QLabel()
        self.path.setWordWrap(True)
        self.count = QLabel()
        count_font = self.count.font()
        count_font.setPointSizeF(count_font.pointSizeF() * 1.15)
        self.count.setFont(count_font)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        _muted(self.hint)

        style = self.style()
        self.download_btn = QPushButton(
            style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning),
            "Download everything under this node…",
        )
        self.download_btn.setMinimumHeight(34)
        self.browser_btn = QPushButton(
            style.standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon), "Open in browser"
        )
        self.browser_btn.setMinimumHeight(34)
        buttons = QHBoxLayout()
        buttons.addWidget(self.download_btn)
        buttons.addWidget(self.browser_btn)
        buttons.addStretch(1)

        card = QFrame()
        card.setFrameShape(QFrame.Shape.StyledPanel)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(18, 16, 18, 16)
        cl.setSpacing(8)
        cl.addWidget(self.kind_label)
        cl.addWidget(self.title)
        cl.addWidget(self.path)
        cl.addSpacing(6)
        cl.addWidget(self.count)
        cl.addSpacing(6)
        cl.addLayout(buttons)
        cl.addWidget(self.hint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(card)
        layout.addStretch(1)

    def set_node(self, node: Node) -> None:
        self.kind_label.setText(node.kind.title.upper())
        self.title.setText(node.name)
        self.path.setText(" › ".join(node.path) if node.path else "")
        self.path.setVisible(bool(node.path))
        child = {
            NodeKind.ARCHIVE: "fondi",
            NodeKind.FONDO: "series (one per comune)",
            NodeKind.SERIE: "record types",
            NodeKind.TIPOLOGIA: "years",
            NodeKind.ANNO: "registers",
        }.get(node.kind, "items")
        self.hint.setText(
            f"Expand the node in the tree to browse its {child}, then select a register to see its pages."
        )

    def set_count(self, text: str) -> None:
        self.count.setText(text)


class MainWindow(QMainWindow):
    def __init__(
        self,
        portal: Portal | None = None,
        settings: QSettings | None = None,
        downloader_factory: DownloaderFactory | None = None,
    ) -> None:
        super().__init__()
        self.portal = portal or Portal()
        self.qsettings = settings or QSettings()
        self.settings = AppSettings.load(self.qsettings)
        self.downloader_factory: DownloaderFactory = downloader_factory or Downloader
        self.setWindowTitle("Antenati Downloader")
        self.resize(1280, 800)

        self.runner = TaskRunner(4, self)
        self.thumbs = ThumbnailLoader(self.portal, concurrency=3, parent=self)
        self.manager = DownloadManager(self.portal, self.downloader_factory, parent=self)

        self.current_node: Node | None = None
        self.current_manifest: Manifest | None = None
        self._selection_gen = 0
        self._preview: PreviewDialog | None = None
        self._bulk_dialog: BulkDownloadDialog | None = None
        self._closing = False

        self._build_actions()
        self._build_ui()
        self._build_statusbar()
        self._connect()
        self._restore_state()
        self._update_actions()
        QTimer.singleShot(0, self.nav_model.load_archives)

    # ================================================================== construction
    def _std(self, sp: QStyle.StandardPixmap):
        return self.style().standardIcon(sp)

    def _build_actions(self) -> None:
        sp = QStyle.StandardPixmap
        self.act_download_selected = QAction(self._std(sp.SP_DialogSaveButton), "Download selected", self)
        self.act_download_selected.setShortcut(QKeySequence("Ctrl+D"))
        self.act_download_register = QAction(self._std(sp.SP_ArrowDown), "Download register", self)
        self.act_download_register.setShortcut(QKeySequence("Ctrl+Shift+D"))
        self.act_download_node = QAction(self._std(sp.SP_MessageBoxWarning), "Download all…", self)
        self.act_download_node.setToolTip("Download every register under the selected node")
        self.act_download_selected.setToolTip("Download the selected pages of the register (Ctrl+D)")
        self.act_download_register.setToolTip("Download every page of the register (Ctrl+Shift+D)")
        self.act_open_folder = QAction(self._std(sp.SP_DirOpenIcon), "Open folder", self)
        self.act_open_folder.setToolTip("Open the download folder (of the current register, if downloaded)")
        self.act_open_browser = QAction(self._std(sp.SP_DriveNetIcon), "Open in browser", self)
        self.act_reload = QAction(self._std(sp.SP_BrowserReload), "Reload", self)
        self.act_reload.setToolTip("Reload the list of archives")
        self.act_settings = QAction(self._std(sp.SP_FileDialogDetailedView), "Settings…", self)
        self.act_settings.setShortcut(QKeySequence("Ctrl+,"))
        self.act_quit = QAction("Quit", self)
        self.act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        self.act_about = QAction("About", self)

    def _build_ui(self) -> None:
        menu = self.menuBar()
        file_menu = menu.addMenu("&File")
        file_menu.addAction(self.act_open_folder)
        file_menu.addAction(self.act_settings)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)
        dl_menu = menu.addMenu("&Download")
        for act in (self.act_download_selected, self.act_download_register, self.act_download_node):
            dl_menu.addAction(act)
        help_menu = menu.addMenu("&Help")
        help_menu.addAction(self.act_about)

        tb = QToolBar("Main toolbar")
        tb.setObjectName("mainToolbar")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        tb.addAction(self.act_download_selected)
        tb.addAction(self.act_download_register)
        tb.addAction(self.act_download_node)
        tb.addSeparator()
        tb.addAction(self.act_open_folder)
        tb.addAction(self.act_open_browser)
        tb.addSeparator()
        tb.addAction(self.act_reload)
        tb.addAction(self.act_settings)
        self.addToolBar(tb)

        # ---- left: navigator
        self.go_edit = QLineEdit()
        self.go_edit.setPlaceholderText("Go to register: ark URL, an_ua… id or page URL")
        self.go_edit.setClearButtonEnabled(True)
        self.go_btn = QPushButton("Go")
        go_row = QHBoxLayout()
        go_row.setSpacing(4)
        go_row.addWidget(self.go_edit, 1)
        go_row.addWidget(self.go_btn)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter loaded items…")
        self.filter_edit.setClearButtonEnabled(True)
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(200)

        self.nav_model = NavigatorModel(self.portal, self.runner, self)
        self.proxy = NavigatorFilter(self)
        self.proxy.setSourceModel(self.nav_model)
        self.tree = QTreeView()
        self.tree.setModel(self.proxy)
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setAnimated(False)
        self.tree.setExpandsOnDoubleClick(True)
        # full names with horizontal scrolling instead of eliding ("Archivio di Stato di ...")
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setStretchLastSection(False)

        left = QWidget()
        left.setMinimumWidth(240)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)
        ll.addLayout(go_row)
        ll.addWidget(self.filter_edit)
        ll.addWidget(self.tree, 1)

        # ---- right: details
        self.welcome = QLabel(
            "<h2>Portale Antenati</h2>"
            "<p>Browse the archives on the left: <i>Archive › Fondo › Series › Record type › Year › "
            "Register</i>. Children are loaded when you expand a node.</p>"
            "<p>Select a register to see its pages, select the pages you need and download them. "
            "You can also paste a register link in <i>Go to register</i>.</p>"
        )
        self.welcome.setWordWrap(True)
        self.welcome.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.welcome.setContentsMargins(16, 8, 16, 8)
        self.node_pane = NodePane()
        self.register_view = RegisterView(self.thumbs)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.welcome)
        self.stack.addWidget(self.node_pane)
        self.stack.addWidget(self.register_view)

        # ---- bottom: queue
        self.queue = DownloadQueueWidget(self.manager)

        self.h_split = QSplitter(Qt.Orientation.Horizontal)
        self.h_split.setObjectName("hSplit")
        self.h_split.addWidget(left)
        self.h_split.addWidget(self.stack)
        self.h_split.setStretchFactor(0, 0)
        self.h_split.setStretchFactor(1, 1)
        self.h_split.setChildrenCollapsible(False)

        self.v_split = QSplitter(Qt.Orientation.Vertical)
        self.v_split.setObjectName("vSplit")
        self.v_split.addWidget(self.h_split)
        self.v_split.addWidget(self.queue)
        self.v_split.setStretchFactor(0, 1)
        self.v_split.setStretchFactor(1, 0)
        self.v_split.setChildrenCollapsible(False)

        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(8, 6, 8, 4)
        cl.addWidget(self.v_split)
        self.setCentralWidget(central)

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setMaximumWidth(120)
        self.busy.setMaximumHeight(14)
        self.busy.setTextVisible(False)
        self.busy.hide()
        self.downloads_label = QLabel()
        sb.addPermanentWidget(self.downloads_label)
        sb.addPermanentWidget(self.busy)
        sb.showMessage("Ready")

    def _connect(self) -> None:
        self.act_download_selected.triggered.connect(self.download_selected)
        self.act_download_register.triggered.connect(self.download_register)
        self.act_download_node.triggered.connect(self.download_node)
        self.act_open_folder.triggered.connect(self.open_download_folder)
        self.act_open_browser.triggered.connect(self.open_in_browser)
        self.act_reload.triggered.connect(lambda: self.nav_model.load_archives(refresh=True))
        self.act_settings.triggered.connect(self.edit_settings)
        self.act_quit.triggered.connect(self.close)
        self.act_about.triggered.connect(self._about)

        self.go_btn.clicked.connect(self.go_to_register)
        self.go_edit.returnPressed.connect(self.go_to_register)
        self.filter_edit.textChanged.connect(lambda _t: self._filter_timer.start())
        self._filter_timer.timeout.connect(self._apply_filter)

        self.tree.expanded.connect(self._on_expanded)
        self.tree.selectionModel().currentChanged.connect(self._on_current_changed)
        self.nav_model.status.connect(lambda msg: self.statusBar().showMessage(msg, 5000))
        self.nav_model.loadFailed.connect(self._on_load_failed)

        self.register_view.previewRequested.connect(self.preview_page)
        self.register_view.selectionChanged.connect(self._update_actions)
        self.register_view.downloadSelectedRequested.connect(self.download_selected)
        self.register_view.downloadAllRequested.connect(self.download_register)
        self.node_pane.download_btn.clicked.connect(self.download_node)
        self.node_pane.browser_btn.clicked.connect(self.open_in_browser)

        self.runner.busyChanged.connect(self.busy.setVisible)
        self.manager.activeChanged.connect(self._on_active_changed)
        self.manager.jobFinished.connect(self._on_job_finished)
        self.queue.openFolderRequested.connect(self._open_job_folder)

    # ================================================================== state
    def _default_layout(self) -> None:
        width, height = max(self.width(), 900), max(self.height(), 600)
        left = int(min(400, max(300, width / 3.2)))
        self.h_split.setSizes([left, width - left])
        self.v_split.setSizes([height - 230, 200])

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._layout_restored:
            self._layout_restored = True
            QTimer.singleShot(0, self._default_layout)

    def _restore_state(self) -> None:
        self._layout_restored = False
        geometry = self.qsettings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        state = self.qsettings.value("window/state")
        if state is not None:
            self.restoreState(state)
        for key, splitter in (("window/hsplit", self.h_split), ("window/vsplit", self.v_split)):
            value = self.qsettings.value(key)
            if value is not None and splitter.restoreState(value):
                self._layout_restored = True

    def _save_state(self) -> None:
        self.qsettings.setValue("window/geometry", self.saveGeometry())
        self.qsettings.setValue("window/state", self.saveState())
        self.qsettings.setValue("window/hsplit", self.h_split.saveState())
        self.qsettings.setValue("window/vsplit", self.v_split.saveState())
        self.settings.save(self.qsettings)

    def _update_actions(self) -> None:
        has_register = self.current_manifest is not None and self.stack.currentIndex() == PAGE_REGISTER
        has_selection = has_register and bool(self.register_view.view.selectionModel().selectedIndexes())
        bulk = self.current_node is not None and self.stack.currentIndex() == PAGE_NODE
        self.act_download_selected.setEnabled(has_selection)
        self.act_download_register.setEnabled(has_register)
        self.act_download_node.setEnabled(bulk)
        self.act_open_browser.setEnabled(has_register or self.current_node is not None)

    # ================================================================== navigation
    def _apply_filter(self) -> None:
        self.proxy.setFilterFixedString(self.filter_edit.text().strip())

    def _on_expanded(self, proxy_index: QModelIndex) -> None:
        item = self.nav_model.itemFromIndex(self.proxy.mapToSource(proxy_index))
        if item is not None:
            self.nav_model.fetch(item)

    def _on_load_failed(self, node: Node | None, message: str) -> None:
        self.statusBar().showMessage(message, 15000)
        if node is not None:
            # collapse so that expanding again retries
            for idx in self.proxy.match(
                self.proxy.index(0, 0),
                Qt.ItemDataRole.DisplayRole,
                node.label,
                -1,
                Qt.MatchFlag.MatchRecursive,
            ):
                if self.nav_model.node_at(self.proxy.mapToSource(idx)) is node:
                    self.tree.collapse(idx)
        elif self.nav_model.rowCount() == 0:
            self.welcome.setText(
                f"<h2>Portale Antenati</h2><p style='color:#b3261e'>{html.escape(message)}</p>"
                "<p>Check your connection and use <i>Reload archives</i>.</p>"
            )

    def _on_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        node = self.nav_model.node_at(self.proxy.mapToSource(current))
        if node is None or node.kind is NodeKind.PLACEHOLDER:
            return
        if node.kind is NodeKind.REGISTER:
            self.current_node = node
            self.load_register(node.payload, title=node.name)
        else:
            self.show_node(node)

    def show_node(self, node: Node) -> None:
        self._selection_gen += 1
        gen = self._selection_gen
        self.current_node = node
        self.current_manifest = None
        self.node_pane.set_node(node)
        self.stack.setCurrentIndex(PAGE_NODE)
        self._update_actions()

        def show_count(count: int | None, suffix: str = "") -> None:
            if gen != self._selection_gen:
                return
            if count is None:
                self.node_pane.set_count(f"Number of registers: unknown{suffix}")
            else:
                self.node_pane.set_count(f"<b>{count:,}</b> registers{suffix}")

        if node.count is not None:
            show_count(node.count)
            return
        self.node_pane.set_count("Counting registers…")

        def failed(exc: BaseException) -> None:
            if gen == self._selection_gen:
                self.node_pane.set_count(f"Could not count the registers: {html.escape(str(exc))}")
            self.statusBar().showMessage(f"Network error: {exc}", 10000)

        if node.kind is NodeKind.FONDO:

            def fondo_done(result) -> None:
                query, count = result
                node.extra["fondo_query"] = query
                node.extra["fondo_resolved"] = True
                node.count = count
                n_series = len(node.payload.series)
                show_count(count, f" in {n_series} series" if n_series else "")

            self.runner.submit(
                resolve_fondo_query, self.portal, node.payload, on_done=fondo_done, on_error=failed
            )
        elif node.query is not None:

            def count_done(count: int) -> None:
                node.count = count
                show_count(count)

            self.runner.submit(self.portal.count, node.query, on_done=count_done, on_error=failed)

    def load_register(self, ref, title: str = "", page_image_id: str | None = None) -> None:
        self._selection_gen += 1
        gen = self._selection_gen
        self.current_manifest = None
        self.register_view.show_loading(f"Loading {title or ref}…")
        self.stack.setCurrentIndex(PAGE_REGISTER)
        self._update_actions()
        self.statusBar().showMessage(f"Loading register {title or ref}…")

        def done(manifest: Manifest) -> None:
            if gen != self._selection_gen:
                return
            self.current_manifest = manifest
            self.register_view.set_manifest(manifest)
            self.statusBar().showMessage(
                f"{manifest.title or manifest.label}: {len(manifest.pages)} pages", 5000
            )
            if page_image_id:
                rows = [p.index for p in manifest.pages if p.image_id == page_image_id]
                if rows:
                    self.register_view.select_indices(rows)
            self._update_actions()

        def failed(exc: BaseException) -> None:
            if gen != self._selection_gen:
                return
            self.register_view.show_loading(f"Could not load the register: {exc}")
            self.statusBar().showMessage(f"Could not load the register: {exc}", 15000)
            self._update_actions()

        self.runner.submit(self.portal.manifest, ref, on_done=done, on_error=failed)

    def go_to_register(self) -> None:
        text = self.go_edit.text().strip()
        if not text:
            return
        try:
            register_id, image_id, manifest_url = parse_register_ref(text)
        except ValueError:
            QMessageBox.warning(
                self,
                "Go to register",
                f"Not a register reference:\n{text}\n\n"
                "Paste an ark URL (…/ark:/12657/an_ua…), an an_ua id, a page URL or a manifest URL.",
            )
            return
        self.current_node = None
        self.tree.selectionModel().clearCurrentIndex()
        self.tree.clearSelection()
        self.load_register(manifest_url or register_id, title=register_id or "", page_image_id=image_id)

    # ================================================================== preview
    def preview_page(self, row: int) -> None:
        manifest = self.current_manifest
        if manifest is None or not manifest.pages:
            return
        if self._preview is not None:
            self._preview.close()
        dlg = PreviewDialog(self.portal, self.runner, manifest.pages, row, register_title(manifest), self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.downloadRequested.connect(lambda page, m=manifest: self.download_pages(m, [page]))
        dlg.destroyed.connect(lambda *_: setattr(self, "_preview", None))
        self._preview = dlg
        dlg.show()

    # ================================================================== downloads
    def _register_desc(self, manifest: Manifest) -> str:
        parts = [p for p in manifest.context[1:] if p]
        if manifest.tipologia:
            parts.append(manifest.tipologia)
        parts.append(manifest.title or manifest.register_id or manifest.label)
        return " › ".join(parts)

    def _enqueue(self, description: str, targets: list[DownloadTarget]) -> DownloadJob:
        job = self.manager.enqueue(description, targets, self.settings.download_options())
        self.statusBar().showMessage(f"Queued: {description}", 5000)
        return job

    def download_pages(self, manifest: Manifest, pages: list[Page]) -> DownloadJob | None:
        if not pages:
            return None
        indices = [p.index for p in pages]
        label = "page" if len(indices) == 1 else "pages"
        desc = f"{self._register_desc(manifest)} - {label} {compact_ranges(indices)}"
        return self._enqueue(desc, [DownloadTarget(register=manifest, pages=indices)])

    def download_selected(self) -> None:
        manifest = self.current_manifest
        if manifest is None:
            return
        pages = self.register_view.selected_pages()
        if not pages and self.register_view.range_edit.text().strip() and self.register_view.apply_range():
            pages = self.register_view.selected_pages()
        if not pages:
            self.statusBar().showMessage("Select some pages first (click, Ctrl+click, Shift+click).", 5000)
            return
        self.download_pages(manifest, pages)

    def download_register(self) -> None:
        manifest = self.current_manifest
        if manifest is None:
            return
        desc = f"{self._register_desc(manifest)} - all {len(manifest.pages)} pages"
        self._enqueue(desc, [DownloadTarget(register=manifest)])

    def node_targets(self, node: Node) -> list[DownloadTarget]:
        if node.kind is NodeKind.FONDO:
            query = node.extra.get("fondo_query")
            if query is not None:
                return [DownloadTarget.from_query(query)]
            return [DownloadTarget.from_query(SearchQuery(serie=s.id)) for s in node.payload.series]
        if node.query is not None:
            return [DownloadTarget.from_query(node.query)]
        return []

    def download_node(self) -> None:
        node = self.current_node
        if node is None or node.kind in (NodeKind.REGISTER, NodeKind.PLACEHOLDER):
            return
        if node.kind is NodeKind.FONDO and not node.extra.get("fondo_resolved"):
            self.statusBar().showMessage("Still resolving the fondo, try again in a moment…", 5000)
            return
        targets = self.node_targets(node)
        if not targets:
            QMessageBox.information(self, "Download everything", "Nothing to download under this node.")
            return
        description = f"Everything under {' › '.join(node.full_path)}"
        options = self.settings.download_options()
        factory, portal = self.downloader_factory, self.portal

        def estimate():
            if len(targets) > 5:  # fondo resolved to one query per series: too many requests
                return f"{len(targets)} series - size not estimated (too many to sample)"
            return factory(portal, options).estimate(targets, sample=3)

        dlg = BulkDownloadDialog(self.runner, description, estimate, options, self)
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.accepted.connect(lambda: self._enqueue(description, targets))
        dlg.destroyed.connect(lambda *_: setattr(self, "_bulk_dialog", None))
        self._bulk_dialog = dlg
        dlg.open()

    def _on_active_changed(self, active: int) -> None:
        running = self.manager.running_count()
        if active:
            self.downloads_label.setText(f"Downloads: {running} running, {active - running} queued")
        else:
            self.downloads_label.setText("")

    def _on_job_finished(self, job: DownloadJob) -> None:
        if self._closing:
            return
        msg = f"{job.state.value}: {job.description}" + (f" - {job.message}" if job.message else "")
        self.statusBar().showMessage(msg, 15000)
        QApplication.alert(self)
        if job.state in (JobState.FAILED, JobState.DONE_WITH_ERRORS):
            box = QMessageBox(
                QMessageBox.Icon.Warning,
                "Download problem",
                f"{job.description}\n\n{job.state.value}: {job.message}",
                QMessageBox.StandardButton.Ok,
                self,
            )
            if job.errors:
                box.setDetailedText("\n".join(job.errors[-50:]))
            box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            box.open()  # non-blocking

    # ================================================================== folders / browser
    def _open_path(self, path: Path) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Open folder", f"Cannot create {path}:\n{exc}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_download_folder(self) -> None:
        root = Path(self.settings.output_dir)
        if self.current_manifest is not None and self.stack.currentIndex() == PAGE_REGISTER:
            folder = Downloader(self.portal, self.settings.download_options()).register_dir(
                self.current_manifest
            )
            if folder.is_dir():
                self._open_path(folder)
                return
        self._open_path(root)

    def _open_job_folder(self, job: DownloadJob | None) -> None:
        if job is None:
            return
        folder = job.folder
        self._open_path(folder if folder.exists() else Path(job.options.output_dir))

    def current_url(self) -> str | None:
        if self.stack.currentIndex() == PAGE_REGISTER and self.current_manifest is not None:
            rid = self.current_manifest.register_id
            return f"{ARK_PREFIX}{rid}/" if rid else self.current_manifest.url
        node = self.current_node
        if node is None:
            return None
        if node.kind in (NodeKind.ARCHIVE, NodeKind.FONDO):
            return f"{PORTAL_URL}/archive/?archivio={node.archive_id}"
        if node.kind is NodeKind.REGISTER and node.register is not None:
            return node.register.url
        if node.query is not None:
            return f"{PORTAL_URL}/search-registry/?{urlencode(node.query.to_params(1, 20))}"
        return None

    def open_in_browser(self) -> None:
        url = self.current_url()
        if url:
            QDesktopServices.openUrl(QUrl(url))

    # ================================================================== settings / about
    def edit_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec() == SettingsDialog.DialogCode.Accepted:
            self.settings = dlg.result_settings()
            self.settings.save(self.qsettings)
            self.statusBar().showMessage(
                f"Settings saved - downloads go to {self.settings.output_dir} "
                f"({self.settings.describe_size()}, {self.settings.workers} workers)",
                8000,
            )

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "About antenati-dl",
            f"<b>antenati-dl {__version__}</b><p>Browse and download registers from the "
            f"<a href='{PORTAL_URL}'>Portale Antenati</a>.</p>"
            "<p>Images are provided by the Italian Ministry of Culture; please respect the portal's "
            "terms of use and keep the load on the service reasonable.</p>",
        )

    # ================================================================== closing
    def closeEvent(self, event: QCloseEvent) -> None:
        if self.manager.has_active():
            answer = QMessageBox.question(
                self,
                "Downloads in progress",
                "Some downloads are still running or queued.\n\n"
                "Quit anyway? They will be cancelled (already downloaded pages are kept and the "
                "download can be resumed later).",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._closing = True
        self._save_state()
        if self.manager.has_active():
            self.manager.cancel_all()
            self.statusBar().showMessage("Cancelling downloads…")
            QApplication.processEvents()
            if not self.manager.wait(30000):
                log.warning("Download threads did not stop in time")
        if self._preview is not None:
            self._preview.close()
        self.thumbs.shutdown(2000)
        self.runner.shutdown(3000)
        event.accept()
