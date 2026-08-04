from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import Any

from .models import CodeSymbol, PythonSymbol

logger = logging.getLogger(__name__)

try:
    import ast_grep_py
    HAS_AST_GREP = True
except ImportError:
    HAS_AST_GREP = False

LANGUAGE_MAP = {
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
}

FUNCTION_KINDS = {
    "c": ["function_definition"],
    "cpp": ["function_definition"],
    "python": ["function_definition"],
    "javascript": ["function_declaration", "method_definition", "arrow_function"],
    "typescript": ["function_declaration", "method_definition", "arrow_function"],
    "go": ["function_declaration", "method_declaration"],
    "rust": ["function_item"],
}

CLASS_STRUCT_KINDS = {
    "c": ["struct_specifier", "enum_specifier", "union_specifier"],
    "cpp": ["class_specifier", "struct_specifier", "enum_specifier"],
    "python": ["class_definition"],
    "javascript": ["class_declaration"],
    "typescript": ["class_declaration", "interface_declaration"],
    "go": ["type_spec"],
    "rust": ["struct_item", "enum_item", "trait_item", "impl_item"],
}


def extract_ast_grep_symbols(
    file_path: Path,
    relative_path: str,
    *,
    source: str | None = None,
) -> tuple[list[CodeSymbol], Counter[str]]:
    """Extract code symbols across multiple languages using ast-grep AST engine."""
    if not HAS_AST_GREP:
        return [], Counter()

    suffix = file_path.suffix.lower()
    lang = LANGUAGE_MAP.get(suffix)
    if not lang:
        return [], Counter()

    if source is None:
        try:
            source = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return [], Counter()

    symbols: list[CodeSymbol] = []
    references: Counter[str] = Counter()

    try:
        sg = ast_grep_py.SgRoot(source, lang)
        root = sg.root()
    except Exception as e:
        logger.warning("ast-grep parse error for %s: %s", relative_path, e)
        return [], Counter()

    # 0. Includes & Macros (for C/C++)
    if lang in ("c", "cpp"):
        for inc_node in root.find_all(kind="preproc_include"):
            rng = inc_node.range()
            start_line = rng.start.line + 1
            inc_text = inc_node.text().replace("#include", "").strip(" <>\"")
            symbols.append(
                PythonSymbol(
                    name=inc_text,
                    qualname=inc_text,
                    kind="include",
                    path=relative_path,
                    line=start_line,
                    end_line=start_line,
                    language=lang,
                )
            )
        for def_node in root.find_all(kind="preproc_def"):
            rng = def_node.range()
            start_line = rng.start.line + 1
            name_node = def_node.field("name")
            def_name = name_node.text() if name_node else def_node.text().split()[1] if len(def_node.text().split()) > 1 else "macro"
            symbols.append(
                PythonSymbol(
                    name=def_name,
                    qualname=def_name,
                    kind="macro",
                    path=relative_path,
                    line=start_line,
                    end_line=start_line,
                    language=lang,
                )
            )

    # 1. Functions / Methods
    fn_kinds = FUNCTION_KINDS.get(lang, [])
    for fn_kind in fn_kinds:
        for fn_node in root.find_all(kind=fn_kind):
            rng = fn_node.range()
            start_line = rng.start.line + 1
            end_line = rng.end.line + 1

            name_node = fn_node.field("declarator") or fn_node.field("name")
            name = name_node.text() if name_node else f"fn_{start_line}"
            name = name.split("(")[0].strip("* ").strip()

            call_targets: list[str] = []
            call_nodes = fn_node.find_all(kind="call_expression")
            if call_nodes:
                for c_node in call_nodes:
                    func_part = c_node.field("function") or c_node.field("callee")
                    if func_part:
                        c_name = func_part.text().strip()
                        if c_name and c_name != name and c_name not in call_targets:
                            call_targets.append(c_name)

            is_test = relative_path.startswith("tests/") or name.startswith("test_") or name.startswith("Test")
            symbols.append(
                PythonSymbol(
                    name=name,
                    qualname=name,
                    kind="function",
                    path=relative_path,
                    line=start_line,
                    end_line=end_line,
                    is_test=is_test,
                    calls=call_targets,
                    language=lang,
                )
            )

    # 2. Classes / Structs / Interfaces / Enums
    cls_kinds = CLASS_STRUCT_KINDS.get(lang, [])
    for cls_kind in cls_kinds:
        for cls_node in root.find_all(kind=cls_kind):
            rng = cls_node.range()
            start_line = rng.start.line + 1
            end_line = rng.end.line + 1

            name_node = cls_node.field("name") or cls_node.field("declarator")
            name = name_node.text() if name_node else f"struct_{start_line}"

            kind_label = "class" if "class" in cls_kind else ("struct" if "struct" in cls_kind else "enum")

            symbols.append(
                PythonSymbol(
                    name=name,
                    qualname=name,
                    kind=kind_label,
                    path=relative_path,
                    line=start_line,
                    end_line=end_line,
                    language=lang,
                )
            )

    # 3. References for BM25
    words = source.split()
    for w in words:
        cleaned = w.strip("(),;{}[]'\"").lower()
        if cleaned.isalnum() and len(cleaned) > 1:
            references[cleaned] += 1

    return symbols, references
