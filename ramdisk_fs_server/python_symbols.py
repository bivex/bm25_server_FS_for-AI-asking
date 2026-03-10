from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

from .models import PythonSymbol


def extract_python_symbols(file_path: Path, relative_path: str) -> tuple[list[PythonSymbol], Counter[str]]:
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [], Counter()
    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return [], Counter()

    visitor = _PythonSymbolVisitor(relative_path)
    visitor.visit(tree)
    return visitor.symbols, visitor.references


class _PythonSymbolVisitor(ast.NodeVisitor):
    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.symbols: list[PythonSymbol] = []
        self.references: Counter[str] = Counter()
        self._scope_stack: list[tuple[str, str]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self._record_symbol(node, node.name, "class")
        self._scope_stack.append((node.name, "class"))
        self.generic_visit(node)
        self._scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        kind = "method" if self._scope_stack and self._scope_stack[-1][1] == "class" else "function"
        self._record_symbol(node, node.name, kind)
        self._scope_stack.append((node.name, kind))
        self.generic_visit(node)
        self._scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self.visit_FunctionDef(node)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            imported_name = alias.asname or alias.name.split(".")[-1]
            self.symbols.append(
                PythonSymbol(
                    name=imported_name,
                    qualname=imported_name,
                    kind="import",
                    path=self.relative_path,
                    line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                )
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        for alias in node.names:
            imported_name = alias.asname or alias.name
            qualname = f"{node.module}.{alias.name}" if node.module else alias.name
            self.symbols.append(
                PythonSymbol(
                    name=imported_name,
                    qualname=qualname,
                    kind="import",
                    path=self.relative_path,
                    line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                )
            )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if isinstance(node.ctx, ast.Load):
            self.references[node.id.lower()] += 1
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if isinstance(node.ctx, ast.Load):
            self.references[node.attr.lower()] += 1
        self.generic_visit(node)

    def _record_symbol(self, node: ast.AST, name: str, kind: str) -> None:
        scope_names = [scope_name for scope_name, _ in self._scope_stack]
        qualname = ".".join([*scope_names, name]) if scope_names else name
        is_test = self.relative_path.startswith("tests/") or name.startswith("test_") or name.startswith("Test")
        docstring = ast.get_docstring(node, clean=True) if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) else None
        self.symbols.append(
            PythonSymbol(
                name=name,
                qualname=qualname,
                kind=kind,
                path=self.relative_path,
                line=getattr(node, "lineno", 1),
                end_line=getattr(node, "end_lineno", getattr(node, "lineno", 1)),
                parent=".".join(scope_names) if scope_names else None,
                is_test=is_test,
                docstring=docstring,
            )
        )