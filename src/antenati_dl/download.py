"""Concurrent, resumable download engine shared by the CLI and the GUI.

Usage::

    portal = Portal()
    dl = Downloader(portal, DownloadOptions(output_dir=Path("Download")), on_event=print)
    report = dl.run([DownloadTarget.from_ref("an_ua37845354", pages="1-10")])

Files are laid out as::

    <output>/<Archivio>/<Fondo>/<Serie>/<Tipologia>/<Titolo> (<an_ua id>)/0001.jpg
                                                                    /register.json

Pages already on disk are skipped, so an interrupted download can simply be restarted.
Progress is reported through :class:`~antenati_dl.models.ProgressEvent` objects, always emitted
from the thread that called :meth:`Downloader.run`.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .http import BlockedError, PortalError
from .models import (
    DownloadReport,
    EventKind,
    Manifest,
    Page,
    ProgressEvent,
    RegisterSummary,
    SearchQuery,
)
from .portal import Portal, parse_register_ref
from .utils import estimate_page_bytes, format_bytes, parse_page_ranges, sanitize_filename

log = logging.getLogger(__name__)

EventCallback = Callable[[ProgressEvent], None]


class InsufficientSpaceError(PortalError):
    """Raised when the destination disk does not have room for the next register."""


@dataclass
class DownloadOptions:
    output_dir: Path = field(default_factory=lambda: Path("Download"))
    size: str = "full"  # "full" (original), "max" (~10 MP) or a width in pixels
    workers: int = 4
    overwrite: bool = False
    write_metadata: bool = True
    pdf: bool = False  # also build <register>/register.pdf (needs the optional img2pdf package)
    min_free_bytes: int = 200 * 1024 * 1024
    check_disk_space: bool = True


@dataclass
class DownloadTarget:
    """Something to download: one register (optionally a page subset) or every register of a search."""

    register: str | RegisterSummary | Manifest | None = None
    pages: str | list[int] | None = None  # "1-10,15" or explicit 1-based indices; None = all
    query: SearchQuery | None = None
    limit: int | None = None  # max registers for a query target
    page_image_id: str | None = None  # select a single page by IIIF id (from a page URL)

    @classmethod
    def from_ref(cls, ref: str, pages: str | list[int] | None = None) -> DownloadTarget:
        """Build a target from an ark URL / id / manifest URL; page URLs select that page."""
        _rid, image_id, _url = parse_register_ref(ref)
        return cls(register=ref, pages=pages, page_image_id=image_id if pages is None else None)

    @classmethod
    def from_query(cls, query: SearchQuery, limit: int | None = None) -> DownloadTarget:
        return cls(query=query, limit=limit)

    def describe(self) -> str:
        if self.query is not None:
            return f"search {self.query.to_params(1, 1)}"
        reg = self.register
        if isinstance(reg, Manifest):
            return reg.label
        if isinstance(reg, RegisterSummary):
            return f"{reg.title} {reg.id}"
        return str(reg)


@dataclass
class Estimate:
    registers: int
    pages: int
    bytes: int
    exact: bool  # False when extrapolated from a sample of registers

    def describe(self) -> str:
        approx = "" if self.exact else "~"
        return f"{self.registers} registri, {approx}{self.pages} pagine, {approx}{format_bytes(self.bytes)}"


class Downloader:
    def __init__(
        self,
        portal: Portal,
        options: DownloadOptions | None = None,
        on_event: EventCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.portal = portal
        self.options = options or DownloadOptions()
        self.on_event = on_event or (lambda _e: None)
        self.cancel_event = cancel_event or threading.Event()

    # ----------------------------------------------------------------- public
    def cancel(self) -> None:
        self.cancel_event.set()

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def register_dir(self, manifest: Manifest) -> Path:
        parts = [sanitize_filename(p) for p in manifest.context] or ["Registro"]
        if manifest.tipologia:
            parts.append(sanitize_filename(manifest.tipologia))
        title = manifest.title or "registro"
        leaf = f"{title} ({manifest.register_id})" if manifest.register_id else title
        parts.append(sanitize_filename(leaf, max_length=100))
        return Path(self.options.output_dir).joinpath(*parts)

    def page_path(self, manifest: Manifest, page: Page) -> Path:
        return self.register_dir(manifest) / f"{page.index:04d}.jpg"

    def select_pages(self, manifest: Manifest, target: DownloadTarget) -> list[Page]:
        if target.page_image_id:
            pages = [p for p in manifest.pages if p.image_id == target.page_image_id]
            if not pages:
                raise PortalError(f"Page {target.page_image_id} not found in register")
            return pages
        if isinstance(target.pages, list):
            wanted = set(target.pages)
            bad = [i for i in wanted if i < 1 or i > len(manifest.pages)]
            if bad:
                raise ValueError(f"Pages {bad} outside 1-{len(manifest.pages)}")
        else:
            wanted = set(parse_page_ranges(target.pages, len(manifest.pages)))
        return [p for p in manifest.pages if p.index in wanted]

    def count_registers(self, targets: Iterable[DownloadTarget]) -> int:
        total = 0
        for t in targets:
            if t.query is not None:
                n = self.portal.count(t.query)
                total += min(n, t.limit) if t.limit is not None else n
            else:
                total += 1
        return total

    def estimate(self, targets: list[DownloadTarget], sample: int = 5) -> Estimate:
        """Estimate registers/pages/bytes. Query targets are extrapolated from ``sample`` registers."""
        per_page = estimate_page_bytes(self.options.size)
        registers = pages = 0
        exact = True
        for t in targets:
            if t.query is None:
                manifest = self._load(t.register)
                n = len(self.select_pages(manifest, t))
                registers += 1
                pages += n
                continue
            count = self.portal.count(t.query)
            if t.limit is not None:
                count = min(count, t.limit)
            if count == 0:
                continue
            sampled = [
                len(self.portal.manifest(r).pages)
                for r in self.portal.iter_registers(t.query, limit=min(sample, count), size=max(sample, 1))
            ]
            avg = sum(sampled) / len(sampled) if sampled else 0
            registers += count
            pages += round(avg * count)
            exact = exact and len(sampled) >= count
        return Estimate(registers, pages, pages * per_page, exact)

    def iter_jobs(self, targets: Iterable[DownloadTarget]) -> Iterator[tuple[Manifest, list[Page]]]:
        """Resolve targets lazily into ``(manifest, pages)`` pairs."""
        for t in targets:
            if t.query is not None:
                for summary in self.portal.iter_registers(t.query, limit=t.limit):
                    if self.cancelled:
                        return
                    yield self._load(summary), None  # type: ignore[misc]  # all pages
            else:
                manifest = self._load(t.register)
                yield manifest, self.select_pages(manifest, t)

    def run(self, targets: list[DownloadTarget]) -> DownloadReport:
        report = DownloadReport()
        try:
            register_total = self.count_registers(targets)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user as an event
            report.errors.append(str(exc))
            report.registers_failed += 1
            self._emit(EventKind.REGISTER_FAILED, message=str(exc), error=str(exc))
            self._emit(EventKind.FINISHED, message="Download non avviato")
            return report

        workers = max(1, int(self.options.workers))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="antenati-dl") as pool:
            index = 0
            jobs = self.iter_jobs(targets)
            while not self.cancelled:
                try:
                    manifest, pages = next(jobs)
                except StopIteration:
                    break
                except Exception as exc:  # noqa: BLE001 - one bad register must not stop a bulk job
                    index += 1
                    report.registers_failed += 1
                    report.errors.append(str(exc))
                    self._emit(
                        EventKind.REGISTER_FAILED,
                        message=f"Registro non disponibile: {exc}",
                        register_index=index,
                        register_total=register_total,
                        error=str(exc),
                    )
                    if isinstance(exc, BlockedError):
                        break
                    continue
                index += 1
                if pages is None:
                    pages = list(manifest.pages)
                try:
                    self._download_register(pool, manifest, pages, index, register_total, report)
                except InsufficientSpaceError as exc:
                    report.errors.append(str(exc))
                    report.registers_failed += 1
                    self._emit(EventKind.REGISTER_FAILED, register=manifest, message=str(exc), error=str(exc))
                    break

        report.cancelled = self.cancelled
        if report.cancelled:
            self._emit(EventKind.CANCELLED, message="Download annullato")
        self._emit(
            EventKind.FINISHED,
            message=(
                f"{report.pages_downloaded} pagine scaricate ({format_bytes(report.bytes_downloaded)}), "
                f"{report.pages_skipped} già presenti, {report.pages_failed} errori"
            ),
            bytes=report.bytes_downloaded,
        )
        return report

    # ----------------------------------------------------------------- internals
    def _emit(self, kind: EventKind, **kwargs) -> None:
        try:
            self.on_event(ProgressEvent(kind=kind, **kwargs))
        except Exception:  # pragma: no cover - a broken UI callback must not kill the download
            log.exception("Progress callback failed")

    def _load(self, ref: str | RegisterSummary | Manifest | None) -> Manifest:
        if isinstance(ref, Manifest):
            return ref
        if ref is None:
            raise ValueError("Download target without register or query")
        return self.portal.manifest(ref)

    def _check_space(self, directory: Path, pages: int) -> None:
        if not self.options.check_disk_space or pages == 0:
            return
        probe = directory
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        free = shutil.disk_usage(probe).free
        needed = pages * estimate_page_bytes(self.options.size) + self.options.min_free_bytes
        if free < needed:
            raise InsufficientSpaceError(
                f"Spazio su disco insufficiente: liberi {format_bytes(free)}, "
                f"servono circa {format_bytes(needed)}"
            )

    def _download_register(
        self,
        pool: ThreadPoolExecutor,
        manifest: Manifest,
        pages: list[Page],
        index: int,
        register_total: int,
        report: DownloadReport,
    ) -> None:
        directory = self.register_dir(manifest)
        todo: list[Page] = []
        skipped: list[Page] = []
        for page in pages:
            path = self.page_path(manifest, page)
            if not self.options.overwrite and path.exists() and path.stat().st_size > 0:
                skipped.append(page)
            else:
                todo.append(page)

        self._check_space(directory, len(todo))
        directory.mkdir(parents=True, exist_ok=True)
        if self.options.write_metadata:
            self._write_metadata(manifest, directory)
        report.output_dirs.append(str(directory))

        total = len(pages)
        done = 0
        common = {"register": manifest, "register_index": index, "register_total": register_total}
        self._emit(
            EventKind.REGISTER_START,
            message=f"{manifest.label} - {total} pagine",
            pages_total=total,
            path=str(directory),
            **common,
        )
        for page in skipped:
            done += 1
            report.pages_skipped += 1
            self._emit(
                EventKind.PAGE_SKIPPED,
                page=page,
                pages_done=done,
                pages_total=total,
                path=str(self.page_path(manifest, page)),
                **common,
            )

        failed = 0
        futures: dict[Future, Page] = {pool.submit(self._fetch_page, manifest, p): p for p in todo}
        pending = set(futures)
        while pending:
            finished, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
            if self.cancelled:
                for f in pending:
                    f.cancel()
            for fut in finished:
                page = futures[fut]
                if fut.cancelled():
                    continue
                done += 1
                try:
                    path, size = fut.result()
                except Exception as exc:  # noqa: BLE001
                    if self.cancelled:
                        continue  # interrupted by the user, not a real failure
                    failed += 1
                    report.pages_failed += 1
                    msg = f"{manifest.register_id} pagina {page.index}: {exc}"
                    report.errors.append(msg)
                    self._emit(
                        EventKind.PAGE_FAILED,
                        page=page,
                        pages_done=done,
                        pages_total=total,
                        error=str(exc),
                        message=msg,
                        **common,
                    )
                    continue
                report.pages_downloaded += 1
                report.bytes_downloaded += size
                self._emit(
                    EventKind.PAGE_DONE,
                    page=page,
                    pages_done=done,
                    pages_total=total,
                    bytes=size,
                    path=str(path),
                    **common,
                )
            if self.cancelled and not pending:
                break

        if self.cancelled:
            return
        if failed:
            report.registers_failed += 1
            self._emit(
                EventKind.REGISTER_FAILED,
                message=f"{failed} pagine non scaricate",
                pages_done=done,
                pages_total=total,
                path=str(directory),
                error=f"{failed} pagine non scaricate",
                **common,
            )
            return
        if self.options.pdf:
            self._build_pdf(manifest, pages, directory)
        report.registers_done += 1
        self._emit(
            EventKind.REGISTER_DONE,
            message=str(directory),
            pages_done=done,
            pages_total=total,
            path=str(directory),
            **common,
        )

    def _fetch_page(self, manifest: Manifest, page: Page) -> tuple[Path, int]:
        if self.cancelled:
            raise PortalError("cancelled")
        path = self.page_path(manifest, page)
        data = self.portal.http.get_image(page.image_url(self.options.size), cancelled=lambda: self.cancelled)
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        return path, len(data)

    def _write_metadata(self, manifest: Manifest, directory: Path) -> None:
        info = {
            "register_id": manifest.register_id,
            "register_url": f"https://antenati.cultura.gov.it/ark:/12657/{manifest.register_id}/"
            if manifest.register_id
            else None,
            "manifest_url": manifest.url,
            "label": manifest.label,
            "metadata": manifest.metadata,
            "image_size": str(self.options.size),
            "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pages": [
                {"index": p.index, "label": p.label, "image_id": p.image_id, "file": f"{p.index:04d}.jpg"}
                for p in manifest.pages
            ],
        }
        (directory / "register.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), "utf-8")

    def _build_pdf(self, manifest: Manifest, pages: list[Page], directory: Path) -> None:
        try:
            import img2pdf  # type: ignore[import-not-found]
        except ImportError:
            self._emit(EventKind.LOG, message="PDF non creato: installare il pacchetto opzionale 'img2pdf'")
            return
        files = [str(self.page_path(manifest, p)) for p in pages if self.page_path(manifest, p).exists()]
        if not files:
            return
        target = directory / "register.pdf"
        try:
            target.write_bytes(img2pdf.convert(files))
            self._emit(EventKind.LOG, message=f"PDF creato: {target}", path=str(target))
        except Exception as exc:  # noqa: BLE001
            self._emit(EventKind.LOG, message=f"PDF non creato: {exc}", error=str(exc))
