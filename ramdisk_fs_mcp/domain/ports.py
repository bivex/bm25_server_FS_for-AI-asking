from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .models import DomainAskAnswer, DomainSearchResult, DomainSkeleton, DomainSymbol


class IndexPort(ABC):
    """Abstract Port Interface for Codebase Indexing & Retrieval Operations."""

    @abstractmethod
    def rebuild(self, root: Path) -> dict[str, Any]:
        """Rebuild or update the codebase index."""
        ...

    @abstractmethod
    def get_file_skeleton(self, path: str) -> DomainSkeleton:
        """Retrieve Skeleton DSL for a specific file."""
        ...

    @abstractmethod
    def get_symbol_contour(self, query: str, limit: int = 10) -> DomainSkeleton:
        """Retrieve Skeleton DSL contour for matching symbol query."""
        ...

    @abstractmethod
    def search_symbols(self, name: str, kind: str | None = None, limit: int = 20) -> list[DomainSymbol]:
        """Search code symbols by name or qualname."""
        ...

    @abstractmethod
    def search_code(self, query: str, content_query: str = "", limit: int = 20) -> list[DomainSearchResult]:
        """Search codebase files and content using BM25 ranking."""
        ...

    @abstractmethod
    def ask_question(self, question: str) -> DomainAskAnswer:
        """Answer natural language architectural questions over the indexed codebase."""
        ...

    @abstractmethod
    def stats(self) -> dict[str, Any]:
        """Retrieve current index statistics."""
        ...
