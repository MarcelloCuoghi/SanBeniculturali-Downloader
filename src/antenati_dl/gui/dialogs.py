"""Settings, page preview and bulk-download confirmation dialogs."""

from __future__ import annotations

import html
import importlib.util
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from ..download import DownloadOptions
from ..models import Page
from .settings import SIZE_CUSTOM, SIZE_FULL, SIZE_MAX, AppSettings
from .workers import TaskRunner

PREVIEW_WIDTH = 1600


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(520)

        self.output_edit = QLineEdit(str(settings.output_dir))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        out_row = QHBoxLayout()
        out_row.addWidget(self.output_edit, 1)
        out_row.addWidget(browse)

        self.size_combo = QComboBox()
        self.size_combo.addItem("Original resolution (largest files)", SIZE_FULL)
        self.size_combo.addItem("Max (server-capped, ~10 megapixels)", SIZE_MAX)
        self.size_combo.addItem("Custom width…", SIZE_CUSTOM)
        self.size_combo.setCurrentIndex(max(0, self.size_combo.findData(settings.size_mode)))
        self.width_spin = QSpinBox()
        self.width_spin.setRange(100, 20000)
        self.width_spin.setSingleStep(100)
        self.width_spin.setSuffix(" px")
        self.width_spin.setValue(settings.custom_width)
        size_row = QHBoxLayout()
        size_row.addWidget(self.size_combo, 1)
        size_row.addWidget(self.width_spin)
        self.size_combo.currentIndexChanged.connect(self._update_width)
        self._update_width()

        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 16)
        self.workers_spin.setValue(settings.workers)
        self.workers_spin.setToolTip("Parallel page downloads. Please be gentle with the public portal.")

        self.overwrite_check = QCheckBox("Download again pages already on disk")
        self.overwrite_check.setChecked(settings.overwrite)
        self.pdf_check = QCheckBox("Also build a PDF of each register")
        self.pdf_check.setChecked(settings.pdf)
        if importlib.util.find_spec("img2pdf") is None:
            self.pdf_check.setToolTip("Requires the optional 'img2pdf' package (pip install img2pdf)")
            self.pdf_check.setText(self.pdf_check.text() + "  (img2pdf not installed)")

        form = QFormLayout()
        form.addRow("Download folder:", out_row)
        form.addRow("Image size:", size_row)
        form.addRow("Parallel downloads:", self.workers_spin)
        form.addRow("", self.overwrite_check)
        form.addRow("", self.pdf_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Download folder", self.output_edit.text())
        if chosen:
            self.output_edit.setText(chosen)

    def _update_width(self) -> None:
        self.width_spin.setEnabled(self.size_combo.currentData() == SIZE_CUSTOM)

    def result_settings(self) -> AppSettings:
        return AppSettings(
            output_dir=Path(self.output_edit.text().strip() or ".").expanduser(),
            size_mode=self.size_combo.currentData(),
            custom_width=self.width_spin.value(),
            workers=self.workers_spin.value(),
            overwrite=self.overwrite_check.isChecked(),
            pdf=self.pdf_check.isChecked(),
        )


class PreviewDialog(QDialog):
    """Larger view of one page with previous/next navigation."""

    downloadRequested = Signal(object)  # Page

    _cache: OrderedDict[str, QImage] = OrderedDict()  # shared, small LRU
    CACHE_SIZE = 12

    def __init__(
        self,
        portal,
        runner: TaskRunner,
        pages: list[Page],
        row: int,
        title: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.portal = portal
        self.runner = runner
        self.pages = pages
        self.row = row
        self.title = title
        self._generation = 0
        self._image: QImage | None = None
        self.setWindowTitle("Page preview")
        self.resize(1000, 800)

        self.image_label = QLabel("Loading…")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.image_label)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)

        style = self.style()
        sp = QStyle.StandardPixmap
        self.prev_btn = QPushButton(style.standardIcon(sp.SP_ArrowBack), "Previous")
        self.next_btn = QPushButton(style.standardIcon(sp.SP_ArrowForward), "Next")
        self.info = QLabel()
        self.info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.download_btn = QPushButton(style.standardIcon(sp.SP_DialogSaveButton), "Download this page")
        close_btn = QPushButton("Close")

        bar = QHBoxLayout()
        bar.addWidget(self.prev_btn)
        bar.addWidget(self.next_btn)
        bar.addWidget(self.info, 1)
        bar.addWidget(self.download_btn)
        bar.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self.scroll, 1)
        layout.addLayout(bar)

        self.prev_btn.clicked.connect(lambda: self.show_row(self.row - 1))
        self.next_btn.clicked.connect(lambda: self.show_row(self.row + 1))
        self.download_btn.clicked.connect(lambda: self.downloadRequested.emit(self.pages[self.row]))
        close_btn.clicked.connect(self.close)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=lambda: self.show_row(self.row - 1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=lambda: self.show_row(self.row + 1))
        self.show_row(row)

    @property
    def page(self) -> Page:
        return self.pages[self.row]

    def show_row(self, row: int) -> None:
        if not self.pages or row < 0 or row >= len(self.pages):
            return
        self.row = row
        page = self.pages[row]
        self.prev_btn.setEnabled(row > 0)
        self.next_btn.setEnabled(row < len(self.pages) - 1)
        prefix = f"{self.title} - " if self.title else ""
        self.info.setText(f"{prefix}page {page.index} of {len(self.pages)}  ({html.escape(page.label)})")
        self._generation += 1
        cached = self._cache.get(page.image_id)
        if cached is not None:
            self._cache.move_to_end(page.image_id)
            self._set_image(cached)
            return
        self._image = None
        self.image_label.setPixmap(QPixmap())
        self.image_label.setText("Loading…")
        gen = self._generation
        self.runner.submit(
            self._fetch,
            page,
            on_done=lambda img, g=gen, iid=page.image_id: self._loaded(g, iid, img),
            on_error=lambda exc, g=gen: self._failed(g, exc),
        )

    def _fetch(self, page: Page) -> QImage:  # worker thread
        data = self.portal.http.get_image(page.image_url(PREVIEW_WIDTH))
        img = QImage.fromData(data)
        if img.isNull():
            raise ValueError("invalid image data")
        return img

    def _loaded(self, gen: int, image_id: str, img: QImage) -> None:
        self._cache[image_id] = img
        while len(self._cache) > self.CACHE_SIZE:
            self._cache.popitem(last=False)
        if gen == self._generation:
            self._set_image(img)

    def _failed(self, gen: int, exc: BaseException) -> None:
        if gen == self._generation:
            self.image_label.setText(f"Could not load the image:\n{exc}")

    def _set_image(self, img: QImage) -> None:
        self._image = img
        self.image_label.setText("")
        self._rescale()

    def _rescale(self) -> None:
        if self._image is None:
            return
        area = self.scroll.viewport().size()
        pm = QPixmap.fromImage(self._image)
        if pm.width() > area.width() or pm.height() > area.height():
            pm = pm.scaled(
                area, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        self.image_label.setPixmap(pm)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._rescale()


class BulkDownloadDialog(QDialog):
    """Confirmation for "download everything under this node", with a background size estimate."""

    def __init__(
        self,
        runner: TaskRunner,
        description: str,
        estimate_fn,
        options: DownloadOptions,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Download everything")
        self.setMinimumWidth(560)
        self._closed = False

        icon = QLabel()
        icon.setPixmap(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning).pixmap(48, 48))
        icon.setAlignment(Qt.AlignmentFlag.AlignTop)

        what = QLabel(f"<b>{html.escape(description)}</b>")
        what.setWordWrap(True)
        self.estimate_label = QLabel("Estimating the size (sampling a few registers)…")
        self.estimate_label.setWordWrap(True)
        warning = QLabel(
            "<p style='color:#b3261e'><b>Warning:</b> this can be a very large download - whole "
            "archives contain tens of thousands of registers, millions of pages and many hundreds "
            "of gigabytes. It will take a long time and put load on a public service.</p>"
            "<p>Pages already on disk are skipped, so an interrupted download can be resumed by "
            "starting it again. Images are saved under:<br>"
            f"<code>{html.escape(str(options.output_dir))}</code> "
            f"(size: {html.escape(str(options.size))})</p>"
        )
        warning.setWordWrap(True)
        warning.setTextFormat(Qt.TextFormat.RichText)

        text = QVBoxLayout()
        text.addWidget(what)
        text.addWidget(self.estimate_label)
        text.addWidget(warning)
        top = QHBoxLayout()
        top.addWidget(icon)
        top.addLayout(text, 1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.start_btn = self.buttons.addButton("Start download", QDialogButtonBox.ButtonRole.AcceptRole)
        self.start_btn.setEnabled(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.buttons)
        self.finished.connect(self._mark_closed)

        runner.submit(estimate_fn, on_done=self._estimated, on_error=self._estimate_failed)

    def _mark_closed(self, *_args) -> None:
        self._closed = True

    def _estimated(self, estimate) -> None:
        if self._closed:
            return
        text = estimate.describe() if hasattr(estimate, "describe") else str(estimate)
        self.estimate_label.setText(f"Estimated size: <b>{html.escape(text)}</b>")
        self.start_btn.setEnabled(True)

    def _estimate_failed(self, exc: BaseException) -> None:
        if self._closed:
            return
        self.estimate_label.setText(f"Could not estimate the size: {html.escape(str(exc))}")
        self.start_btn.setEnabled(True)
