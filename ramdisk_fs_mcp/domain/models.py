from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class DomainSymbol:
    name: str
    qualname: str
    kind: str
    path: str
    line: int
    end_line: int
    parent: str | None = None
    inherits: tuple[str, ...] = ()
    calls: tuple[str, ...] = ()
    signature: str | None = None
    language: str = "python"


@dataclass(frozen=True, slots=True)
class DomainSkeleton:
    path: str
    contour: str | None
    dsl_text: str
    line_count: int


@dataclass(frozen=True, slots=True)
class DomainSearchResult:
    path: str
    score: float
    excerpt: str
    symbol: DomainSymbol | None = None


@dataclass(frozen=True, slots=True)
class DomainAskAnswer:
    question: str
    answer_text: str
    files: tuple[str, ...]
    matches: tuple[dict[str, Any], ...]
    skeleton_dsl: str | None = None
