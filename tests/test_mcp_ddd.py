import json
import tempfile
from pathlib import Path

from ramdisk_fs_mcp.application import (
    AskCodebaseUseCase,
    GetFileSkeletonUseCase,
    GetIndexStatsUseCase,
    GetSymbolContourUseCase,
    SearchCodebaseUseCase,
)
from ramdisk_fs_mcp.infrastructure.index_store_adapter import IndexStoreAdapter
from ramdisk_fs_mcp.infrastructure.mcp_server import create_mcp_server


def test_ddd_hexagonal_use_cases():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "auth.py").write_text(
            "class AuthService:\n"
            "    def login(self, username: str) -> bool:\n"
            "        return True\n"
        )

        adapter = IndexStoreAdapter()
        adapter.rebuild(root)

        # 1. GetFileSkeletonUseCase
        file_uc = GetFileSkeletonUseCase(adapter)
        skel = file_uc.execute("auth.py")
        assert skel.path == "auth.py"
        assert "class AuthService:" in skel.dsl_text

        # 2. GetSymbolContourUseCase
        contour_uc = GetSymbolContourUseCase(adapter)
        contour = contour_uc.execute("AuthService")
        assert "# CONTOUR: AuthService" in contour.dsl_text

        # 3. SearchCodebaseUseCase
        search_uc = SearchCodebaseUseCase(adapter)
        results = search_uc.execute("auth")
        assert len(results) > 0
        assert results[0].path == "auth.py"

        # 4. AskCodebaseUseCase
        ask_uc = AskCodebaseUseCase(adapter)
        answer = ask_uc.execute("где класс AuthService")
        assert "auth.py" in answer.files

        # 5. GetIndexStatsUseCase
        stats_uc = GetIndexStatsUseCase(adapter)
        stats = stats_uc.execute()
        assert stats["indexed"] is True


def test_mcp_server_creation():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "main.py").write_text("def main(): pass")

        mcp_server = create_mcp_server(root_path=root)
        assert mcp_server is not None
        assert mcp_server.name == "codebase-skeleton-mcp"
