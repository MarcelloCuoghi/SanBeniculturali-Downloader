"""Download queue.

Jobs are executed one at a time (sequential queue) in a dedicated :class:`QThread`; inside the
thread :meth:`Downloader.run` fetches pages with its own worker pool. Progress events are emitted
from the job thread through Qt signals, hence delivered (queued) to the GUI thread.
"""

from __future__ import annotations

import itertools
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QAbstractTableModel,
    QElapsedTimer,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
    QThread,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionProgressBar,
    QStyleOptionViewItem,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..download import Downloader, DownloadOptions, DownloadTarget
from ..models import DownloadReport, EventKind, ProgressEvent
from ..utils import format_bytes

#: ``factory(portal, options, on_event=..., cancel_event=...)`` -> object with ``run(targets)``
DownloaderFactory = Callable[..., Any]


class JobState(str, Enum):
    QUEUED = "Queued"
    RUNNING = "Running"
    CANCELLING = "Cancelling…"
    DONE = "Done"
    DONE_WITH_ERRORS = "Done with errors"
    FAILED = "Failed"
    CANCELLED = "Cancelled"

    @property
    def active(self) -> bool:
        return self in (JobState.QUEUED, JobState.RUNNING, JobState.CANCELLING)

    @property
    def finished(self) -> bool:
        return not self.active


@dataclass
class DownloadJob:
    id: int
    description: str
    targets: list[DownloadTarget]
    options: DownloadOptions
    state: JobState = JobState.QUEUED
    current_register: str = ""
    pages_done: int = 0
    pages_total: int = 0
    registers_done: int = 0
    registers_failed: int = 0
    registers_total: int = 0
    bytes: int = 0
    message: str = ""
    errors: list[str] = field(default_factory=list)
    last_dir: str | None = None
    report: DownloadReport | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    @property
    def folder(self) -> Path:
        return Path(self.last_dir) if self.last_dir else Path(self.options.output_dir)

    def apply(self, ev: ProgressEvent) -> None:
        if ev.register_total:
            self.registers_total = ev.register_total
        if ev.register is not None:
            self.current_register = ev.register.title or ev.register.label
        if ev.kind is EventKind.REGISTER_START:
            self.pages_done = 0
            self.pages_total = ev.pages_total
            if ev.path:
                self.last_dir = ev.path
        elif ev.kind in (EventKind.PAGE_DONE, EventKind.PAGE_SKIPPED, EventKind.PAGE_FAILED):
            self.pages_done = ev.pages_done
            self.pages_total = ev.pages_total or self.pages_total
            if ev.kind is EventKind.PAGE_DONE:
                self.bytes += ev.bytes
        elif ev.kind is EventKind.REGISTER_DONE:
            self.registers_done += 1
            self.pages_done = ev.pages_done or self.pages_done
        elif ev.kind is EventKind.REGISTER_FAILED:
            self.registers_failed += 1
        if ev.error:
            self.errors.append(ev.message or ev.error)
        if ev.kind in (EventKind.PAGE_FAILED, EventKind.REGISTER_FAILED, EventKind.LOG):
            self.message = ev.message or ev.error or ""


class DownloadThread(QThread):
    progress = Signal(int, object)  # job id, ProgressEvent
    completed = Signal(int, object, object)  # job id, DownloadReport | None, exception | None

    def __init__(self, job: DownloadJob, portal, factory: DownloaderFactory, parent: QObject | None = None):
        super().__init__(parent)
        self.job = job
        self.portal = portal
        self.factory = factory

    def run(self) -> None:  # job thread
        job_id = self.job.id
        try:
            downloader = self.factory(
                self.portal,
                self.job.options,
                on_event=lambda ev: self.progress.emit(job_id, ev),
                cancel_event=self.job.cancel_event,
            )
            report = downloader.run(self.job.targets)
        except BaseException as exc:  # noqa: BLE001 - reported to the GUI thread
            self.completed.emit(job_id, None, exc)
            return
        self.completed.emit(job_id, report, None)


class DownloadManager(QObject):
    """Owns the jobs; runs up to ``max_parallel`` of them at once (default: sequential)."""

    jobAdded = Signal(int)  # row
    jobChanged = Signal(int)  # row
    jobFinished = Signal(object)  # DownloadJob
    activeChanged = Signal(int)  # number of active (queued/running) jobs

    def __init__(
        self,
        portal,
        factory: DownloaderFactory | None = None,
        max_parallel: int = 1,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.portal = portal
        self.factory: DownloaderFactory = factory or Downloader
        self.max_parallel = max(1, max_parallel)
        self.jobs: list[DownloadJob] = []
        self._threads: dict[int, DownloadThread] = {}
        self._ids = itertools.count(1)
        self._throttle: dict[int, QElapsedTimer] = {}

    # ------------------------------------------------------------------ queries
    def row_of(self, job_id: int) -> int:
        for row, job in enumerate(self.jobs):
            if job.id == job_id:
                return row
        return -1

    def job(self, job_id: int) -> DownloadJob | None:
        row = self.row_of(job_id)
        return self.jobs[row] if row >= 0 else None

    def active_jobs(self) -> list[DownloadJob]:
        return [j for j in self.jobs if j.state.active]

    def has_active(self) -> bool:
        return bool(self.active_jobs())

    def running_count(self) -> int:
        return len(self._threads)

    def threads_running(self) -> bool:
        """True while a job thread is still executing (destroying it now would abort the process)."""
        return any(t.isRunning() for t in self._threads.values())

    # ------------------------------------------------------------------ actions
    def enqueue(
        self, description: str, targets: list[DownloadTarget], options: DownloadOptions
    ) -> DownloadJob:
        job = DownloadJob(next(self._ids), description, list(targets), options)
        self.jobs.append(job)
        self.jobAdded.emit(len(self.jobs) - 1)
        self._emit_active()
        self._start_next()
        return job

    def cancel(self, job_id: int) -> None:
        job = self.job(job_id)
        if job is None or not job.state.active:
            return
        job.cancel_event.set()
        if job.state is JobState.QUEUED:
            job.state = JobState.CANCELLED
            self._emit_active()
        else:
            job.state = JobState.CANCELLING
        self.jobChanged.emit(self.row_of(job_id))

    def cancel_all(self) -> None:
        for job in list(self.jobs):
            self.cancel(job.id)

    def remove_finished(self) -> None:
        keep = [j for j in self.jobs if j.state.active]
        if len(keep) != len(self.jobs):
            self.jobs[:] = keep
            self.jobChanged.emit(-1)  # full refresh

    def wait(self, timeout_ms: int) -> bool:
        """Block until every job thread finished (bounded). Returns True if all stopped."""
        timer = QElapsedTimer()
        timer.start()
        for thread in list(self._threads.values()):
            remaining = max(0, timeout_ms - timer.elapsed())
            if not thread.wait(remaining):
                return False
        return True

    # ------------------------------------------------------------------ internals
    def _emit_active(self) -> None:
        self.activeChanged.emit(len(self.active_jobs()))

    def _start_next(self) -> None:
        while len(self._threads) < self.max_parallel:
            job = next((j for j in self.jobs if j.state is JobState.QUEUED), None)
            if job is None:
                return
            job.state = JobState.RUNNING
            job.message = "Starting…"
            thread = DownloadThread(job, self.portal, self.factory, self)
            thread.progress.connect(self._on_progress)
            thread.completed.connect(self._on_completed)
            self._threads[job.id] = thread
            self.jobChanged.emit(self.row_of(job.id))
            thread.start()

    @Slot(int, object)
    def _on_progress(self, job_id: int, ev: ProgressEvent) -> None:
        job = self.job(job_id)
        if job is None:
            return
        job.apply(ev)
        if job.state is JobState.RUNNING and ev.kind is EventKind.REGISTER_START:
            job.message = ""
        # repaint at most ~10 times per second per job, but always on structural events
        timer = self._throttle.setdefault(job_id, QElapsedTimer())
        structural = ev.kind not in (EventKind.PAGE_DONE, EventKind.PAGE_SKIPPED)
        if structural or not timer.isValid() or timer.elapsed() > 100 or job.pages_done == job.pages_total:
            timer.start()
            self.jobChanged.emit(self.row_of(job_id))

    @Slot(int, object, object)
    def _on_completed(self, job_id: int, report: DownloadReport | None, exc: BaseException | None) -> None:
        thread = self._threads.pop(job_id, None)
        if thread is not None:
            thread.wait(2000)
            thread.deleteLater()
        self._throttle.pop(job_id, None)
        job = self.job(job_id)
        if job is not None:
            job.report = report
            if exc is not None:
                job.state = JobState.FAILED
                job.message = str(exc)
                job.errors.append(str(exc))
            elif report is None:
                job.state = JobState.FAILED
            elif report.cancelled or job.cancel_event.is_set():
                job.state = JobState.CANCELLED
                job.message = "Cancelled by the user"
            elif report.ok:
                job.state = JobState.DONE
                job.message = (
                    f"{report.pages_downloaded} downloaded, {report.pages_skipped} already present"
                    if report.pages_skipped
                    else f"{report.pages_downloaded} pages downloaded"
                )
            else:
                nothing = report.pages_downloaded == 0 and report.pages_skipped == 0
                job.state = JobState.FAILED if nothing else JobState.DONE_WITH_ERRORS
                job.message = report.errors[-1] if report.errors else "Some pages failed"
            if report is not None and report.output_dirs:
                job.last_dir = report.output_dirs[-1]
            self.jobChanged.emit(self.row_of(job_id))
            self.jobFinished.emit(job)
        self._emit_active()
        self._start_next()


# --------------------------------------------------------------------------- model / view
COLUMNS = ["Download", "Status", "Current register", "Pages", "Registers", "Size"]
COL_PROGRESS = 3
PROGRESS_ROLE = Qt.ItemDataRole.UserRole + 20
JOB_ROLE = Qt.ItemDataRole.UserRole + 21


class DownloadQueueModel(QAbstractTableModel):
    def __init__(self, manager: DownloadManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.manager = manager
        manager.jobAdded.connect(self._on_added)
        manager.jobChanged.connect(self._on_changed)

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.manager.jobs)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.manager.jobs):
            return None
        job = self.manager.jobs[index.row()]
        col = index.column()
        if role == JOB_ROLE:
            return job
        if role == PROGRESS_ROLE and col == COL_PROGRESS:
            return job.pages_done, job.pages_total
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 0:
                return job.description
            if col == 1:
                return job.state.value + (f" - {job.message}" if job.message else "")
            if col == 2:
                return job.current_register
            if col == COL_PROGRESS:
                return f"{job.pages_done}/{job.pages_total}" if job.pages_total else ""
            if col == 4:
                total = job.registers_total or "?"
                failed = f" ({job.registers_failed} failed)" if job.registers_failed else ""
                return f"{job.registers_done}/{total}{failed}"
            if col == 5:
                return format_bytes(job.bytes) if job.bytes else ""
        if role == Qt.ItemDataRole.ToolTipRole:
            lines = [job.description, f"Status: {job.state.value}", f"Folder: {job.folder}"]
            if job.message:
                lines.append(job.message)
            lines.extend(job.errors[-5:])
            return "\n".join(lines)
        if role == Qt.ItemDataRole.ForegroundRole and col == 1:
            if job.state in (JobState.FAILED, JobState.DONE_WITH_ERRORS):
                return QColor("#b3261e")
            if job.state is JobState.DONE:
                return QColor("#2e7d32")
        if role == Qt.ItemDataRole.DecorationRole and col == 0:
            style = QApplication.style()
            sp = QStyle.StandardPixmap
            icon = {
                JobState.QUEUED: sp.SP_MediaPause,
                JobState.RUNNING: sp.SP_MediaPlay,
                JobState.CANCELLING: sp.SP_BrowserStop,
                JobState.DONE: sp.SP_DialogApplyButton,
                JobState.DONE_WITH_ERRORS: sp.SP_MessageBoxWarning,
                JobState.FAILED: sp.SP_MessageBoxCritical,
                JobState.CANCELLED: sp.SP_DialogCancelButton,
            }[job.state]
            return style.standardIcon(icon)
        return None

    def _on_added(self, row: int) -> None:
        self.beginInsertRows(QModelIndex(), row, row)
        self.endInsertRows()

    def _on_changed(self, row: int) -> None:
        if row < 0:
            self.beginResetModel()
            self.endResetModel()
            return
        self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMNS) - 1))


class ProgressDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index) -> None:
        value = index.data(PROGRESS_ROLE)
        if not value or not value[1]:
            super().paint(painter, option, index)
            return
        done, total = value
        opt = QStyleOptionProgressBar()
        opt.rect = option.rect.adjusted(3, 4, -3, -4)
        opt.minimum = 0
        opt.maximum = total
        opt.progress = min(done, total)
        opt.text = f"{done}/{total}"
        opt.textVisible = True
        opt.state = option.state | QStyle.StateFlag.State_Horizontal
        opt.palette = option.palette
        if option.state & QStyle.StateFlag.State_Selected:
            # draw the selection background behind the bar
            bg = QStyleOptionViewItem(option)
            self.initStyleOption(bg, index)
            bg.text = ""
            QApplication.style().drawPrimitive(QStyle.PrimitiveElement.PE_PanelItemViewItem, bg, painter)
        QApplication.style().drawControl(QStyle.ControlElement.CE_ProgressBar, opt, painter)


class DownloadQueueWidget(QWidget):
    openFolderRequested = Signal(object)  # DownloadJob

    def __init__(self, manager: DownloadManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.model = DownloadQueueModel(manager, self)

        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(COL_PROGRESS, ProgressDelegate(self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        for col, width in ((1, 250), (2, 170), (3, 150), (4, 110), (5, 90)):
            header.resizeSection(col, width)
        header.setStretchLastSection(False)

        style = self.style()
        sp = QStyle.StandardPixmap
        self.title = QLabel("<b>Downloads</b>")
        self.cancel_btn = QPushButton(style.standardIcon(sp.SP_BrowserStop), "Cancel")
        self.cancel_all_btn = QPushButton("Cancel all")
        self.clear_btn = QPushButton(style.standardIcon(sp.SP_DialogResetButton), "Clear finished")
        self.folder_btn = QPushButton(style.standardIcon(sp.SP_DirOpenIcon), "Open folder")

        bar = QHBoxLayout()
        bar.addWidget(self.title)
        bar.addStretch(1)
        for b in (self.folder_btn, self.cancel_btn, self.cancel_all_btn, self.clear_btn):
            bar.addWidget(b)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(4)
        layout.addLayout(bar)
        layout.addWidget(self.table, 1)

        self.cancel_btn.clicked.connect(self.cancel_selected)
        self.cancel_all_btn.clicked.connect(manager.cancel_all)
        self.clear_btn.clicked.connect(manager.remove_finished)
        self.folder_btn.clicked.connect(self._open_selected_folder)
        self.table.doubleClicked.connect(lambda idx: self.openFolderRequested.emit(idx.data(JOB_ROLE)))
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.selectionModel().selectionChanged.connect(self._update_buttons)
        manager.jobChanged.connect(self._update_buttons)
        manager.jobAdded.connect(self._update_buttons)
        self._update_buttons()

    def selected_jobs(self) -> list[DownloadJob]:
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [self.manager.jobs[r] for r in rows if r < len(self.manager.jobs)]

    def cancel_selected(self) -> None:
        for job in self.selected_jobs():
            self.manager.cancel(job.id)

    def _open_selected_folder(self) -> None:
        jobs = self.selected_jobs() or self.manager.jobs[-1:]
        if jobs:
            self.openFolderRequested.emit(jobs[0])

    def _update_buttons(self, *_args) -> None:
        selected = self.selected_jobs()
        self.cancel_btn.setEnabled(any(j.state.active for j in selected))
        self.cancel_all_btn.setEnabled(self.manager.has_active())
        self.clear_btn.setEnabled(any(j.state.finished for j in self.manager.jobs))
        self.folder_btn.setEnabled(bool(self.manager.jobs))
        active = len(self.manager.active_jobs())
        self.title.setText(f"<b>Downloads</b>  ({active} active)" if active else "<b>Downloads</b>")

    def _context_menu(self, pos) -> None:
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        job: DownloadJob = index.data(JOB_ROLE)
        menu = QMenu(self)
        cancel = menu.addAction("Cancel")
        cancel.setEnabled(job.state.active)
        folder = menu.addAction("Open folder")
        menu.addSeparator()
        copy_errors = menu.addAction("Copy errors")
        copy_errors.setEnabled(bool(job.errors))
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is cancel:
            self.manager.cancel(job.id)
        elif chosen is folder:
            self.openFolderRequested.emit(job)
        elif chosen is copy_errors:
            QApplication.clipboard().setText("\n".join(job.errors))
