from __future__ import annotations

from collections import Counter
from pathlib import Path

from .c_symbols import extract_c_symbols
from .models import CodeSymbol
from .python_symbols import extract_python_symbols

PYTHON_EXTENSIONS = {".py"}
C_EXTENSIONS = {".c", ".h", ".cpp", ".hpp", ".cc"}


def extract_code_symbols(
    file_path: Path,
    relative_path: str,
    *,
    source: str | None = None,
) -> tuple[list[CodeSymbol], Counter[str]]:
    """Dispatch symbol extraction to appropriate language extractor based on file suffix."""
    suffix = file_path.suffix.lower()

    if suffix in PYTHON_EXTENSIONS:
        return extract_python_symbols(file_path, relative_path, source=source)

    if suffix in C_EXTENSIONS:
        return extract_c_symbols(file_path, relative_path, source=source)

    return [], Counter()
