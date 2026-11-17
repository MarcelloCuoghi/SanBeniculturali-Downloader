"""Offline tests of the command line interface: the portal is replaced by one serving saved pages."""

from __future__ import annotations

import io
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from antenati_dl import cli
from antenati_dl.models import FacetTerm, Manifest, RegisterSummary, SearchPage, SearchQuery
from antenati_dl.portal import Portal, parse_archives, parse_fondi, parse_manifest, parse_search

FIXTURES = Path(__file__).parent / "fixtures"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 120 + b"\xff\xd9"


def _text(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


class FakeHttp:
    """Serves a tiny JPEG for every image request and counts them."""

    def __init__(self) -> None:
        self.images: list[str] = []

    def get_image(self, url: str, cancelled=None) -> bytes:  # noqa: ANN001 - mirrors HttpClient
        self.images.append(url)
        return JPEG


class FakePortal(Portal):
    """A :class:`Portal` answering from ``tests/fixtures`` (serie 37842016 "Acquaro" of archive 145)."""

    def __init__(self) -> None:
        super().__init__(client=FakeHttp())  # type: ignore[arg-type]
        self.queries: list[SearchQuery] = []

    def archives(self, refresh: bool = False):  # noqa: ANN201
        return parse_archives(_text("explore_archives.html"))

    def fondi(self, archive_id: int):  # noqa: ANN201
        return parse_fondi(_text("archive_145.html"), archive_id)

    def _search_raw(self, query: SearchQuery, page: int, size: int) -> SearchPage:
        self.queries.append(query)
        return parse_search(_text("search_serie.html"), query, page=page, size=size)

    def manifest(self, ref: str | RegisterSummary) -> Manifest:
        manifest = parse_manifest(json.loads(_text("manifest.json")), url="https://example.org/manifest")
        manifest.register_id = manifest.register_id or "an_ua37845354"
        return manifest


@pytest.fixture
def portal(monkeypatch: pytest.MonkeyPatch) -> FakePortal:
    fake = FakePortal()
    monkeypatch.setattr(cli, "make_portal", lambda: fake)
    for console in (cli.console, cli.err_console):  # avoid wrapping at 80 columns under capture
        monkeypatch.setattr(console, "width", 200)
    return fake


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


# --------------------------------------------------------------------------- helpers
def test_match_term_is_case_insensitive_and_keeps_quoted_query() -> None:
    terms = [
        FacetTerm("tipologia_ss", "Matrimoni", 3, "tipologia_ss:Matrimoni"),
        FacetTerm("tipologia_ss", "Matrimoni, pubblicazioni", 2, 'tipologia_ss:"Matrimoni, pubblicazioni"'),
        FacetTerm("tipologia_ss", "Nati", 5, "tipologia_ss:Nati"),
    ]
    assert cli.match_term(terms, "nati", "tipologia").query == "tipologia_ss:Nati"
    assert cli.match_term(terms, "matrimoni", "tipologia").value == "Matrimoni"
    term = cli.match_term(terms, "MATRIMONI, PUBBLICAZIONI", "tipologia")
    assert term.query == 'tipologia_ss:"Matrimoni, pubblicazioni"'
    assert cli.match_term(terms, "pubbl", "tipologia").value == "Matrimoni, pubblicazioni"
    with pytest.raises(cli.CliError, match="Ambiguous"):
        cli.match_term(terms, "Matr", "tipologia")
    with pytest.raises(cli.CliError, match="Available"):
        cli.match_term(terms, "Morti", "tipologia")


def test_match_term_years_must_be_exact() -> None:
    terms = [FacetTerm("anni_is", "1911", 1, "anni_is:1911"), FacetTerm("anni_is", "1912", 1, "anni_is:1912")]
    assert cli.match_term(terms, "1911", "anno").query == "anni_is:1911"
    with pytest.raises(cli.CliError):
        cli.match_term(terms, "191", "anno")


@pytest.mark.parametrize("value", ["full", "max", "2000", "FULL"])
def test_size_argument_accepts_valid_values(value: str) -> None:
    assert cli._size_arg(value) == value.lower()


@pytest.mark.parametrize("value", ["0", "huge", "-5"])
def test_size_argument_rejects_invalid_values(value: str) -> None:
    with pytest.raises(Exception):  # noqa: B017 - argparse.ArgumentTypeError
        cli._size_arg(value)


def test_workers_are_capped(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["download", "an_ua1", "-j", "17"])
    assert exc.value.code == 2


# --------------------------------------------------------------------------- browsing
def test_archives_with_filter(portal: FakePortal, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "archives", "vibo")
    assert code == 0
    assert "Archivio di Stato di Vibo Valentia" in out and "145" in out


def test_archives_unicode_names(portal: FakePortal, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "archives", "aquila")
    assert code == 0
    assert "Archivio di Stato dell’Aquila" in out


def test_fondi(portal: FakePortal, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "fondi", "145")
    assert code == 0
    assert "Stato civile italiano" in out and "37842015" in out and "60" in out


def test_series_filter(portal: FakePortal, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "series", "145", "acqu", "--fondo", "stato civile")
    assert code == 0
    assert "Acquaro" in out and "37842016" in out and "Arena" not in out


def test_unknown_archive_is_a_friendly_error(portal: FakePortal, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "fondi", "no such archive")
    assert code == 2
    assert "Error" in out and "Traceback" not in out


def test_browse_without_filters_lists_archives(
    portal: FakePortal, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = run(capsys, "browse")
    assert code == 0 and "Archives (98)" in out


def test_browse_archive_and_fondo_list_series(portal: FakePortal, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "browse", "--archive", "145", "--fondo", "Stato civile italiano")
    assert code == 0 and "Acquaro" in out and "Series of Stato civile italiano (60)" in out


def test_browse_serie_shows_tipologie(portal: FakePortal, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "browse", "--serie", "37842016")
    assert code == 0
    assert "Tipologia" in out and "Matrimoni, pubblicazioni" in out and "Nati" in out
    assert "Vibo Valentia > Stato civile italiano > Acquaro" in out


def test_browse_refines_with_facet_terms(portal: FakePortal, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "browse", "--serie", "37842016", "--tipologia", "nati", "--anno", "1911")
    assert code == 0
    assert portal.queries[-1].facets == ("tipologia_ss:Nati", "anni_is:1911")
    assert "Acquaro > Nati > 1911" in out


def test_browse_serie_by_name_needs_archive(portal: FakePortal, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "browse", "--serie", "Acquaro")
    assert code == 2 and "--archive" in out
    code, out = run(capsys, "browse", "--archive", "vibo", "--serie", "acquaro")
    assert code == 0 and portal.queries[0].serie == 37842016


def test_localita_keeps_the_fondo_restriction(portal: FakePortal, capsys: pytest.CaptureFixture[str]) -> None:
    # --fondo used to be silently dropped when --localita was given (bulk download of every fondo)
    code, out = run(
        capsys, "browse", "--archive", "145", "--fondo", "Stato civile italiano", "--localita", "Acquaro"
    )
    assert code == 0, out
    query = portal.queries[-1]
    assert query.localita == "Acquaro" and query.archivio == 145
    assert any(f.startswith("primo_livello_label_s:") for f in query.facets)


def test_search(portal: FakePortal, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "search", "--serie", "37842016", "--sort", "oldest", "--size", "20")
    assert code == 0
    assert "133" in out and "an_ua37845354" in out
    assert portal.queries[-1].sort == "estremoRemoto_i asc"


def test_info_pages(portal: FakePortal, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "info", "an_ua37845354", "--pages")
    assert code == 0
    assert "LzblN79" in out and "pag. 1" in out and "Morti" in out


# --------------------------------------------------------------------------- downloads
def test_download_pages_and_resume(
    portal: FakePortal, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, out = run(capsys, "download", "an_ua37845354", "--pages", "1-2", "-o", str(tmp_path))
    assert code == 0, out
    files = sorted(p.name for p in tmp_path.rglob("*.jpg"))
    assert files == ["0001.jpg", "0002.jpg"]
    assert len(portal.http.images) == 2
    code, out = run(capsys, "download", "an_ua37845354", "--pages", "1-2", "-o", str(tmp_path))
    assert code == 0
    assert "already on disk" in out and len(portal.http.images) == 2


def test_download_dry_run_downloads_nothing(
    portal: FakePortal, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, out = run(capsys, "download", "an_ua37845354", "--pages", "3", "--dry-run", "-o", str(tmp_path))
    assert code == 0
    assert "1/55" in out and "Estimate" in out
    assert not list(tmp_path.iterdir()) and not portal.http.images


def test_download_rejects_bad_page_selection(portal: FakePortal, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "download", "an_ua37845354", "--pages", "1-x", "--dry-run")
    assert code == 2 and "Invalid page selection" in out
    code, out = run(capsys, "download", "an_ua37845354", "--pages", "50-60")
    assert code == 2 and "outside" in out


def test_download_open_ended_page_range(portal: FakePortal, capsys: pytest.CaptureFixture[str]) -> None:
    # "20-" is the documented syntax; validating it must not expand 20..10**9 in memory
    code, out = run(capsys, "download", "an_ua37845354", "--pages", "50-", "--dry-run")
    assert code == 0, out
    assert "6/55" in out


def test_download_to_unusable_folder_is_reported_not_a_traceback(
    portal: FakePortal, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    code, out = run(capsys, "download", "an_ua37845354", "--pages", "1", "-o", str(blocker / "out"))
    assert code == cli.EXIT_FAILED, out
    assert "Traceback" not in out and "Registers failed" in out
    assert not portal.http.images


def test_bulk_download_requires_a_filter(portal: FakePortal, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "download-all", "--tipologia", "Nati")
    assert code == 2 and "Refusing" in out


def test_bulk_download_dry_run(
    portal: FakePortal, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, out = run(
        capsys, "download-all", "--serie", "37842016", "--limit", "3", "--dry-run", "-o", str(tmp_path)
    )
    assert code == 0
    assert "3 registers would be downloaded" in out and "Estimate: 3 registers" in out
    assert not portal.http.images


def test_bulk_download_can_be_declined(
    portal: FakePortal, capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
    code, out = run(capsys, "download-all", "--serie", "37842016", "-o", str(tmp_path))
    assert code == 0 and "Aborted" in out and not portal.http.images


# --------------------------------------------------------------------------- interactive
def test_interactive_navigation(
    portal: FakePortal, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # filter archives, enter Vibo > Stato civile > page 2 of the series and back > Acquaro > Nati > 1911
    script = "/vibo\n1\n1\nn\np\nxyz\n999\n1\nnati\n/Nati\n1\n1\nb\nq\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(script))
    code, out = run(capsys, "interactive")
    assert code == 0
    assert "Stato civile italiano" in out
    assert "page 2/2" in out  # 60 series, 30 per page
    assert "Unknown command" in out and "between 1 and" in out
    assert "Matrimoni, pubblicazioni" in out
    assert portal.queries[-1].facets == ("tipologia_ss:Nati", "anni_is:1911")


def test_interactive_download_pages(
    portal: FakePortal, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    nav = cli.Navigator(portal, cli.DownloadOptions(output_dir=tmp_path))
    register = RegisterSummary("an_ua37845354", "Registro: 1911", "Morti")
    nav.stack.append(nav.pages_level(register))
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))
    assert nav.handle(nav.stack[-1], "d 1,3") is False
    assert sorted(p.name for p in tmp_path.rglob("*.jpg")) == ["0001.jpg", "0003.jpg"]
    with pytest.raises(ValueError):
        nav.handle(nav.stack[-1], "d 99")


def test_interactive_refuses_download_of_everything(portal: FakePortal) -> None:
    nav = cli.Navigator(portal, cli.DownloadOptions())
    level = nav.archives_level()
    with pytest.raises(cli.Notice):
        nav.handle(level, "d")


def test_interactive_ends_on_eof(
    portal: FakePortal, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    code, _out = run(capsys, "shell")
    assert code == 0


def test_ctrl_c_cancels_the_download(
    portal: FakePortal, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    started = threading.Event()
    release = threading.Event()

    def slow_image(url: str, cancelled=None) -> bytes:  # noqa: ANN001
        started.set()
        release.wait(5)
        return JPEG

    monkeypatch.setattr(portal.http, "get_image", slow_image)

    class InterruptedThread(threading.Thread):
        """Simulates Ctrl+C arriving in the main thread while it waits for the download."""

        def join(self, timeout: float | None = None) -> None:
            if not getattr(self, "_interrupted", False):
                started.wait(5)
                self._interrupted = True
                raise KeyboardInterrupt
            release.set()
            super().join(timeout)

    # patch only the module seen by cli (the thread pool must keep the real Thread class)
    monkeypatch.setattr(cli, "threading", SimpleNamespace(Thread=InterruptedThread, Event=threading.Event))
    code, out = run(capsys, "download", "an_ua37845354", "--pages", "1-8", "-j", "2", "-o", str(tmp_path))
    assert code == cli.EXIT_INTERRUPTED
    assert "Cancelling" in out and "Download cancelled" in out
    assert len(list(tmp_path.rglob("*.jpg"))) < 8
