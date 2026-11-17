"""Background execution helpers.

Every network call of the GUI goes through a :class:`TaskRunner`: the callable runs in a
:class:`QThreadPool` thread and its result (or exception) is delivered back to the GUI thread
through a queued Qt signal, where the ``on_done`` / ``on_error`` callbacks are invoked. Widgets are
therefore only ever touched from the GUI thread.
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

log = logging.getLogger(__name__)

DoneCallback = Callable[[Any], None]
ErrorCallback = Callable[[BaseException], None]


class _Task(QRunnable):
    def __init__(self, runner: TaskRunner, task_id: int, fn: Callable[[], Any]) -> None:
        super().__init__()
        self.setAutoDelete(False)  # the runner keeps the Python reference until the result arrives
        self._runner = runner
        self._task_id = task_id
        self._fn = fn

    def run(self) -> None:  # worker thread
        try:
            result = self._fn()
        except BaseException as exc:  # noqa: BLE001 - delivered to the GUI thread
            self._runner._result.emit(self._task_id, False, exc)
            return
        self._runner._result.emit(self._task_id, True, result)


class TaskRunner(QObject):
    """Runs callables in a private thread pool; callbacks are invoked on the GUI thread."""

    #: emitted (on the GUI thread) when the number of tracked running tasks goes 0 <-> >0
    busyChanged = Signal(bool)

    # (task id, ok, result-or-exception); emitted from pool threads, received on the GUI thread
    _result = Signal(int, bool, object)

    def __init__(self, max_threads: int = 4, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max_threads)
        self._ids = itertools.count(1)
        self._pending: dict[int, tuple[_Task, DoneCallback | None, ErrorCallback | None, bool]] = {}
        self._busy = 0
        self._result.connect(self._on_result)

    @property
    def busy(self) -> bool:
        return self._busy > 0

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def submit(
        self,
        fn: Callable[..., Any],
        *args: Any,
        on_done: DoneCallback | None = None,
        on_error: ErrorCallback | None = None,
        track_busy: bool = True,
        **kwargs: Any,
    ) -> int:
        """Run ``fn(*args, **kwargs)`` in the pool. Returns a task id."""
        task_id = next(self._ids)
        task = _Task(self, task_id, lambda: fn(*args, **kwargs))
        self._pending[task_id] = (task, on_done, on_error, track_busy)
        if track_busy:
            self._busy += 1
            if self._busy == 1:
                self.busyChanged.emit(True)
        self.pool.start(task)
        return task_id

    @Slot(int, bool, object)
    def _on_result(self, task_id: int, ok: bool, payload: object) -> None:
        entry = self._pending.pop(task_id, None)
        if entry is None:
            return
        _task, on_done, on_error, track_busy = entry
        if track_busy:
            self._busy -= 1
            if self._busy == 0:
                self.busyChanged.emit(False)
        try:
            if ok:
                if on_done is not None:
                    on_done(payload)
            elif on_error is not None:
                on_error(payload)  # type: ignore[arg-type]
            else:
                log.warning("Background task failed: %s", payload)
        except Exception:  # pragma: no cover - a bad callback must not break the event loop
            log.exception("Task callback failed")

    def shutdown(self, timeout_ms: int = 3000) -> bool:
        """Drop queued tasks and wait (bounded) for running ones."""
        self.pool.clear()
        return self.pool.waitForDone(timeout_ms)
