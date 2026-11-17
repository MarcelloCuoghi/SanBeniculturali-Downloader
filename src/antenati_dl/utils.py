"""Small helpers: page range parsing, file name sanitizing, size formatting."""

from __future__ import annotations

import re
import unicodedata

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}  # fmt: skip


def sanitize_filename(name: str, max_length: int = 80) -> str:
    """Make ``name`` safe as a single path component on Windows, macOS and Linux."""
    name = unicodedata.normalize("NFC", name)
    name = _INVALID_CHARS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    if not name:
        name = "_"
    if name.upper().split(".")[0] in _RESERVED:
        name = f"_{name}"
    if len(name) > max_length:
        name = name[:max_length].rstrip(". ")
    return name


def parse_page_ranges(spec: str | None, total: int) -> list[int]:
    """Parse a page selection like ``"1-10,15,20-"`` into sorted 1-based indices.

    * ``None``, ``""``, ``"all"`` or ``"*"`` select every page;
    * ``"-5"`` means 1..5, ``"20-"`` means 20..total, ``"last"`` the last page;
    * indices outside ``1..total`` raise :class:`ValueError`.
    """
    if spec is None or spec.strip().lower() in ("", "all", "*", "tutte", "tutto"):
        return list(range(1, total + 1))
    selected: set[int] = set()
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        part = part.lower().replace("last", str(total)).replace("ultima", str(total))
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start = int(start_s) if start_s else 1
            end = int(end_s) if end_s else total
        else:
            start = end = int(part)
        if start > end:
            start, end = end, start
        if start < 1 or end > total:
            raise ValueError(f"Page range {part!r} outside 1-{total}")
        selected.update(range(start, end + 1))
    if not selected:
        raise ValueError(f"Empty page selection: {spec!r}")
    return sorted(selected)


def format_bytes(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024 or unit == "TB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024
    return f"{num:.1f} TB"  # pragma: no cover


#: Average size of a full resolution page, used for disk-space estimates.
AVERAGE_PAGE_BYTES = {"full": 650_000, "max": 600_000}


def estimate_page_bytes(size: str | int) -> int:
    if str(size) in AVERAGE_PAGE_BYTES:
        return AVERAGE_PAGE_BYTES[str(size)]
    try:
        width = int(size)
    except (TypeError, ValueError):
        return AVERAGE_PAGE_BYTES["full"]
    # JPEG size grows roughly with the area; full scans are ~6000px wide.
    return max(20_000, int(AVERAGE_PAGE_BYTES["full"] * (width / 6000) ** 2))
