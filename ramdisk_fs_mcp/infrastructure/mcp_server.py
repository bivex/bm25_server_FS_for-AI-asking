from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..application.use_cases import (
    AskCodebaseUseCase,
    GetFileSkeletonUseCase,
    GetIndexStatsUseCase,
    GetSymbolContourUseCase,
    SearchCodebaseUseCase,
)
from .index_store_adapter import IndexStoreAdapter


def create_mcp_server(root_path: Path | None = None) -> FastMCP:
    """Create and configure the FastMCP server instance using DDD Use Cases."""
    mcp = FastMCP("codebase-skeleton-mcp")

    adapter = IndexStoreAdapter()
    if root_path is not None:
        adapter.rebuild(root_path)

    file_skeleton_uc = GetFileSkeletonUseCase(adapter)
    symbol_contour_uc = GetSymbolContourUseCase(adapter)
    search_code_uc = SearchCodebaseUseCase(adapter)
    ask_question_uc = AskCodebaseUseCase(adapter)
    index_stats_uc = GetIndexStatsUseCase(adapter)

    @mcp.tool()
    def get_file_skeleton(path: str, root_path: str | None = None) -> str:
        """Get token-efficient Skeleton DSL with cross-references for a specific file. Option: root_path (e.g. /tmp/project)."""
        adapter.ensure_root(root_path)
        skeleton = file_skeleton_uc.execute(path)
        return skeleton.dsl_text

    @mcp.tool()
    def get_symbol_contour(query: str, limit: int = 10, root_path: str | None = None) -> str:
        """Get Skeleton DSL contour for symbols matching a query name or qualname. Option: root_path (e.g. /tmp/project)."""
        adapter.ensure_root(root_path)
        contour = symbol_contour_uc.execute(query, limit=limit)
        return contour.dsl_text

    @mcp.tool()
    def search_codebase(query: str = "", content: str = "", limit: int = 20, root_path: str | None = None) -> str:
        """Search codebase files and text content using BM25 scoring and return excerpts. Option: root_path (e.g. /tmp/project)."""
        adapter.ensure_root(root_path)
        results = search_code_uc.execute(query, content_query=content, limit=limit)
        items = [{"path": r.path, "score": r.score, "excerpt": r.excerpt} for r in results]
        return json.dumps({"count": len(items), "results": items}, ensure_ascii=False, indent=2)

    @mcp.tool()
    def ask_codebase(question: str, root_path: str | None = None) -> str:
        """Answer natural language architectural questions over the codebase and return Skeleton DSL. Option: root_path (e.g. /tmp/project)."""
        adapter.ensure_root(root_path)
        answer = ask_question_uc.execute(question)
        res = {
            "question": answer.question,
            "answer": answer.answer_text,
            "files": list(answer.files),
            "matches": list(answer.matches),
            "skeleton_dsl": answer.skeleton_dsl,
        }
        return json.dumps(res, ensure_ascii=False, indent=2)

    @mcp.tool()
    def get_index_stats(root_path: str | None = None) -> str:
        """Get current index statistics (file count, symbol count, BM25 ready). Option: root_path (e.g. /tmp/project)."""
        adapter.ensure_root(root_path)
        stats = index_stats_uc.execute()
        return json.dumps(stats, ensure_ascii=False, indent=2)

    return mcp
