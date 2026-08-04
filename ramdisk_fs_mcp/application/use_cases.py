from __future__ import annotations

from pathlib import Path
from typing import Any

from ..domain.models import DomainAskAnswer, DomainSearchResult, DomainSkeleton, DomainSymbol
from ..domain.ports import IndexPort


class GetFileSkeletonUseCase:
    def __init__(self, index_port: IndexPort) -> None:
        self.index_port = index_port

    def execute(self, path: str) -> DomainSkeleton:
        return self.index_port.get_file_skeleton(path)


class GetSymbolContourUseCase:
    def __init__(self, index_port: IndexPort) -> None:
        self.index_port = index_port

    def execute(self, query: str, limit: int = 10) -> DomainSkeleton:
        return self.index_port.get_symbol_contour(query, limit=limit)


class SearchCodebaseUseCase:
    def __init__(self, index_port: IndexPort) -> None:
        self.index_port = index_port

    def execute(self, query: str, content_query: str = "", limit: int = 20) -> list[DomainSearchResult]:
        return self.index_port.search_code(query, content_query=content_query, limit=limit)


class AskCodebaseUseCase:
    def __init__(self, index_port: IndexPort) -> None:
        self.index_port = index_port

    def execute(self, question: str) -> DomainAskAnswer:
        return self.index_port.ask_question(question)


class GetIndexStatsUseCase:
    def __init__(self, index_port: IndexPort) -> None:
        self.index_port = index_port

    def execute(self) -> dict[str, Any]:
        return self.index_port.stats()
