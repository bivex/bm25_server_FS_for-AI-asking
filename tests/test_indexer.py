import tempfile
import unittest
from pathlib import Path

from ramdisk_fs_server.indexer import IndexStore


class IndexStoreTests(unittest.TestCase):
    def test_rebuild_populates_stats_and_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "guide.txt").write_text("hello")
            (root / "image.png").write_bytes(b"png")

            store = IndexStore()
            stats = store.rebuild(root)

            self.assertTrue(stats["indexed"])
            self.assertEqual(stats["total_entries"], 4)
            self.assertIn("docs/guide.txt", store.by_path)
            self.assertIn("guide", store.token_index)
            self.assertIn(".txt", store.suffix_index)

    def test_search_filters_by_query_suffix_and_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "guide.txt").write_text("hello")
            (root / "docs" / "notes.md").write_text("hello")
            (root / "other.txt").write_text("hello")

            store = IndexStore()
            store.rebuild(root)

            matches = store.search("guide", suffix="txt", path_prefix="docs")
            self.assertEqual([item.path for item in matches], ["docs/guide.txt"])

    def test_bm25_search_with_scores_prefers_stronger_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("ramdisk server readme")
            (root / "server_notes.md").write_text("server")

            store = IndexStore()
            store.rebuild(root)

            ranked = store.search_with_scores("readme server")
            self.assertEqual(ranked[0][0].path, "README.md")
            self.assertGreater(ranked[0][1], ranked[1][1])
            self.assertTrue(store.stats()["bm25_ready"])

    def test_search_can_match_text_file_contents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.txt").write_text("alpha beta gamma")
            (root / "other.txt").write_text("delta epsilon")

            store = IndexStore()
            store.rebuild(root)

            matches = store.search(content_query="beta")
            self.assertEqual([item.path for item in matches], ["notes.txt"])
            self.assertGreaterEqual(store.stats()["text_files_indexed"], 2)
            self.assertTrue(store.stats()["bm25_loaded_in_memory"])

    def test_get_by_path_and_children(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "guide.txt").write_text("hello")

            store = IndexStore()
            store.rebuild(root)

            entry = store.get_by_path("docs/guide.txt")
            self.assertEqual(entry.name, "guide.txt")
            children = store.get_children("docs")
            self.assertEqual([item.path for item in children], ["docs/guide.txt"])

    def test_rebuild_ignores_common_cache_and_vendor_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("hello")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "module.pyc").write_bytes(b"123")
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("gitdir")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "lib.js").write_text("hello")
            (root / ".venv").mkdir()
            (root / ".venv" / "pyvenv.cfg").write_text("hello")

            store = IndexStore()
            stats = store.rebuild(root)

            self.assertEqual(stats["ignored_names"], [".git", ".venv", "__pycache__", "node_modules"])
            self.assertIn("README.md", store.by_path)
            self.assertNotIn("__pycache__", store.by_path)
            self.assertNotIn(".git", store.by_path)
            self.assertNotIn("node_modules", store.by_path)
            self.assertNotIn(".venv", store.by_path)

    def test_excerpt_highlights_best_matching_line_with_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.txt").write_text("header\nalpha beta gamma\nfooter")

            store = IndexStore()
            store.rebuild(root)

            excerpt = store.get_excerpt("notes.txt", "beta")
            self.assertIsNotNone(excerpt)
            assert excerpt is not None
            self.assertIn("line 2", excerpt)
            self.assertIn("[[beta]]", excerpt)

    def test_python_symbol_index_supports_definitions_usages_and_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pkg").mkdir()
            (root / "pkg" / "service.py").write_text(
                "class IndexStore:\n"
                "    pass\n\n"
                "def rebuild_index():\n"
                "    store = IndexStore()\n"
                "    return store\n"
            )
            (root / "tests").mkdir()
            (root / "tests" / "test_service.py").write_text(
                "from pkg.service import rebuild_index\n\n"
                "def test_rebuild_index():\n"
                "    rebuild_index()\n"
            )

            store = IndexStore()
            stats = store.rebuild(root)

            self.assertGreaterEqual(stats["python_symbol_count"], 3)
            self.assertGreaterEqual(stats["python_test_symbol_count"], 1)
            symbols = store.search_symbols("rebuild_index", kind="function")
            self.assertEqual(symbols[0].path, "pkg/service.py")
            usages = store.find_symbol_usages("IndexStore")
            self.assertEqual(usages[0]["path"], "pkg/service.py")
            related_tests = store.find_related_tests("rebuild_index")
            self.assertEqual(related_tests[0].path, "tests/test_service.py")
            self.assertIn("test_rebuild_index", related_tests[0].qualname)


if __name__ == "__main__":
    unittest.main()
