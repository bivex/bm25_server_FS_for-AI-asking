from __future__ import annotations

from pathlib import Path
from typing import Any

from ramdisk_fs_server.ask import answer_question
from ramdisk_fs_server.indexer import IndexStore
from ramdisk_fs_server.models import PythonSymbol

from ..domain.models import DomainAskAnswer, DomainSearchResult, DomainSkeleton, DomainSymbol
from ..domain.ports import IndexPort


class IndexStoreAdapter(IndexPort):
    """Adapter bridging IndexStore to Domain IndexPort interface."""

    def __init__(self, index_store: IndexStore | None = None) -> None:
        self._store = index_store or IndexStore()

    @property
    def store(self) -> IndexStore:
        return self._store

    def ensure_root(self, root_path: str | Path | None) -> None:
        """Dynamically switch and index any target codebase directory (e.g. /tmp/project)."""
        if root_path is not None:
            p = Path(root_path).expanduser().resolve()
            if self._store.cache_root != str(p):
                self.rebuild(p)

    def rebuild(self, root: Path) -> dict[str, Any]:
        cache_file = root / ".ramdisk_cache.pkl"
        if cache_file.exists() and self._store.snapshot is None:
            self._store.load_disk_cache(cache_file)
            stats = self._store.rebuild_incremental(root)
        else:
            stats = self._store.rebuild(root)
        self._store.save_disk_cache(cache_file)
        return stats

    def get_file_skeleton(self, path: str) -> DomainSkeleton:
        dsl = self._store.get_skeleton_dsl(path)
        return DomainSkeleton(
            path=path,
            contour=None,
            dsl_text=dsl,
            line_count=len(dsl.splitlines()),
        )

    def get_symbol_contour(self, query: str, limit: int = 10) -> DomainSkeleton:
        dsl = self._store.get_contour_skeleton_dsl(query, limit=limit)
        return DomainSkeleton(
            path="contour",
            contour=query,
            dsl_text=dsl,
            line_count=len(dsl.splitlines()),
        )

    def search_symbols(self, name: str, kind: str | None = None, limit: int = 20) -> list[DomainSymbol]:
        symbols = self._store.search_symbols(name, kind=kind, limit=limit)
        return [self._map_symbol(s) for s in symbols]

    def search_code(self, query: str, content_query: str = "", limit: int = 20) -> list[DomainSearchResult]:
        matches = self._store.search_with_scores(query, content_query=content_query, limit=limit)
        query_terms = f"{query} {content_query}".strip()
        results: list[DomainSearchResult] = []
        for model, score in matches:
            excerpt = self._store.get_excerpt(model.path, query_terms)
            results.append(
                DomainSearchResult(
                    path=model.path,
                    score=round(score, 6),
                    excerpt=excerpt,
                )
            )
        return results

    def ask_question(self, question: str) -> DomainAskAnswer:
        res = answer_question(question, self._store)
        return DomainAskAnswer(
            question=str(res.get("question", question)),
            answer_text=str(res.get("answer", "")),
            files=tuple(res.get("files", [])),
            matches=tuple(res.get("matches", [])),
            skeleton_dsl=res.get("skeleton_dsl"),
        )

    def stats(self) -> dict[str, Any]:
        return self._store.stats()

    def _map_symbol(self, s: PythonSymbol) -> DomainSymbol:
        return DomainSymbol(
            name=s.name,
            qualname=s.qualname,
            kind=s.kind,
            path=s.path,
            line=s.line,
            end_line=s.end_line,
            parent=s.parent,
            inherits=tuple(getattr(s, "inherits", [])),
            calls=tuple(getattr(s, "calls", [])),
            signature=getattr(s, "signature", None),
            language=getattr(s, "language", "python"),
        )
