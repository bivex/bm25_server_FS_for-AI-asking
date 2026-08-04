from __future__ import annotations

from collections import Counter
from pathlib import Path

from .ast_grep_symbols import HAS_AST_GREP, extract_ast_grep_symbols
from .c_symbols import extract_c_symbols
from .models import CodeSymbol
from .python_symbols import extract_python_symbols

PYTHON_EXTENSIONS = {".py"}
C_EXTENSIONS = {".c", ".h", ".cpp", ".hpp", ".cc"}
OTHER_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".go", ".rs"}


def extract_code_symbols(
    file_path: Path,
    relative_path: str,
    *,
    source: str | None = None,
) -> tuple[list[CodeSymbol], Counter[str]]:
    """Dispatch symbol extraction to ast-grep, native Python AST, or C extractor based on suffix."""
    suffix = file_path.suffix.lower()

    if suffix in PYTHON_EXTENSIONS:
        return extract_python_symbols(file_path, relative_path, source=source)

    if HAS_AST_GREP:
        symbols, references = extract_ast_grep_symbols(file_path, relative_path, source=source)
        if symbols or references:
            return symbols, references

    if suffix in C_EXTENSIONS:
        return extract_c_symbols(file_path, relative_path, source=source)

    return [], Counter()
