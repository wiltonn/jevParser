"""Pick the adapter for a file."""

from __future__ import annotations

from pathlib import Path

from .base import FormatAdapter, UnsupportedFormat
from .xlsx import XlsxAdapter


def adapters() -> list[FormatAdapter]:
    found: list[FormatAdapter] = [XlsxAdapter()]
    from .pdf import PdfAdapter

    # Always registered: sniffing needs no dependency, and parsing without the
    # optional ``pdf`` extra fails with a message naming it.
    found.append(PdfAdapter())
    return found


def detect_format(path: str, filename: str | None = None) -> str:
    """Content first, extension as the tie-breaker.

    ``filename`` is the name to report and to take the extension from, for
    files stored under a content hash.
    """
    name = filename or Path(path).name
    ext = Path(name).suffix.lower()
    candidates = adapters()
    for adapter in sorted(candidates, key=lambda a: ext not in a.extensions):
        if adapter.sniff(path):
            return adapter.format
    raise UnsupportedFormat(f"{name}: not a supported order-guide format "
                            f"(expected an Excel .xlsx export or a PDF)")


def adapter_for(fmt: str) -> FormatAdapter:
    for adapter in adapters():
        if adapter.format == fmt:
            return adapter
    hint = " (install the 'pdf' extra)" if fmt == "pdf" else ""
    raise UnsupportedFormat(f"no adapter for format {fmt!r}{hint}")
