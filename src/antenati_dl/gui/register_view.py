"""Register pane: metadata header + thumbnail grid of its pages.

Thumbnails are requested by the model only when the view asks for an item's decoration (i.e. when
the item is painted, hence visible). Requests are queued in :class:`ThumbnailLoader`, which runs at
most ``concurrency`` downloads at a time and drops queued requests whose item scrolled out of view.
"""

from __future__ import annotations

import html
from collections import OrderedDict, deque
from collections.abc import Callable

from PySide6.QtCore import (
    QAbstractListModel,
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from ..models import Manifest, Page
from ..utils import parse_page_ranges
from .workers import TaskRunner

THUMB_HEIGHT = 170
ICON_SIZE = QSize(150, 170)
GRID_SIZE = QSize(176, 212)


def compact_ranges(indices: list[int]) -> str:
    """[1, 2, 3, 5, 7, 8] -> "1-3,5,7-8"."""
    out: list[str] = []
    nums = sorted(set(indices))
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        out.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ",".join(out)


def _placeholder_pixmap(text: str, broken: bool = False) -> QPixmap:
    pm = QPixmap(ICON_SIZE.width() - 30, ICON_SIZE.height() - 20)
    pm.fill(QColor("#eceae6") if not broken else QColor("#f3e1df"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor("#c9c4bb") if not broken else QColor("#d49a94"), 2))
    p.drawRect(pm.rect().adjusted(1, 1, -2, -2))
    font = QFont()
    font.setPointSize(9)
    p.setFont(font)
    p.setPen(QColor("#8a857c") if not broken else QColor("#a5463d"))
    p.drawText(QRect(0, 0, pm.width(), pm.height()), Qt.AlignmentFlag.AlignCenter, text)
    p.end()
    return pm


class ThumbnailLoader(QObject):
    """Bounded, cancellable thumbnail fetcher with an in-memory LRU cache of decoded images."""

    loaded = Signal(str)  # image_id
    failed = Signal(str)  # image_id

    def __init__(
        self,
        portal,
        concurrency: int = 3,
        height: int = THUMB_HEIGHT,
        cache_size: int = 1500,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.portal = portal
        self.height = height
        self.cache_size = cache_size
        self.concurrency = max(1, min(4, concurrency))
        self.runner = TaskRunner(self.concurrency, self)
        self._cache: OrderedDict[str, QImage] = OrderedDict()
        self._queue: deque[Page] = deque()
        self._queued: set[str] = set()
        self._in_flight: set[str] = set()
        self._failed: set[str] = set()
        #: optional predicate; queued pages for which it returns False are dropped before fetching
        self.wanted: Callable[[Page], bool] | None = None

    # ------------------------------------------------------------------ cache
    def cached(self, image_id: str) -> QImage | None:
        img = self._cache.get(image_id)
        if img is not None:
            self._cache.move_to_end(image_id)
        return img

    def has_failed(self, image_id: str) -> bool:
        return image_id in self._failed

    @property
    def in_flight(self) -> int:
        return len(self._in_flight)

    @property
    def queued(self) -> int:
        return len(self._queue)

    # ------------------------------------------------------------------ requests
    def request(self, page: Page) -> None:
        iid = page.image_id
        if iid in self._cache or iid in self._queued or iid in self._in_flight or iid in self._failed:
            return
        self._queue.append(page)
        self._queued.add(iid)
        self._pump()

    def clear_queue(self) -> None:
        self._queue.clear()
        self._queued.clear()

    def retry_failed(self) -> None:
        self._failed.clear()

    def _pump(self) -> None:
        while self._queue and len(self._in_flight) < self.concurrency:
            page = self._queue.popleft()
            self._queued.discard(page.image_id)
            if self.wanted is not None and not self.wanted(page):
                continue  # scrolled away; it will be requested again when painted
            self._in_flight.add(page.image_id)
            self.runner.submit(
                self._fetch,
                page,
                on_done=lambda img, p=page: self._done(p, img),
                on_error=lambda exc, p=page: self._error(p, exc),
                track_busy=False,
            )

    def _fetch(self, page: Page) -> QImage:  # worker thread (QImage is thread-safe, QPixmap is not)
        data = self.portal.thumbnail(page, self.height)
        img = QImage.fromData(data)
        if img.isNull():
            raise ValueError("invalid image data")
        return img

    def _done(self, page: Page, img: QImage) -> None:
        self._in_flight.discard(page.image_id)
        self._cache[page.image_id] = img
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)
        self.loaded.emit(page.image_id)
        self._pump()

    def _error(self, page: Page, _exc: BaseException) -> None:
        self._in_flight.discard(page.image_id)
        self._failed.add(page.image_id)
        self.failed.emit(page.image_id)
        self._pump()

    def shutdown(self, timeout_ms: int = 2000) -> bool:
        self.clear_queue()
        return self.runner.shutdown(timeout_ms)


class ThumbnailModel(QAbstractListModel):
    PageRole = Qt.ItemDataRole.UserRole + 10

    def __init__(self, loader: ThumbnailLoader, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.loader = loader
        self.pages: list[Page] = []
        self._rows: dict[str, int] = {}
        self._pixmaps: dict[str, QPixmap] = {}
        self._loading = _placeholder_pixmap("loading…")
        self._broken = _placeholder_pixmap("not available", broken=True)
        loader.loaded.connect(self._on_changed)
        loader.failed.connect(self._on_changed)

    def set_pages(self, pages: list[Page]) -> None:
        self.beginResetModel()
        self.loader.clear_queue()
        self.loader.retry_failed()  # a thumbnail that failed once (portal glitch) is retried on reload
        self.pages = list(pages)
        self._rows = {p.image_id: i for i, p in enumerate(self.pages)}
        self._pixmaps = {}
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.pages)

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.pages):
            return None
        page = self.pages[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return page.label or str(page.index)
        if role == Qt.ItemDataRole.DecorationRole:
            return self._pixmap_for(page)
        if role == Qt.ItemDataRole.ToolTipRole:
            return f"Page {page.index}: {page.label}\n{page.image_id}\nDouble-click to preview"
        if role == self.PageRole:
            return page
        return None

    def _pixmap_for(self, page: Page) -> QPixmap:
        pm = self._pixmaps.get(page.image_id)
        if pm is not None:
            return pm
        img = self.loader.cached(page.image_id)
        if img is not None:
            if img.width() > ICON_SIZE.width() or img.height() > ICON_SIZE.height():
                img = img.scaled(
                    ICON_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                )
            pm = QPixmap.fromImage(img)
            self._pixmaps[page.image_id] = pm
            return pm
        if self.loader.has_failed(page.image_id):
            return self._broken
        self.loader.request(page)  # lazy: only items being painted get here
        return self._loading

    def _on_changed(self, image_id: str) -> None:
        row = self._rows.get(image_id)
        if row is not None:
            idx = self.index(row)
            self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])

    def page_at(self, row: int) -> Page:
        return self.pages[row]


class ThumbnailDelegate(QStyledItemDelegate):
    """Fixed-size cell: thumbnail centred in a box, page label below, rounded selection frame."""

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        return GRID_SIZE

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = option.palette
        cell = option.rect.adjusted(4, 4, -4, -4)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        highlight = pal.color(QPalette.ColorRole.Highlight)
        if selected or hover:
            fill = QColor(highlight)
            fill.setAlpha(70 if selected else 25)
            painter.setBrush(fill)
            painter.setPen(QPen(highlight, 2) if selected else Qt.PenStyle.NoPen)
            painter.drawRoundedRect(cell, 6, 6)

        box = QRect(cell.x() + 4, cell.y() + 5, cell.width() - 8, ICON_SIZE.height())
        pm = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(pm, QPixmap) and not pm.isNull():
            size = pm.size()
            if size.width() > box.width() or size.height() > box.height():
                size = size.scaled(box.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRect(0, 0, size.width(), size.height())
            target.moveCenter(box.center())
            target.moveBottom(box.bottom())
            painter.drawPixmap(target, pm)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(0, 0, 0, 50), 1))
            painter.drawRect(target.adjusted(0, 0, -1, -1))

        text_rect = QRect(cell.x() + 2, box.bottom() + 4, cell.width() - 4, cell.bottom() - box.bottom() - 4)
        font = QFont(option.font)
        font.setBold(selected)
        painter.setFont(font)
        painter.setPen(pal.color(QPalette.ColorRole.Text))
        text = QFontMetrics(font).elidedText(
            str(index.data() or ""), Qt.TextElideMode.ElideRight, text_rect.width()
        )
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, text)
        painter.restore()


def register_title(manifest: Manifest) -> str:
    """Short human title, e.g. "Acquaro · Nati · 1911"."""
    parts: list[str] = []
    for part in (
        manifest.context[-1] if len(manifest.context) > 1 else "",
        manifest.tipologia,
        manifest.title,
    ):
        if part and part not in parts:
            parts.append(part)
    return " · ".join(parts) or manifest.label or (manifest.register_id or "")


class ThumbnailListView(QListView):
    """Icon-mode list whose grid cells stretch so that the columns fill the available width."""

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_grid()

    def _fit_grid(self) -> None:
        # assume a vertical scrollbar is always there, so that the layout cannot oscillate
        bar = self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        avail = self.width() - 2 * self.frameWidth() - bar - 2
        cols = max(1, avail // GRID_SIZE.width())
        width = max(GRID_SIZE.width(), avail // cols)
        if self.gridSize().width() != width:
            self.setGridSize(QSize(width, GRID_SIZE.height()))


class RegisterView(QWidget):
    """Header with the register metadata + selectable thumbnail grid."""

    previewRequested = Signal(int)  # row
    selectionChanged = Signal()
    downloadSelectedRequested = Signal()
    downloadAllRequested = Signal()

    def __init__(self, loader: ThumbnailLoader, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.manifest: Manifest | None = None

        self.title = QLabel()
        title_font = self.title.font()
        title_font.setPointSizeF(title_font.pointSizeF() * 1.35)
        title_font.setBold(True)
        self.title.setFont(title_font)
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.RichText)
        self.details.setOpenExternalLinks(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)

        header = QFrame()
        header.setObjectName("registerHeader")
        header.setFrameShape(QFrame.Shape.StyledPanel)
        hl = QVBoxLayout(header)
        hl.setContentsMargins(12, 10, 12, 10)
        hl.setSpacing(4)
        hl.addWidget(self.title)
        hl.addWidget(self.details)

        # selection bar
        self.select_all_btn = QPushButton("Select all")
        self.select_none_btn = QPushButton("Select none")
        self.range_edit = QLineEdit()
        self.range_edit.setPlaceholderText("Pages, e.g. 1-10,15")
        self.range_edit.setClearButtonEnabled(True)
        self.range_edit.setMaximumWidth(200)
        self.range_btn = QPushButton("Select range")
        self.range_edit.setMinimumWidth(120)
        self.selection_label = QLabel()
        self.selection_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.download_selected_btn = QPushButton("Download selected")
        self.download_all_btn = QPushButton("Download register")
        style = self.style()
        self.download_selected_btn.setIcon(style.standardIcon(style.StandardPixmap.SP_DialogSaveButton))
        self.download_all_btn.setIcon(style.standardIcon(style.StandardPixmap.SP_ArrowDown))

        bar = QHBoxLayout()
        bar.setSpacing(6)
        for w in (self.select_all_btn, self.select_none_btn, self.range_edit, self.range_btn):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.download_selected_btn)
        bar.addWidget(self.download_all_btn)

        self.model = ThumbnailModel(loader, self)
        self.view = ThumbnailListView()
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setMovement(QListView.Movement.Static)
        self.view.setUniformItemSizes(True)
        self.view.setIconSize(ICON_SIZE)
        self.view.setGridSize(GRID_SIZE)
        self.view.setItemDelegate(ThumbnailDelegate(self.view))
        self.view.setMouseTracking(True)
        self.view.setLayoutMode(QListView.LayoutMode.Batched)
        self.view.setBatchSize(200)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.setModel(self.model)
        loader.wanted = self._is_visible

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(header)
        layout.addLayout(bar)
        layout.addWidget(self.selection_label)
        layout.addWidget(self.view, 1)

        self.select_all_btn.clicked.connect(self.view.selectAll)
        self.select_none_btn.clicked.connect(self.view.clearSelection)
        self.range_btn.clicked.connect(self.apply_range)
        self.range_edit.returnPressed.connect(self.apply_range)
        self.view.doubleClicked.connect(lambda idx: self.previewRequested.emit(idx.row()))
        self.view.selectionModel().selectionChanged.connect(self._selection_changed)
        self.download_selected_btn.clicked.connect(self.downloadSelectedRequested)
        self.download_all_btn.clicked.connect(self.downloadAllRequested)
        self.show_loading("")

    # ------------------------------------------------------------------ content
    def show_loading(self, text: str) -> None:
        self.manifest = None
        self.title.setText(text or "No register selected")
        self.details.setText("")
        self.model.set_pages([])
        self._update_controls()

    def set_manifest(self, manifest: Manifest) -> None:
        self.manifest = manifest
        self.title.setText(register_title(manifest))
        rows: list[tuple[str, str]] = []
        if manifest.tipologia:
            rows.append(("Record type", manifest.tipologia))
        if manifest.context:
            rows.append(("Context", " › ".join(manifest.context)))
        if manifest.dating:
            rows.append(("Dating", manifest.dating))
        rows.append(("Pages", str(len(manifest.pages))))
        if manifest.register_id:
            url = f"https://antenati.cultura.gov.it/ark:/12657/{manifest.register_id}/"
            rows.append(("Portal", f'<a href="{html.escape(url)}">{html.escape(manifest.register_id)}</a>'))
        muted = self.palette().color(QPalette.ColorRole.PlaceholderText).name()
        body = "".join(
            f"<tr><td style='color:{muted};padding-right:12px'>{html.escape(k)}</td>"
            f"<td>{v if k == 'Portal' else html.escape(v)}</td></tr>"
            for k, v in rows
        )
        self.details.setText(f"<table cellspacing='0' cellpadding='1'>{body}</table>")
        self.range_edit.clear()
        self.model.set_pages(manifest.pages)
        self.view.scrollToTop()
        self._update_controls()

    # ------------------------------------------------------------------ selection
    def selected_pages(self) -> list[Page]:
        rows = sorted(i.row() for i in self.view.selectionModel().selectedIndexes())
        return [self.model.page_at(r) for r in rows]

    def select_indices(self, indices: list[int]) -> None:
        """Select pages by 1-based index (replacing the current selection)."""
        selection = QItemSelection()
        for part in compact_ranges(indices).split(","):
            if not part:
                continue
            lo, _, hi = part.partition("-")
            selection.select(self.model.index(int(lo) - 1), self.model.index(int(hi or lo) - 1))
        sm = self.view.selectionModel()
        sm.select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        if indices:
            self.view.scrollTo(self.model.index(min(indices) - 1))

    def apply_range(self) -> bool:
        text = self.range_edit.text().strip()
        if not self.manifest or not text:
            return False
        try:
            indices = parse_page_ranges(text, len(self.manifest.pages))
        except ValueError as exc:
            self.selection_label.setText(f"<span style='color:#b3261e'>{html.escape(str(exc))}</span>")
            return False
        self.select_indices(indices)
        return True

    def _selection_changed(self, *_args) -> None:
        self._update_controls()
        self.selectionChanged.emit()

    def _update_controls(self) -> None:
        total = len(self.model.pages)
        selected = len(self.view.selectionModel().selectedIndexes()) if total else 0
        has = self.manifest is not None and total > 0
        for w in (self.select_all_btn, self.select_none_btn, self.range_edit, self.range_btn):
            w.setEnabled(has)
        self.download_all_btn.setEnabled(has)
        self.download_selected_btn.setEnabled(has and selected > 0)
        if has:
            pages = self.selected_pages()
            sel = f" ({compact_ranges([p.index for p in pages])})" if 0 < selected <= 400 else ""
            self.selection_label.setText(f"{selected} of {total} pages selected{sel}")
        else:
            self.selection_label.setText("")

    # ------------------------------------------------------------------ lazy loading
    def _is_visible(self, page: Page) -> bool:
        row = page.index - 1
        if row < 0 or row >= len(self.model.pages) or self.model.pages[row].image_id != page.image_id:
            row = next((i for i, p in enumerate(self.model.pages) if p.image_id == page.image_id), -1)
            if row < 0:
                return False
        rect = self.view.visualRect(self.model.index(row))
        return rect.isValid() and rect.intersects(self.view.viewport().rect())
