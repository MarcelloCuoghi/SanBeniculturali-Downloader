"""User settings persisted with :class:`QSettings`."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QSettings

from ..download import DownloadOptions

SIZE_FULL = "full"
SIZE_MAX = "max"
SIZE_CUSTOM = "custom"


def default_output_dir() -> Path:
    downloads = Path.home() / "Downloads"
    if downloads.is_dir():
        return downloads / "Antenati"
    return Path.cwd() / "Download"


def _to_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _to_int(value: object, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


@dataclass
class AppSettings:
    output_dir: Path = field(default_factory=default_output_dir)
    size_mode: str = SIZE_FULL  # "full", "max" or "custom"
    custom_width: int = 2000
    workers: int = 4
    overwrite: bool = False
    pdf: bool = False

    @property
    def image_size(self) -> str:
        """Value for :attr:`DownloadOptions.size`."""
        if self.size_mode == SIZE_CUSTOM:
            return str(self.custom_width)
        return SIZE_MAX if self.size_mode == SIZE_MAX else SIZE_FULL

    def describe_size(self) -> str:
        if self.size_mode == SIZE_CUSTOM:
            return f"{self.custom_width}px wide"
        return "max (~10 MP)" if self.size_mode == SIZE_MAX else "original"

    def download_options(self) -> DownloadOptions:
        return DownloadOptions(
            output_dir=Path(self.output_dir),
            size=self.image_size,
            workers=self.workers,
            overwrite=self.overwrite,
            pdf=self.pdf,
        )

    # ------------------------------------------------------------------ persistence
    @classmethod
    def load(cls, qs: QSettings) -> AppSettings:
        s = cls()
        out = qs.value("download/output_dir")
        if out:
            s.output_dir = Path(str(out))
        mode = str(qs.value("download/size_mode", SIZE_FULL))
        s.size_mode = mode if mode in (SIZE_FULL, SIZE_MAX, SIZE_CUSTOM) else SIZE_FULL
        s.custom_width = _to_int(qs.value("download/custom_width"), 2000, 100, 20000)
        s.workers = _to_int(qs.value("download/workers"), 4, 1, 16)
        s.overwrite = _to_bool(qs.value("download/overwrite"), False)
        s.pdf = _to_bool(qs.value("download/pdf"), False)
        return s

    def save(self, qs: QSettings) -> None:
        qs.setValue("download/output_dir", str(self.output_dir))
        qs.setValue("download/size_mode", self.size_mode)
        qs.setValue("download/custom_width", int(self.custom_width))
        qs.setValue("download/workers", int(self.workers))
        qs.setValue("download/overwrite", bool(self.overwrite))
        qs.setValue("download/pdf", bool(self.pdf))
        qs.sync()
