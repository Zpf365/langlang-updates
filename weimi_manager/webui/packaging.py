"""Windows packaging filters for the Web UI desktop bundle."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, TypeVar


_BundleEntry = TypeVar("_BundleEntry", bound=tuple)
_ICU_DATA_DLL = re.compile(r"^icudt\d*\.dll$", re.IGNORECASE)
_ICU_LIBRARY_DLLS = {
    "icuin.dll",
    "icuio.dll",
    "icutu.dll",
    "icuuc.dll",
}


def is_bundled_windows_icu(destination: str) -> bool:
    """Return whether a destination would shadow Windows' Qt-compatible ICU."""

    path = Path(destination)
    if path.parent != Path("."):
        return False
    name = path.name.casefold()
    return name in _ICU_LIBRARY_DLLS or bool(_ICU_DATA_DLL.fullmatch(name))


def filter_windows_icu(
    entries: Iterable[_BundleEntry],
) -> list[_BundleEntry]:
    """Remove root-level ICU DLLs from a PyInstaller binary collection.

    PyInstaller binary entries are ``(destination, source, type)`` tuples.
    """

    return [
        entry
        for entry in entries
        if not entry or not is_bundled_windows_icu(str(entry[0]))
    ]
