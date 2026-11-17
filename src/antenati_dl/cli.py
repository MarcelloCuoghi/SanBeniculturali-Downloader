"""Command line interface of antenati-dl.

Browse the Portale Antenati hierarchy (Archivio > Fondo > Serie > Tipologia > Anno > Registro > Pagine),
download single registers or whole branches of it, or navigate interactively::

    antenati-dl archives vibo
    antenati-dl fondi 145
    antenati-dl series 145 acq
    antenati-dl browse --serie 37842016 --tipologia Nati
    antenati-dl info an_ua37845354 --pages
    antenati-dl download an_ua37845354 --pages 1-10
    antenati-dl download-all --serie 37842016 --tipologia Nati --anno 1911
    antenati-dl interactive
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import sys
import threading
import webbrowser
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.logging import RichHandler
from rich.markup import escape
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.prompt import Confirm, Prompt
from rich.table import Table

from . import __version__
from .download import Downloader, DownloadOptions, DownloadTarget
from .http import PortalError
from .models import (
    PORTAL_URL,
    Archive,
    DownloadReport,
    EventKind,
    FacetTerm,
    Fondo,
    Manifest,
    ProgressEvent,
    RegisterSummary,
    SearchPage,
    SearchQuery,
    Serie,
)
from .portal import EXPLORE_URL, SEARCH_URL, Portal, parse_register_ref
from .utils import format_bytes, page_range_spans, parse_page_ranges

log = logging.getLogger("antenati_dl")

#: Register lists up to this size are printed by ``browse`` without ``--registers``.
SMALL_RESULT = 50
#: Items per page in the interactive navigator.
MENU_PAGE_SIZE = 30
#: Maximum number of registers listed by ``--dry-run`` for a bulk download.
DRY_RUN_LIST_LIMIT = 100
MAX_WORKERS = 16

SORT_CHOICES = {"relevance": "", "newest": "estremoRemoto_i desc", "oldest": "estremoRemoto_i asc"}

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ERROR = 2
EXIT_INTERRUPTED = 130

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)


class CliError(Exception):
    """A user error (bad argument, nothing matching, ...) reported without traceback."""


# =========================================================================== setup helpers
def _configure_streams() -> None:
    """Make sure non-ASCII names (e.g. ``Archivio di Stato dell’Aquila``) can be printed on Windows."""
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        with contextlib.suppress(OSError, ValueError):  # detached or exotic streams
            reconfigure(encoding="utf-8", errors="replace")


def _configure_logging(verbosity: int) -> None:
    level = logging.WARNING if verbosity <= 0 else logging.INFO if verbosity == 1 else logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=err_console, show_path=verbosity > 1, rich_tracebacks=True)],
        force=True,
    )
    if verbosity < 2:  # urllib3 is very chatty at DEBUG
        logging.getLogger("urllib3").setLevel(max(level, logging.INFO))


def make_portal() -> Portal:
    """Create the portal client (monkeypatched by the tests)."""
    return Portal()


# =========================================================================== argument types
def _size_arg(value: str) -> str:
    value = value.strip().lower()
    if value in ("full", "max"):
        return value
    if value.isdigit() and int(value) > 0:
        return value
    raise argparse.ArgumentTypeError("must be 'full', 'max' or a width in pixels (e.g. 2000)")


def _workers_arg(value: str) -> int:
    try:
        workers = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer") from None
    if not 1 <= workers <= MAX_WORKERS:
        raise argparse.ArgumentTypeError(f"must be between 1 and {MAX_WORKERS}")
    return workers


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer") from None
    if number < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return number


def _check_page_spec(spec: str | None) -> None:
    """Validate the syntax of a ``--pages`` selection before talking to the portal."""
    if spec is None:
        return
    try:
        page_range_spans(spec, 10**9)  # syntax only: never expand open ranges like "20-" here
    except ValueError:
        raise CliError(f"Invalid page selection {spec!r} (examples: 1-10,15  20-  last)") from None


# =========================================================================== resolution helpers
def plural(count: int, word: str) -> str:
    """``plural(1, "register")`` -> ``"1 register"``, ``plural(2, "register")`` -> ``"2 registers"``."""
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _casefold(text: str) -> str:
    return text.strip().strip('"').casefold()


def resolve_fondo(portal: Portal, archive: Archive, text: str) -> Fondo:
    """Find a fondo of ``archive`` by id or (part of its) name."""
    fondi = portal.fondi(archive.id)
    if not fondi:
        raise CliError(f"{archive.name} has no fondi")
    text = text.strip()
    if text.isdigit():
        for fondo in fondi:
            if fondo.id == int(text):
                return fondo
    needle = _casefold(text)
    exact = [f for f in fondi if f.name.casefold() == needle]
    if exact:
        return exact[0]
    matches = [f for f in fondi if needle in f.name.casefold()]
    if len(matches) == 1:
        return matches[0]
    names = ", ".join(f"{f.name} ({f.id})" for f in (matches or fondi)[:10])
    if matches:
        raise CliError(f"Ambiguous fondo {text!r}: {names}")
    raise CliError(f"No fondo {text!r} in {archive.name}. Available: {names}")


def resolve_serie(portal: Portal, text: str, archive: Archive | None, fondo: Fondo | None) -> Serie:
    """Find a serie by numeric id, or by name inside ``archive`` (and ``fondo``)."""
    text = text.strip()
    pool: list[Serie] = []
    if archive is not None:
        for f in [fondo] if fondo else portal.fondi(archive.id):
            pool.extend(f.series)
    if text.isdigit():
        sid = int(text)
        return next((s for s in pool if s.id == sid), Serie(sid, ""))
    if archive is None:
        raise CliError(
            f"--serie {text!r} is not an id: give --archive too, or use the numeric id "
            "printed by 'antenati-dl series ARCHIVE'"
        )
    needle = _casefold(text)
    exact = [s for s in pool if s.name.casefold() == needle]
    if exact:
        return exact[0]
    matches = [s for s in pool if needle in s.name.casefold()]
    if len(matches) == 1:
        return matches[0]
    if matches:
        names = ", ".join(f"{s.name} ({s.id})" for s in matches[:10])
        raise CliError(f"Ambiguous serie {text!r}: {names}")
    raise CliError(f"No serie {text!r} in {archive.name}")


def match_term(terms: Sequence[FacetTerm], value: str, what: str) -> FacetTerm:
    """Pick the facet term the user meant: exact (case-insensitive) match, then unique prefix/substring.

    The returned term's :attr:`FacetTerm.query` is the raw string to pass to :meth:`SearchQuery.refine`
    (values containing commas come back quoted from the portal, e.g. ``tipologia_ss:"Morti, allegati"``).
    """
    if not terms:
        raise CliError(f"No {what} available at this level")
    needle = _casefold(value)
    for term in terms:
        if term.value.casefold() == needle or term.query.casefold() == value.strip().casefold():
            return term
    if what != "anno":  # years must match exactly
        hits = [t for t in terms if t.value.casefold().startswith(needle)]
        if not hits:
            hits = [t for t in terms if needle in t.value.casefold()]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise CliError(f"Ambiguous {what} {value!r}: " + ", ".join(t.value for t in hits))
    shown = ", ".join(t.value for t in terms[:60]) + (" ..." if len(terms) > 60 else "")
    raise CliError(f"No {what} {value!r} here. Available: {shown}")


def _has_facet(query: SearchQuery, field_name: str) -> bool:
    return any(f.startswith(field_name + ":") for f in query.facets)


def fondo_query(portal: Portal, archive: Archive, fondo: Fondo) -> SearchQuery:
    """Search query for every register of ``fondo`` (archive + ``primo_livello_label_s`` facet)."""
    base = SearchQuery(archivio=archive.id)
    facet = portal.search(base, size=2).facets.get("primo_livello_label_s")
    for term in facet.terms if facet else []:
        if term.value.casefold() == fondo.name.casefold():
            return base.refine(term)
    return base.refine(f'primo_livello_label_s:"{fondo.name}"')


def search_url(query: SearchQuery) -> str:
    """Portal URL showing the results of ``query`` in a browser."""
    return f"{SEARCH_URL}?{urlencode(query.to_params(page=1, size=20))}"


def _context_crumbs(result: SearchPage) -> list[str]:
    """Archive / fondo / serie names, read from the single-valued facets of a search result."""
    crumbs: list[str] = []
    for field_name in ("conservatore_s", "primo_livello_label_s", "localita_fondo_s"):
        facet = result.facets.get(field_name)
        if not facet or len(facet.terms) != 1:
            break
        crumbs.append(facet.terms[0].value)
    return crumbs


@dataclass
class Scope:
    """Where the user is in the hierarchy, as selected with --archive/--fondo/--serie/... options."""

    archive: Archive | None = None
    fondo: Fondo | None = None
    serie: Serie | None = None
    query: SearchQuery | None = None
    result: SearchPage | None = None  # last search for ``query`` (facets + first registers)
    crumbs: list[str] = field(default_factory=list)

    @property
    def path(self) -> str:
        return " > ".join(self.crumbs)


def resolve_scope(portal: Portal, args: argparse.Namespace, build_query: bool = True) -> Scope:
    """Resolve the filter options shared by ``browse`` and ``download-all``.

    Builds a :class:`SearchQuery` from the base filters (serie, località, archive [+ fondo]) and narrows it
    with the ``tipologia_ss`` / ``anni_is`` facet terms returned by the portal.
    """
    scope = Scope()
    if args.archive:
        scope.archive = portal.get_archive(args.archive)
    if args.fondo:
        if scope.archive is None:
            raise CliError("--fondo needs --archive")
        scope.fondo = resolve_fondo(portal, scope.archive, args.fondo)
    if args.serie:
        scope.serie = resolve_serie(portal, args.serie, scope.archive, scope.fondo)
    if not build_query:
        return scope

    if scope.serie is not None:
        query = SearchQuery(serie=scope.serie.id)
    elif args.localita:
        query = SearchQuery(localita=args.localita, archivio=scope.archive.id if scope.archive else None)
        if scope.fondo is not None:  # keep the --fondo restriction (scope.archive is set with --fondo)
            assert scope.archive is not None
            for term in fondo_query(portal, scope.archive, scope.fondo).facets:
                query = query.refine(term)
    elif scope.archive is not None:
        query = (
            fondo_query(portal, scope.archive, scope.fondo)
            if scope.fondo
            else SearchQuery(archivio=scope.archive.id)
        )
    else:
        raise CliError("Give at least --archive, --serie or --localita")

    result = portal.search(query, size=SMALL_RESULT)
    crumbs = _context_crumbs(result)
    if not crumbs:
        crumbs = [n for n in (scope.archive and scope.archive.name, scope.fondo and scope.fondo.name) if n]
        if scope.serie is not None:
            crumbs.append(scope.serie.name or f"serie {scope.serie.id}")
        elif args.localita:
            crumbs.append(f"Località: {args.localita}")
    for field_name, value, what in (
        ("tipologia_ss", args.tipologia, "tipologia"),
        ("anni_is", args.anno, "anno"),
    ):
        if not value:
            continue
        facet = result.facets.get(field_name)
        term = match_term(facet.terms if facet else [], value, what)
        query = query.refine(term)
        crumbs.append(term.value)
        result = portal.search(query, size=SMALL_RESULT)
    scope.query, scope.result, scope.crumbs = query, result, crumbs
    return scope


# =========================================================================== tables
def _table(title: str | None, *columns: str, **kwargs: Any) -> Table:
    table = Table(title=title, box=box.SIMPLE_HEAD, title_justify="left", title_style="bold", **kwargs)
    for col in columns:
        justify = "right" if col in ("#", "Id", "Registers", "Series", "Pages", "Page", "Count") else "left"
        no_wrap = col in ("Id", "Register id", "Image id")
        table.add_column(col, justify=justify, overflow="fold", no_wrap=no_wrap)
    return table


def archives_table(archives: Sequence[Archive]) -> Table:
    table = _table(f"Archives ({len(archives)})", "Id", "Archivio")
    for a in archives:
        table.add_row(str(a.id), escape(a.name))
    return table


def fondi_table(archive: Archive, fondi: Sequence[Fondo]) -> Table:
    table = _table(f"Fondi of {escape(archive.name)} ({archive.id})", "Id", "Fondo", "Series")
    for f in fondi:
        table.add_row(str(f.id), escape(f.name), str(len(f.series)))
    return table


def series_table(title: str, rows: Sequence[tuple[Serie, Fondo]]) -> Table:
    table = _table(title, "Id", "Serie", "Fondo")
    for serie, fondo in rows:
        table.add_row(str(serie.id), escape(serie.name), escape(fondo.name))
    return table


def registers_table(registers: Sequence[RegisterSummary], title: str | None = None, start: int = 1) -> Table:
    table = _table(title, "#", "Register id", "Title", "Tipologia", "Context", "Segnatura")
    for i, r in enumerate(registers, start=start):
        table.add_row(
            str(i), r.id, escape(r.title), escape(r.tipologia), escape(r.context), escape(r.segnatura)
        )
    return table


def facet_table(title: str, terms: Sequence[FacetTerm]) -> Table:
    table = _table(title, "Value", "Registers")
    for t in terms:
        table.add_row(escape(t.value), str(t.count))
    return table


def years_view(terms: Sequence[FacetTerm]) -> Columns:
    return Columns([f"[bold]{escape(t.value)}[/] [dim]({t.count})[/]" for t in terms], padding=(0, 2))


def manifest_table(manifest: Manifest) -> Table:
    table = Table(box=box.SIMPLE, show_header=False, title=escape(manifest.label), title_justify="left")
    table.add_column("Field", style="bold")
    table.add_column("Value", overflow="fold")
    if manifest.register_id:
        table.add_row("Register id", manifest.register_id)
        table.add_row("Register URL", f"{PORTAL_URL}/ark:/12657/{manifest.register_id}/")
    table.add_row("Manifest", manifest.url)
    table.add_row("Pages", str(len(manifest.pages)))
    for key, value in manifest.metadata.items():
        if key != "Vedi il registro":
            table.add_row(escape(key), escape(value))
    return table


def pages_table(manifest: Manifest, highlight: str | None = None) -> Table:
    table = _table(f"Pages of {manifest.register_id or manifest.label}", "Page", "Label", "Image id")
    for p in manifest.pages:
        style = "reverse" if highlight and p.image_id == highlight else None
        table.add_row(str(p.index), escape(p.label), p.image_id, style=style)
    return table


# =========================================================================== download runner
class ProgressView:
    """Rich progress bars (overall registers + pages of the current register) fed by ProgressEvents."""

    def __init__(self, progress: Progress) -> None:
        self.progress = progress
        self.bytes = 0
        self.registers_seen = 0
        self.overall: TaskID = progress.add_task("Registers", total=None, extra="")
        self.pages: TaskID = progress.add_task("Pages", total=None, visible=False, extra="")

    def _print(self, message: str) -> None:
        self.progress.console.print(message)

    def handle(self, ev: ProgressEvent) -> None:
        p = self.progress
        if ev.register_total and p.tasks[self.overall].total != ev.register_total:
            p.update(self.overall, total=ev.register_total)
        kind = ev.kind
        if kind is EventKind.REGISTER_START:
            m = ev.register
            name = f"{m.register_id or ''} {m.title}".strip() if m else "Pages"
            p.reset(
                self.pages, total=ev.pages_total, completed=0, description=escape(name[:40]), visible=True
            )
            counter = escape(f"[{ev.register_index}/{ev.register_total}] ") if ev.register_total else ""
            label = escape(m.label) if m else ""
            where = escape(f"({plural(ev.pages_total, 'page')} -> {ev.path or ''})")
            self._print(f"[cyan]{counter}{label}[/] [dim]{where}[/]")
        elif kind in (EventKind.PAGE_DONE, EventKind.PAGE_SKIPPED, EventKind.PAGE_FAILED):
            self.bytes += ev.bytes
            p.update(self.pages, completed=ev.pages_done, total=ev.pages_total or None)
            p.update(self.overall, extra=format_bytes(self.bytes))
            if kind is EventKind.PAGE_FAILED:
                self._print(f"[yellow]  ! {escape(ev.message or str(ev.error))}[/]")
        elif kind in (EventKind.REGISTER_DONE, EventKind.REGISTER_FAILED):
            p.advance(self.overall)
            if kind is EventKind.REGISTER_FAILED:
                self._print(f"[red]  x {escape(ev.message or str(ev.error))}[/]")
        elif kind is EventKind.LOG:
            self._print(f"[dim]  {escape(ev.message)}[/]")
        elif kind is EventKind.CANCELLED:
            self._print("[yellow]Download cancelled[/]")


def make_progress(out: Console) -> Progress:
    return Progress(
        TextColumn("[progress.description]{task.description}", table_column=None),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        TextColumn("[dim]{task.fields[extra]}"),
        console=out,
    )


def run_download(portal: Portal, options: DownloadOptions, targets: list[DownloadTarget]) -> int:
    """Run a download with progress bars; Ctrl+C cancels cleanly. Returns the exit code."""
    cancel = threading.Event()
    progress = make_progress(console)
    view = ProgressView(progress)
    downloader = Downloader(portal, options, on_event=view.handle, cancel_event=cancel)
    outcome: dict[str, Any] = {}

    def work() -> None:
        try:
            outcome["report"] = downloader.run(targets)
        except BaseException as exc:  # noqa: BLE001 - re-raised in the main thread
            outcome["error"] = exc

    thread = threading.Thread(target=work, name="antenati-dl-run", daemon=True)
    forced = False
    with progress:
        thread.start()
        while thread.is_alive():
            try:
                thread.join(0.2)
            except KeyboardInterrupt:
                if cancel.is_set():
                    forced = True
                    break
                downloader.cancel()
                progress.console.print(
                    "[yellow]Cancelling: waiting for the pages in flight (Ctrl+C again to quit now)...[/]"
                )
    if "error" in outcome:
        raise outcome["error"]
    report: DownloadReport | None = outcome.get("report")
    if report is None:  # forced quit while the engine was still stopping
        console.print("[yellow]Download interrupted. Run the same command again to resume.[/]")
        return EXIT_INTERRUPTED
    print_report(report, forced=forced)
    if report.cancelled:
        return EXIT_INTERRUPTED
    return EXIT_OK if report.ok else EXIT_FAILED


def print_report(report: DownloadReport, forced: bool = False) -> None:
    table = Table(
        box=box.SIMPLE, show_header=False, title="Summary", title_justify="left", title_style="bold"
    )
    table.add_column("What", style="bold")
    table.add_column("Value", overflow="fold")
    table.add_row("Registers completed", str(report.registers_done))
    if report.registers_failed:
        table.add_row("Registers failed", f"[red]{report.registers_failed}[/]")
    table.add_row("Pages downloaded", f"{report.pages_downloaded} ({format_bytes(report.bytes_downloaded)})")
    table.add_row("Pages already on disk", str(report.pages_skipped))
    if report.pages_failed:
        table.add_row("Pages failed", f"[red]{report.pages_failed}[/]")
    dirs = list(dict.fromkeys(report.output_dirs))
    if len(dirs) == 1:
        table.add_row("Saved in", escape(dirs[0]))
    elif dirs:
        try:
            common = os.path.commonpath(dirs)
        except ValueError:
            common = ""
        table.add_row(
            "Saved in",
            escape(f"{common} ({plural(len(dirs), 'register')})" if common else f"{len(dirs)} folders"),
        )
    console.print(table)
    if report.pages_skipped:
        console.print(
            f"[dim]{plural(report.pages_skipped, 'page')} already on disk skipped (resume); "
            "use --overwrite to download them again.[/]"
        )
    for error in report.errors[:10]:
        console.print(f"[red]  {escape(error)}[/]")
    if len(report.errors) > 10:
        console.print(f"[red]  ... and {len(report.errors) - 10} more errors[/]")
    if report.cancelled or forced:
        console.print("[yellow]Download cancelled. Run the same command again to resume.[/]")
    elif report.ok:
        console.print("[green]Download complete.[/]")
    else:
        console.print("[red]Download finished with errors; run it again to retry the missing pages.[/]")


def download_options(args: argparse.Namespace) -> DownloadOptions:
    return DownloadOptions(
        output_dir=Path(args.output),
        size=args.size,
        workers=args.workers,
        overwrite=args.overwrite,
        write_metadata=not args.no_metadata,
        pdf=args.pdf,
    )


def show_plan(portal: Portal, options: DownloadOptions, targets: list[DownloadTarget]) -> None:
    """Print what a download would fetch (used by --dry-run)."""
    downloader = Downloader(portal, options)
    for target in targets:
        if target.query is None:
            manifest = portal.manifest(target.register)  # type: ignore[arg-type]
            pages = downloader.select_pages(manifest, target)
            table = _table(None, "Register id", "Title", "Tipologia", "Pages", "Destination")
            table.add_row(
                manifest.register_id or "",
                escape(manifest.title),
                escape(manifest.tipologia),
                f"{len(pages)}/{len(manifest.pages)}",
                escape(str(downloader.register_dir(manifest))),
            )
            console.print(table)
            continue
        total = portal.count(target.query)
        if target.limit is not None:
            total = min(total, target.limit)
        shown = list(portal.iter_registers(target.query, limit=min(total, DRY_RUN_LIST_LIMIT)))
        console.print(registers_table(shown, title=f"{plural(total, 'register')} would be downloaded"))
        if total > len(shown):
            console.print(f"[dim]... and {total - len(shown)} more[/]")
    console.print(f"Destination: [bold]{escape(str(Path(options.output_dir).resolve()))}[/]")
    console.print(f"Estimate: [bold]{downloader.estimate(targets).describe()}[/]")


# =========================================================================== commands
def cmd_archives(portal: Portal, args: argparse.Namespace) -> int:
    archives = portal.find_archives(args.filter) if args.filter else portal.archives()
    if not archives:
        raise CliError(f"No archive matches {args.filter!r}")
    console.print(archives_table(archives))
    return EXIT_OK


def cmd_fondi(portal: Portal, args: argparse.Namespace) -> int:
    archive = portal.get_archive(args.archive)
    console.print(fondi_table(archive, portal.fondi(archive.id)))
    return EXIT_OK


def cmd_series(portal: Portal, args: argparse.Namespace) -> int:
    archive = portal.get_archive(args.archive)
    fondi = [resolve_fondo(portal, archive, args.fondo)] if args.fondo else portal.fondi(archive.id)
    rows = [(s, f) for f in fondi for s in f.series]
    if args.filter:
        needle = args.filter.casefold()
        rows = [(s, f) for s, f in rows if needle in s.name.casefold()]
    if not rows:
        raise CliError("No series found" + (f" matching {args.filter!r}" if args.filter else ""))
    console.print(series_table(f"Series of {escape(archive.name)} ({len(rows)})", rows))
    return EXIT_OK


def _print_registers(portal: Portal, query: SearchQuery, result: SearchPage) -> None:
    if result.total <= len(result.registers):
        registers = result.registers
    else:
        registers = list(portal.iter_registers(query))
    console.print(registers_table(registers, title=f"Registers ({len(registers)})"))


def cmd_browse(portal: Portal, args: argparse.Namespace) -> int:
    search_mode = bool(args.serie or args.localita or args.tipologia or args.anno or args.registers)
    if not search_mode:
        if not args.archive:
            console.print(archives_table(portal.archives()))
            console.print("[dim]Next: antenati-dl browse --archive ID[/]")
            return EXIT_OK
        scope = resolve_scope(portal, args, build_query=False)
        assert scope.archive is not None
        if scope.fondo is None:
            console.print(fondi_table(scope.archive, portal.fondi(scope.archive.id)))
            console.print(f"[dim]Next: antenati-dl browse --archive {scope.archive.id} --fondo ID[/]")
        else:
            rows = [(s, scope.fondo) for s in scope.fondo.series]
            console.print(series_table(f"Series of {escape(scope.fondo.name)} ({len(rows)})", rows))
            console.print("[dim]Next: antenati-dl browse --serie ID[/]")
        return EXIT_OK

    scope = resolve_scope(portal, args)
    assert scope.query is not None and scope.result is not None
    query, result = scope.query, scope.result
    console.print(f"[bold]{escape(scope.path)}[/] - {plural(result.total, 'register')}")
    if result.total == 0:
        return EXIT_OK
    shown_level = False
    tipologie = result.facets.get("tipologia_ss")
    anni = result.facets.get("anni_is")
    if not _has_facet(query, "tipologia_ss") and tipologie and tipologie.terms:
        console.print(facet_table("Tipologia", tipologie.terms))
        example = max(tipologie.terms, key=lambda t: t.count).value
        console.print(f'[dim]Next: add --tipologia VALUE (e.g. --tipologia "{escape(example)}")[/]')
        shown_level = True
    elif not _has_facet(query, "anni_is") and anni and anni.terms:
        console.print("[bold]Anno[/]")
        console.print(years_view(anni.terms))
        console.print("[dim]Next: add --anno YEAR[/]")
        shown_level = True
    if args.registers or result.total <= SMALL_RESULT:
        _print_registers(portal, query, result)
    elif not shown_level:
        console.print(f"[dim]{plural(result.total, 'register')}: add --registers to list them all.[/]")
    console.print(
        "[dim]Download them with: antenati-dl download-all (same filters) or download REGISTER_ID[/]"
    )
    return EXIT_OK


def cmd_search(portal: Portal, args: argparse.Namespace) -> int:
    archive = portal.get_archive(args.archive) if args.archive else None
    query = SearchQuery(
        archivio=archive.id if archive else None,
        serie=args.serie,
        localita=args.localita,
        tipologia=args.tipologia,
        anno=args.anno,
        sort=SORT_CHOICES[args.sort] or None,
        nominativi=args.nominativi,
    )
    if query.is_empty():
        raise CliError("Give at least one of --localita, --tipologia, --anno, --archive, --serie")
    result = portal.search(query, page=args.page, size=args.size)
    console.print(
        f"[bold]{plural(result.total, 'register')}[/] found - page {result.page}/{result.page_count}"
    )
    if result.registers:
        start = (result.page - 1) * result.size + 1
        console.print(registers_table(result.registers, start=start))
    if result.page < result.page_count:
        console.print(f"[dim]Next page: --page {result.page + 1}[/]")
    return EXIT_OK


def cmd_info(portal: Portal, args: argparse.Namespace) -> int:
    _rid, image_id, _url = parse_register_ref(args.ref)
    manifest = portal.manifest(args.ref)
    console.print(manifest_table(manifest))
    if image_id:
        page = next((p for p in manifest.pages if p.image_id == image_id), None)
        if page:
            console.print(f"Selected page: [bold]{page.index}[/] ({escape(page.label)})")
    if args.pages:
        console.print(pages_table(manifest, highlight=image_id))
    else:
        console.print("[dim]Add --pages to list the pages.[/]")
    return EXIT_OK


def cmd_download(portal: Portal, args: argparse.Namespace) -> int:
    _check_page_spec(args.pages)
    targets = [DownloadTarget.from_ref(ref, pages=args.pages) for ref in args.refs]
    options = download_options(args)
    if args.dry_run:
        show_plan(portal, options, targets)
        return EXIT_OK
    checker = Downloader(portal, options)
    for target in targets:  # load manifests up front so bad refs/page ranges fail early and clearly
        manifest = portal.manifest(target.register)  # type: ignore[arg-type]
        try:
            checker.select_pages(manifest, target)
        except (ValueError, PortalError) as exc:
            raise CliError(f"{target.register}: {exc}") from None
    return run_download(portal, options, targets)


def _confirm(question: str, default: bool = False) -> bool:
    try:
        return Confirm.ask(question, default=default, console=console)
    except EOFError:
        return False


def cmd_download_all(portal: Portal, args: argparse.Namespace) -> int:
    if not (args.archive or args.serie or args.localita):
        raise CliError("Refusing a bulk download without --archive, --serie or --localita")
    scope = resolve_scope(portal, args)
    assert scope.query is not None and scope.result is not None
    total = scope.result.total if args.limit is None else min(scope.result.total, args.limit)
    console.print(f"[bold]{escape(scope.path)}[/] - {plural(total, 'register')} to download")
    if total == 0:
        console.print("[yellow]Nothing to download.[/]")
        return EXIT_OK
    targets = [DownloadTarget.from_query(scope.query, limit=args.limit)]
    options = download_options(args)
    if args.dry_run:
        show_plan(portal, options, targets)
        return EXIT_OK
    with console.status("Estimating size..."):
        estimate = Downloader(portal, options).estimate(targets)
    console.print(
        f"Estimate: [bold]{estimate.describe()}[/] -> {escape(str(Path(options.output_dir).resolve()))}"
    )
    if not args.yes and not _confirm("Start the download?"):
        console.print("Aborted.")
        return EXIT_OK
    return run_download(portal, options, targets)


def cmd_gui(_portal: Portal | None, _args: argparse.Namespace) -> int:
    try:
        from .gui.app import main as gui_main  # noqa: PLC0415 - optional dependency
    except ImportError as exc:
        missing = getattr(exc, "name", "") or ""
        if missing.startswith("PySide6"):
            err_console.print(
                "[red]The GUI needs PySide6.[/] Install it with: [bold]pip install 'antenati-dl[gui]'[/]"
            )
        else:
            err_console.print(f"[red]The graphical interface is not available: {escape(str(exc))}[/]")
        return EXIT_ERROR
    return int(gui_main() or 0)


def cmd_interactive(portal: Portal, args: argparse.Namespace) -> int:
    return Navigator(portal, download_options(args)).run()


# =========================================================================== interactive navigator
class Notice(Exception):
    """A message for the user that keeps the navigator on the current level."""


@dataclass
class Level:
    """One menu of the interactive navigator."""

    kind: str  # archives, fondi, series, tipologia, anno, registers, pages
    crumb: str
    columns: list[str]
    items: list[Any]
    rows: list[list[str]]
    enter: Callable[[Any], Level] | None = None
    targets: Callable[[str | None], list[DownloadTarget]] | None = None  # what 'd' downloads
    url: Callable[[Any | None], str] | None = None  # what 'o' opens (item or whole level)
    query: SearchQuery | None = None
    note: str = ""
    filter: str = ""
    page: int = 0

    def visible(self) -> list[int]:
        """Indices of the items matching the current filter."""
        if not self.filter:
            return list(range(len(self.items)))
        needle = self.filter.casefold()
        return [i for i, row in enumerate(self.rows) if needle in " ".join(row).casefold()]

    @property
    def page_count(self) -> int:
        return max(1, -(-len(self.visible()) // MENU_PAGE_SIZE))


HELP = (
    "[bold]number[/] enter  [bold]b[/] back  [bold]q[/] quit  [bold]n[/]/[bold]p[/] next/prev page  "
    "[bold]/text[/] filter ([bold]/[/] clears)  [bold]d[/] download all below  [bold]o[/] open in browser  "
    "[bold]h[/] help"
)


class Navigator:
    """Numbered menus Archive > Fondo > Serie > Tipologia > Anno > Registers > Pages."""

    def __init__(self, portal: Portal, options: DownloadOptions, out: Console | None = None) -> None:
        self.portal = portal
        self.options = options
        self.out = out or console
        self.stack: list[Level] = []

    # ------------------------------------------------------------------ main loop
    def run(self) -> int:
        self.out.print(f"[bold]antenati-dl {__version__}[/] - interactive navigator. {HELP}")
        try:
            with self.out.status("Loading archives..."):
                self.stack.append(self.archives_level())
        except KeyboardInterrupt:
            return EXIT_INTERRUPTED
        dirty = True
        while self.stack:
            level = self.stack[-1]
            if dirty:
                self.render(level)
            try:
                raw = Prompt.ask(self._prompt(level), console=self.out)
            except EOFError:
                self.out.print()
                return EXIT_OK
            except KeyboardInterrupt:
                self.out.print()
                return EXIT_INTERRUPTED
            try:
                result = self.handle(level, (raw or "").strip())
            except Notice as notice:
                self.out.print(f"[yellow]{escape(str(notice))}[/]")
                result = False
            except (PortalError, requests.RequestException, ValueError, CliError) as exc:
                self.out.print(f"[red]Error: {escape(str(exc))}[/]")
                result = False
            except KeyboardInterrupt:
                self.out.print("[yellow]Interrupted.[/]")
                result = False
            if result is None:
                return EXIT_OK
            dirty = result
        return EXIT_OK

    def _prompt(self, level: Level) -> str:
        extra = []
        if level.page_count > 1:
            extra.append("n/p")
        if level.targets:
            extra.append("d [pages]" if level.kind == "pages" else "d")
        if level.kind in ("tipologia", "anno"):
            extra.append("r")
        hint = ", ".join(["#", "b", "q", "/filter", *extra, "o", "h"])
        return f"[bold]{level.kind}[/] [dim]({hint})[/]"

    def handle(self, level: Level, raw: str) -> bool | None:
        """Execute one command. Returns None to quit, True to redraw the menu, False otherwise."""
        if not raw:
            return False
        cmd, _, arg = raw.partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        if raw.startswith("/"):
            level.filter, level.page = raw[1:].strip(), 0
            if level.filter and not level.visible():
                self.out.print(f"[yellow]Nothing matches {level.filter!r}[/]")
            return True
        if raw.isdigit():
            return self.enter(level, int(raw))
        if cmd in ("q", "quit", "exit"):
            return None
        if cmd in ("b", "back", ".."):
            if len(self.stack) > 1:
                self.stack.pop()
                return True
            raise Notice("Already at the top level")
        if cmd in ("n", "next", "p", "prev"):
            step = 1 if cmd.startswith("n") else -1
            new = level.page + step
            if not 0 <= new < level.page_count:
                raise Notice("No more pages")
            level.page = new
            return True
        if cmd in ("h", "?", "help"):
            self.print_help()
            return False
        if cmd == "r" and level.kind in ("tipologia", "anno") and level.query is not None:
            self.push(lambda: self.registers_level(level.query, "all registers"))  # type: ignore[arg-type]
            return True
        if cmd in ("d", "download"):
            return self.download(level, arg or None)
        if cmd in ("o", "open"):
            return self.open(level, arg)
        raise Notice(f"Unknown command {raw!r} (h for help)")

    def print_help(self) -> None:
        self.out.print(HELP)
        self.out.print(
            "[dim]r at Tipologia/Anno level lists all registers below; at the pages level "
            "'d 1-5,9' downloads only those pages and 'o N' opens page N in the browser.[/]"
        )

    def push(self, build: Callable[[], Level]) -> None:
        with self.out.status("Loading..."):
            self.stack.append(build())

    # ------------------------------------------------------------------ rendering
    def render(self, level: Level) -> None:
        visible = level.visible()
        level.page = min(level.page, max(0, level.page_count - 1))
        start = level.page * MENU_PAGE_SIZE
        chunk = visible[start : start + MENU_PAGE_SIZE]
        crumbs = " > ".join(lv.crumb for lv in self.stack[1:]) or "Portale Antenati"
        self.out.rule(style="dim")
        self.out.print(f"[bold]{escape(crumbs)}[/]")
        if level.note:
            self.out.print(level.note)
        table = _table(None, "#", *level.columns)
        for pos, idx in enumerate(chunk, start=start + 1):
            table.add_row(str(pos), *(escape(c) for c in level.rows[idx]))
        self.out.print(table)
        info = f"{len(visible)} of {len(level.items)} items" if level.filter else f"{len(level.items)} items"
        if level.page_count > 1:
            info += f" - page {level.page + 1}/{level.page_count} (n/p)"
        if level.filter:
            info += f" - filter {level.filter!r} (/ to clear)"
        self.out.print(f"[dim]{info}[/]")

    # ------------------------------------------------------------------ navigation
    def _item(self, level: Level, number: int) -> Any:
        visible = level.visible()
        if not 1 <= number <= len(visible):
            raise Notice(f"Choose a number between 1 and {len(visible)}")
        return level.items[visible[number - 1]]

    def enter(self, level: Level, number: int) -> bool:
        item = self._item(level, number)
        if level.kind == "pages":
            self.show_page(level, item)
            return False
        if level.enter is None:
            raise Notice("Nothing below this level")
        enter = level.enter
        self.push(lambda: enter(item))
        return True

    def open(self, level: Level, arg: str) -> bool:
        if level.url is None:
            raise Notice("Nothing to open here")
        item = self._item(level, int(arg)) if arg.isdigit() else None
        url = level.url(item)
        self.out.print(f"Opening {url}")
        webbrowser.open(url)
        return False

    def show_page(self, level: Level, page: Any) -> None:
        url = level.url(page) if level.url else ""
        self.out.print(f"[bold]Page {page.index}[/] - {escape(page.label)} (image {page.image_id})")
        self.out.print(f"  Viewer: {url}")
        self.out.print(f"  Image:  {page.image_url(self.options.size)}")
        self.out.print(f"[dim]  'd {page.index}' downloads it, 'o {page.index}' opens it in the browser[/]")

    # ------------------------------------------------------------------ download
    def download(self, level: Level, spec: str | None) -> bool:
        if level.targets is None:
            raise Notice("Select an archive first: downloading every archive is not allowed")
        if spec and level.kind != "pages":
            raise Notice("A page selection is only allowed when viewing a register's pages")
        with self.out.status("Estimating size..."):
            targets = level.targets(spec)
            estimate = Downloader(self.portal, self.options).estimate(targets)
        if estimate.registers == 0:
            raise Notice("Nothing to download here")
        dest = Path(self.options.output_dir).resolve()
        self.out.print(f"Download [bold]{estimate.describe()}[/] to {escape(str(dest))}")
        try:
            confirmed = Confirm.ask("Start the download?", default=False, console=self.out)
        except EOFError:
            confirmed = False
        if not confirmed:
            self.out.print("Aborted.")
            return False
        run_download(self.portal, self.options, targets)
        return False

    # ------------------------------------------------------------------ level builders
    def archives_level(self) -> Level:
        archives = self.portal.archives()
        return Level(
            kind="archives",
            crumb="Archivi",
            columns=["Archivio", "Id"],
            items=archives,
            rows=[[a.name, str(a.id)] for a in archives],
            enter=self.fondi_level,
            url=lambda item: item.explore_url if item else EXPLORE_URL,
        )

    def fondi_level(self, archive: Archive) -> Level:
        fondi = self.portal.fondi(archive.id)
        if not fondi:
            raise Notice(f"{archive.name} has no fondi")
        query = SearchQuery(archivio=archive.id)
        return Level(
            kind="fondi",
            crumb=archive.name,
            columns=["Fondo", "Series", "Id"],
            items=fondi,
            rows=[[f.name, str(len(f.series)), str(f.id)] for f in fondi],
            enter=lambda fondo: self.series_level(archive, fondo),
            targets=lambda _spec: [DownloadTarget.from_query(query)],
            url=lambda _item: archive.explore_url,
            query=query,
        )

    def series_level(self, archive: Archive, fondo: Fondo) -> Level:
        if not fondo.series:
            raise Notice(f"{fondo.name} has no series")
        return Level(
            kind="series",
            crumb=fondo.name,
            columns=["Serie", "Id"],
            items=list(fondo.series),
            rows=[[s.name, str(s.id)] for s in fondo.series],
            enter=lambda serie: self.search_level(SearchQuery(serie=serie.id), serie.name),
            targets=lambda _spec: [DownloadTarget.from_query(fondo_query(self.portal, archive, fondo))],
            url=lambda item: search_url(SearchQuery(serie=item.id)) if item else archive.explore_url,
        )

    def search_level(self, query: SearchQuery, crumb: str) -> Level:
        """Next level below a search: Tipologia facet, then Anno facet, then the registers."""
        result = self.portal.search(query, size=SMALL_RESULT)
        if result.total == 0:
            raise Notice("No registers here")
        for field_name, kind in (("tipologia_ss", "tipologia"), ("anni_is", "anno")):
            facet = result.facets.get(field_name)
            if _has_facet(query, field_name) or not facet or not facet.terms:
                continue
            terms = facet.terms
            return Level(
                kind=kind,
                crumb=crumb,
                columns=[kind.capitalize(), "Registers"],
                items=terms,
                rows=[[t.value, str(t.count)] for t in terms],
                enter=lambda term: self.search_level(query.refine(term), term.value),
                targets=lambda _spec: [DownloadTarget.from_query(query)],
                url=lambda term: search_url(query.refine(term) if term else query),
                query=query,
                note=f"{plural(result.total, 'register')} by {kind}: pick a number, or 'r' to list them all",
            )
        return self.registers_level(query, crumb, result)

    def registers_level(self, query: SearchQuery, crumb: str, result: SearchPage | None = None) -> Level:
        if result is not None and result.total <= len(result.registers):
            registers = list(result.registers)
        else:
            registers = list(self.portal.iter_registers(query))
        if not registers:
            raise Notice("No registers here")
        return Level(
            kind="registers",
            crumb=crumb,
            columns=["Title", "Tipologia", "Segnatura", "Register id"],
            items=registers,
            rows=[[r.title, r.tipologia, r.segnatura, r.id] for r in registers],
            enter=self.pages_level,
            targets=lambda _spec: [DownloadTarget.from_query(query)],
            url=lambda reg: reg.url if reg else search_url(query),
            query=query,
        )

    def pages_level(self, register: RegisterSummary) -> Level:
        manifest = self.portal.manifest(register)
        meta = " | ".join(
            v
            for v in (
                manifest.title,
                plural(len(manifest.pages), "page"),
                manifest.tipologia,
                manifest.dating,
            )
            if v
        )

        def targets(spec: str | None) -> list[DownloadTarget]:
            if spec:
                parse_page_ranges(spec, len(manifest.pages))  # raises ValueError -> shown to the user
            return [DownloadTarget(register=manifest, pages=spec)]

        def url(page: Any | None) -> str:
            return f"{register.url}{page.image_id}" if page is not None else register.url

        return Level(
            kind="pages",
            crumb=f"{register.title} ({register.id})",
            columns=["Label", "Image id"],
            items=list(manifest.pages),
            rows=[[p.label, p.image_id] for p in manifest.pages],
            targets=targets,
            url=url,
            note=f"[bold]{escape(meta)}[/]",
        )


# =========================================================================== argument parser
def _add_filters(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("filters")
    group.add_argument("--archive", metavar="ID_OR_NAME", help="archive id or part of its name")
    group.add_argument("--fondo", metavar="ID_OR_NAME", help="fondo of the archive (needs --archive)")
    group.add_argument("--serie", metavar="ID", help="serie id (or name, with --archive)")
    group.add_argument("--localita", metavar="NAME", help="località (comune) name")
    group.add_argument("--tipologia", metavar="VALUE", help="e.g. Nati, Morti, Matrimoni (case-insensitive)")
    group.add_argument("--anno", metavar="YEAR", help="year, e.g. 1911")


def _add_download_options(parser: argparse.ArgumentParser, dry_run: bool = True) -> None:
    group = parser.add_argument_group("download options")
    group.add_argument(
        "-o", "--output", default="Download", metavar="DIR", help="output folder (default: ./Download)"
    )
    group.add_argument(
        "--size",
        type=_size_arg,
        default="full",
        metavar="{full,max,WIDTH}",
        help="image size: full (original), max (~10 MP) or a width in pixels (default: full)",
    )
    group.add_argument(
        "-j",
        "--workers",
        type=_workers_arg,
        default=4,
        metavar="N",
        help=f"parallel downloads, 1-{MAX_WORKERS} (default: 4)",
    )
    group.add_argument(
        "--overwrite", action="store_true", help="download pages again even if already on disk"
    )
    group.add_argument("--pdf", action="store_true", help="also build a PDF per register (needs img2pdf)")
    group.add_argument("--no-metadata", action="store_true", help="do not write register.json")
    if dry_run:
        group.add_argument("--dry-run", action="store_true", help="only show what would be downloaded")


def build_parser() -> argparse.ArgumentParser:
    verbose = argparse.ArgumentParser(add_help=False)
    verbose.add_argument(
        "-v", "--verbose", action="count", default=argparse.SUPPRESS, help="more logging (-vv for debug)"
    )

    parser = argparse.ArgumentParser(
        prog="antenati-dl",
        description="Browse and download registers from the Portale Antenati (antenati.cultura.gov.it).",
        epilog="Hierarchy: Archivio > Fondo > Serie > Tipologia > Anno > Registro > Pagine. "
        "Run 'antenati-dl interactive' to navigate it with menus.",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0, help="more logging (-vv for debug)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    def add(name: str, help_text: str, **kwargs: Any) -> argparse.ArgumentParser:
        return sub.add_parser(name, help=help_text, description=help_text, parents=[verbose], **kwargs)

    p = add("archives", "list the archives (Archivi di Stato)")
    p.add_argument("filter", nargs="?", help="id or part of the name")
    p.set_defaults(func=cmd_archives)

    p = add("fondi", "list the fondi of an archive")
    p.add_argument("archive", metavar="ARCHIVE", help="archive id or part of its name")
    p.set_defaults(func=cmd_fondi)

    p = add("series", "list the series (comuni) of an archive")
    p.add_argument("archive", metavar="ARCHIVE", help="archive id or part of its name")
    p.add_argument("filter", nargs="?", help="part of the serie name")
    p.add_argument("--fondo", metavar="ID_OR_NAME", help="only this fondo")
    p.set_defaults(func=cmd_series)

    p = add("browse", "drill down the hierarchy: prints the next level (and registers when few)")
    _add_filters(p)
    p.add_argument("--registers", action="store_true", help="always list the registers")
    p.set_defaults(func=cmd_browse)

    p = add("search", "free search of registers")
    p.add_argument("--localita", metavar="NAME")
    p.add_argument("--tipologia", metavar="VALUE")
    p.add_argument("--anno", metavar="YEAR")
    p.add_argument("--archive", metavar="ID_OR_NAME")
    p.add_argument("--serie", type=int, metavar="ID")
    p.add_argument("--nominativi", action="store_true", help="only registers with indexed names")
    p.add_argument("--sort", choices=list(SORT_CHOICES), default="relevance")
    p.add_argument("--page", type=_positive_int, default=1)
    p.add_argument("--size", type=_positive_int, default=20, help="results per page (default: 20)")
    p.set_defaults(func=cmd_search)

    p = add("info", "show a register's metadata (and pages)")
    p.add_argument("ref", metavar="REF", help="register id (an_ua...), ark/page URL or manifest URL")
    p.add_argument("--pages", action="store_true", help="list the pages")
    p.set_defaults(func=cmd_info)

    p = add("download", "download one or more registers (or single pages)")
    p.add_argument("refs", nargs="+", metavar="REF", help="register id, ark URL, page URL or manifest URL")
    p.add_argument(
        "--pages", metavar="SPEC", help="pages to download, e.g. 1-10,15,20- (applies to each REF)"
    )
    _add_download_options(p)
    p.set_defaults(func=cmd_download)

    p = add("download-all", "download every register matching the filters (bulk)", aliases=["bulk"])
    _add_filters(p)
    p.add_argument("--limit", type=_positive_int, metavar="N", help="at most N registers")
    p.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    _add_download_options(p)
    p.set_defaults(func=cmd_download_all)

    p = add("interactive", "navigate the archives with numbered menus", aliases=["shell"])
    _add_download_options(p, dry_run=False)
    p.set_defaults(func=cmd_interactive)

    p = add("gui", "start the graphical interface")
    p.set_defaults(func=cmd_gui, no_portal=True)
    return parser


# =========================================================================== entry point
def main(argv: Sequence[str] | None = None) -> int:
    _configure_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    verbosity = int(getattr(args, "verbose", 0) or 0)
    _configure_logging(verbosity)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_ERROR
    try:
        portal = None if getattr(args, "no_portal", False) else make_portal()
        return int(func(portal, args))
    except KeyboardInterrupt:
        err_console.print("[yellow]Interrupted.[/]")
        return EXIT_INTERRUPTED
    except (CliError, PortalError, requests.RequestException, ValueError) as exc:
        if verbosity:
            log.exception("Command failed")
        message = str(exc) or exc.__class__.__name__
        if isinstance(exc, requests.RequestException):
            message = f"Network error: {message}"
        err_console.print(f"[red]Error: {escape(message)}[/]")
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
