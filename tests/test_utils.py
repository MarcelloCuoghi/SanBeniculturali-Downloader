from __future__ import annotations

import pytest

from antenati_dl.utils import (
    AVERAGE_PAGE_BYTES,
    estimate_page_bytes,
    format_bytes,
    parse_page_ranges,
    sanitize_filename,
)


# --------------------------------------------------------------------------- sanitize_filename
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Acquaro", "Acquaro"),
        ('a<b>c:d"e/f\\g|h?i*j', "a_b_c_d_e_f_g_h_i_j"),
        ("tab\there\x00nul\nx", "tab_here_nul_x"),  # control characters are invalid
        ("  many   spaces  ", "many spaces"),
        ("trailing dots...", "trailing dots"),
        ("trailing dot and space. ", "trailing dot and space"),
        ("", "_"),
        ("...", "_"),
        ("Matrimoni, pubblicazioni", "Matrimoni, pubblicazioni"),
        ("Archivio di Stato dell’Aquila", "Archivio di Stato dell’Aquila"),
    ],
)
def test_sanitize_filename(name, expected):
    assert sanitize_filename(name) == expected


@pytest.mark.parametrize("reserved", ["CON", "con", "NUL", "COM1", "LPT9", "aux.txt", "PRN"])
def test_sanitize_filename_reserved_windows_names(reserved):
    assert sanitize_filename(reserved) == f"_{reserved}"


def test_sanitize_filename_not_reserved_lookalikes():
    assert sanitize_filename("CONSOLE") == "CONSOLE"
    assert sanitize_filename("COM10") == "COM10"


def test_sanitize_filename_unicode_normalized_to_nfc():
    decomposed = "Località"  # a + combining grave
    assert sanitize_filename(decomposed) == "Località"


def test_sanitize_filename_truncates_without_trailing_dot_or_space():
    long = "x" * 79 + ". tail"
    out = sanitize_filename(long, max_length=80)
    assert out == "x" * 79
    assert len(sanitize_filename("y" * 300)) == 80
    assert len(sanitize_filename("y" * 300, max_length=100)) == 100


# --------------------------------------------------------------------------- parse_page_ranges
@pytest.mark.parametrize("spec", [None, "", "  ", "all", "ALL", "*", "tutte", "tutto"])
def test_parse_page_ranges_all(spec):
    assert parse_page_ranges(spec, 5) == [1, 2, 3, 4, 5]


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("3", [3]),
        ("1-3", [1, 2, 3]),
        ("1-3,5", [1, 2, 3, 5]),
        ("5,1-2", [1, 2, 5]),
        ("-3", [1, 2, 3]),
        ("8-", [8, 9, 10]),
        ("last", [10]),
        ("ultima", [10]),
        ("8-last", [8, 9, 10]),
        ("3-1", [1, 2, 3]),  # reversed range is normalised
        ("1-3,2-4", [1, 2, 3, 4]),  # overlaps de-duplicated
        (" 1 - 2 , 4 ", [1, 2, 4]),  # spaces ignored
        ("1,,2,", [1, 2]),  # empty parts ignored
        ("-", list(range(1, 11))),
    ],
)
def test_parse_page_ranges(spec, expected):
    assert parse_page_ranges(spec, 10) == expected


@pytest.mark.parametrize("spec", ["0", "11", "5-11", "0-3", ",", "abc", "1-x"])
def test_parse_page_ranges_invalid(spec):
    with pytest.raises(ValueError):
        parse_page_ranges(spec, 10)


# --------------------------------------------------------------------------- format_bytes
@pytest.mark.parametrize(
    ("num", "expected"),
    [
        (0, "0 B"),
        (1023, "1023 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (5 * 1024**2, "5.0 MB"),
        (3 * 1024**3, "3.0 GB"),
        (2 * 1024**4, "2.0 TB"),
        (5000 * 1024**4, "5000.0 TB"),
    ],
)
def test_format_bytes(num, expected):
    assert format_bytes(num) == expected


# --------------------------------------------------------------------------- estimate_page_bytes
def test_estimate_page_bytes_named_sizes():
    assert estimate_page_bytes("full") == AVERAGE_PAGE_BYTES["full"]
    assert estimate_page_bytes("max") == AVERAGE_PAGE_BYTES["max"]


def test_estimate_page_bytes_width_scales_with_area():
    assert estimate_page_bytes(6000) == AVERAGE_PAGE_BYTES["full"]
    assert estimate_page_bytes("3000") == AVERAGE_PAGE_BYTES["full"] // 4
    assert estimate_page_bytes(100) == 20_000  # floor


def test_estimate_page_bytes_unknown_falls_back_to_full():
    assert estimate_page_bytes("weird") == AVERAGE_PAGE_BYTES["full"]
