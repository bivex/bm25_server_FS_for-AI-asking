from __future__ import annotations

from typing import TYPE_CHECKING

from .models import PythonSymbol

if TYPE_CHECKING:
    from .indexer import IndexStore


def resolve_symbol_ref(ref_name: str, index_store: IndexStore | None) -> str:
    """Resolve a call or inheritance target name to 'Target (path:Lline)' if indexed."""
    if index_store is None:
        return ref_name

    short_name = ref_name.split(".")[-1]
    matches = index_store.search_symbols(short_name, limit=5)
    for m in matches:
        if m.name == short_name or m.qualname == ref_name:
            return f"{ref_name} ({m.path}:L{m.line})"

    return ref_name


_USED_BY_CACHE: dict[int, tuple[int, dict[str, list[str]]]] = {}


def find_used_by(symbol: PythonSymbol, index_store: IndexStore | None) -> list[str]:
    """Find callers across the codebase that reference/call this symbol."""
    if index_store is None:
        return []

    store_id = id(index_store)
    rebuild_cnt = getattr(index_store, "rebuild_counter", 0)

    cached_entry = _USED_BY_CACHE.get(store_id)
    if cached_entry is None or cached_entry[0] != rebuild_cnt:
        used_by_map: dict[str, list[str]] = {}
        seen_map: dict[str, set[tuple[str, int]]] = {}
        for s in index_store.python_symbols:
            for call_target in getattr(s, "calls", []):
                call_short = call_target.split(".")[-1]
                for target_key in (call_target, call_short):
                    if target_key not in seen_map:
                        seen_map[target_key] = set()
                        used_by_map[target_key] = []
                    key = (s.path, s.line)
                    if key not in seen_map[target_key]:
                        seen_map[target_key].add(key)
                        used_by_map[target_key].append(f"{s.qualname} ({s.path}:L{s.line})")
        _USED_BY_CACHE[store_id] = (rebuild_cnt, used_by_map)
        used_by_map_active = used_by_map
    else:
        used_by_map_active = cached_entry[1]

    candidates = used_by_map_active.get(symbol.qualname) or used_by_map_active.get(symbol.name) or []
    # Exclude self-references
    self_ref = f"({symbol.path}:L{symbol.line})"
    return [c for c in candidates if self_ref not in c]


def render_symbol_dsl(
    symbol: PythonSymbol,
    index_store: IndexStore | None = None,
    indent: str = "",
) -> list[str]:
    """Render a single PythonSymbol (class, function, method) into Skeleton DSL lines."""
    lines: list[str] = []
    line_range = f"[L{symbol.line}-L{symbol.end_line}]"

    if symbol.kind in ("class", "struct", "enum", "union"):
        inherits_str = ""
        if symbol.inherits:
            resolved_inherits = [resolve_symbol_ref(b, index_store) for b in symbol.inherits]
            inherits_str = f"  # inherits: {', '.join(resolved_inherits)}"
        bases_str = f"({', '.join(symbol.inherits)})" if symbol.inherits else ""
        lines.append(f"{indent}{symbol.kind} {symbol.name}{bases_str}:{inherits_str}")
        if symbol.docstring:
            doc_first = symbol.docstring.splitlines()[0]
            lines.append(f'{indent}  """{doc_first}"""')

    elif symbol.kind in ("function", "method"):
        sig = symbol.signature if symbol.signature else "()"
        lines.append(f"{indent}# {line_range}")
        lines.append(f"{indent}def {symbol.name}{sig}:")

        if symbol.docstring:
            doc_first = symbol.docstring.splitlines()[0]
            lines.append(f'{indent}  """{doc_first}"""')

        # Add calls xrefs
        if symbol.calls:
            resolved_calls = [resolve_symbol_ref(c, index_store) for c in symbol.calls[:6]]
            lines.append(f"{indent}  # calls: {', '.join(resolved_calls)}")

        # Add used_by xrefs
        used_by = find_used_by(symbol, index_store)
        if used_by:
            lines.append(f"{indent}  # used_by: {', '.join(used_by[:6])}")

    elif symbol.kind == "macro":
        lines.append(f"{indent}#define {symbol.name}")
    elif symbol.kind == "include":
        lines.append(f"{indent}#include <{symbol.name}>")

    return lines


def render_file_skeleton_dsl(path: str, index_store: IndexStore | None = None, contour: str | None = None) -> str:
    """Render a full file's symbols into compact Skeleton DSL format."""
    output: list[str] = [f"# FILE: {path}"]
    if contour:
        output.append(f"# CONTOUR: {contour}")
    output.append("")

    symbols: list[PythonSymbol] = []
    if index_store is not None:
        symbols = index_store.symbols_by_path.get(path, [])

    if not symbols:
        output.append("# (no python symbols indexed in file)")
        return "\n".join(output)

    top_level = [s for s in symbols if not s.parent]
    methods_by_parent: dict[str, list[PythonSymbol]] = {}
    for s in symbols:
        if s.parent:
            methods_by_parent.setdefault(s.parent, []).append(s)

    for sym in top_level:
        if sym.kind == "import":
            continue
        sym_lines = render_symbol_dsl(sym, index_store, indent="")
        output.extend(sym_lines)

        if sym.kind == "class":
            children = methods_by_parent.get(sym.name, [])
            for child in children:
                output.append("")
                output.extend(render_symbol_dsl(child, index_store, indent="  "))
            output.append("")

    return "\n".join(output)


def render_contour_skeleton_dsl(
    symbols: list[PythonSymbol],
    index_store: IndexStore | None = None,
    contour_name: str | None = None,
) -> str:
    """Render an arbitrary list of symbols (e.g. search hits or query contour) into Skeleton DSL."""
    output: list[str] = []
    if contour_name:
        output.append(f"# CONTOUR: {contour_name}")

    by_file: dict[str, list[PythonSymbol]] = {}
    for s in symbols:
        by_file.setdefault(s.path, []).append(s)

    for path, file_symbols in by_file.items():
        output.append(f"# FILE: {path}")
        for s in file_symbols:
            indent = "  " if s.parent else ""
            output.extend(render_symbol_dsl(s, index_store, indent=indent))
        output.append("")

    return "\n".join(output)
