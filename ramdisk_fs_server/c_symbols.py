from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from .models import PythonSymbol

INCLUDE_RE = re.compile(r'^\s*#\s*include\s+[<"]([^>"]+)[>"]')
DEFINE_RE = re.compile(r'^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)')
STRUCT_CLASS_RE = re.compile(
    r'^\s*(?:typedef\s+)?(struct|enum|union|class)\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s*:\s*(?:public|protected|private)?\s*([A-Za-z_][A-Za-z0-9_]*))?'
)
TYPEDEF_END_RE = re.compile(r'\}\s*([A-Za-z_][A-Za-z0-9_]*)\s*;')

FUNC_DEF_RE = re.compile(
    r'^\s*(?:inline\s+|static\s+|virtual\s+|extern\s+|const\s+)*([A-Za-z_][A-Za-z0-9_*\s]*?)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*(?:const\s*)?(?:\{|;)'
)

CALL_RE = re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(')

C_KEYWORDS = {
    "if", "while", "for", "switch", "return", "sizeof", "else", "case",
    "goto", "break", "continue", "do", "typedef", "struct", "enum", "union",
    "class", "const", "static", "extern", "void", "int", "char", "float",
    "double", "long", "short", "unsigned", "signed", "auto", "register",
    "volatile", "inline", "bool", "true", "false", "nullptr", "NULL"
}


def extract_c_symbols(
    file_path: Path,
    relative_path: str,
    *,
    source: str | None = None,
) -> tuple[list[PythonSymbol], Counter[str]]:
    """Extract C/C++ symbols (functions, structs, enums, classes, includes, macros) and calls."""
    if source is None:
        try:
            source = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return [], Counter()

    lines = source.splitlines()
    symbols: list[PythonSymbol] = []
    references: Counter[str] = Counter()

    in_func = False
    func_start_line = 0
    func_name = ""
    func_qualname = ""
    func_sig = ""
    func_calls: list[str] = []
    func_brace_depth = 0
    current_class: str | None = None

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()

        # Track references for BM25/usage search
        words = re.findall(r'[A-Za-z_][A-Za-z0-9_]*', stripped)
        for w in words:
            if w.lower() not in C_KEYWORDS:
                references[w.lower()] += 1

        # 1. Includes
        inc_m = INCLUDE_RE.match(stripped)
        if inc_m:
            inc_name = inc_m.group(1)
            symbols.append(
                PythonSymbol(
                    name=inc_name,
                    qualname=inc_name,
                    kind="include",
                    path=relative_path,
                    line=idx,
                    end_line=idx,
                    language="c",
                )
            )
            continue

        # 2. Macro defines
        def_m = DEFINE_RE.match(stripped)
        if def_m:
            macro_name = def_m.group(1)
            if macro_name not in C_KEYWORDS:
                symbols.append(
                    PythonSymbol(
                        name=macro_name,
                        qualname=macro_name,
                        kind="macro",
                        path=relative_path,
                        line=idx,
                        end_line=idx,
                        language="c",
                    )
                )
            continue

        # 3. Structs, Classes, Enums
        struct_m = STRUCT_CLASS_RE.match(stripped)
        if struct_m:
            st_kind = struct_m.group(1)
            st_name = struct_m.group(2)
            base_class = struct_m.group(3)
            inherits = [base_class] if base_class else []

            if st_kind == "class":
                current_class = st_name

            symbols.append(
                PythonSymbol(
                    name=st_name,
                    qualname=f"{current_class}.{st_name}" if current_class and st_kind != "class" else st_name,
                    kind=st_kind,
                    path=relative_path,
                    line=idx,
                    end_line=idx,
                    inherits=inherits,
                    language="c",
                )
            )
            continue

        # 4. Functions / Methods
        if not in_func:
            func_m = FUNC_DEF_RE.match(stripped)
            if func_m:
                ret_type = func_m.group(1).strip()
                f_name = func_m.group(2).strip()
                args_str = func_m.group(3).strip()

                if f_name not in C_KEYWORDS and not f_name.startswith("typedef"):
                    in_func = "{" in stripped
                    func_start_line = idx
                    func_name = f_name
                    qualname = f"{current_class}.{f_name}" if current_class else f_name
                    func_qualname = qualname
                    func_sig = f"({args_str}) -> {ret_type}" if ret_type else f"({args_str})"
                    func_calls = []
                    func_brace_depth = stripped.count("{") - stripped.count("}")

                    # If prototype (ends in ; and no {), record immediately
                    if ";" in stripped and "{" not in stripped:
                        symbols.append(
                            PythonSymbol(
                                name=f_name,
                                qualname=qualname,
                                kind="method" if current_class else "function",
                                path=relative_path,
                                line=idx,
                                end_line=idx,
                                parent=current_class,
                                signature=func_sig,
                                language="c",
                            )
                        )
                        in_func = False
        else:
            # We are inside function body -> track calls and brace depth
            func_brace_depth += stripped.count("{") - stripped.count("}")
            for c_match in CALL_RE.finditer(stripped):
                target_call = c_match.group(1)
                if target_call not in C_KEYWORDS and target_call != func_name:
                    if target_call not in func_calls:
                        func_calls.append(target_call)

            if func_brace_depth <= 0 or idx == len(lines):
                # Function body finished
                is_test = relative_path.startswith("tests/") or func_name.startswith("test_") or func_name.startswith("Test")
                symbols.append(
                    PythonSymbol(
                        name=func_name,
                        qualname=func_qualname,
                        kind="method" if current_class else "function",
                        path=relative_path,
                        line=func_start_line,
                        end_line=idx,
                        parent=current_class,
                        is_test=is_test,
                        calls=func_calls,
                        signature=func_sig,
                        language="c",
                    )
                )
                in_func = False

    return symbols, references
