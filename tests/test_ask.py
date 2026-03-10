import tempfile
import unittest
from pathlib import Path

from ramdisk_fs_server.ask import answer_question, parse_question
from ramdisk_fs_server.indexer import IndexStore


class AskTests(unittest.TestCase):
    def test_parse_question_extracts_path_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests" / "api").mkdir()
            store = IndexStore()
            store.rebuild(root)

            parsed = parse_question("покажи только директории в tests", index_store=store)

            self.assertEqual(parsed.entry_type, "directory")
            self.assertEqual(parsed.path_prefix, "tests")
            self.assertEqual(parsed.query, "")

    def test_answer_question_lists_directories_under_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests" / "api").mkdir()
            (root / "tests" / "fixtures").mkdir()
            (root / "docs").mkdir()
            store = IndexStore()
            store.rebuild(root)

            answer = answer_question("покажи только директории в tests", store)

            self.assertEqual(answer["files"], ["tests", "tests/api", "tests/fixtures"])
            self.assertIn("директорий внутри tests", answer["answer"])

    def test_answer_question_finds_symbol_definition_usage_and_tests(self) -> None:
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
            store.rebuild(root)

            symbol_answer = answer_question("где функция rebuild_index", store)
            self.assertEqual(symbol_answer["files"], ["pkg/service.py"])
            self.assertIn("определён", symbol_answer["answer"])

            usage_answer = answer_question("кто использует IndexStore", store)
            self.assertEqual(usage_answer["files"], ["pkg/service.py"])
            self.assertIn("используется", usage_answer["answer"])

            tests_answer = answer_question("где тесты для rebuild_index", store)
            self.assertEqual(tests_answer["files"], ["tests/test_service.py"])
            self.assertIn("тест", tests_answer["answer"].lower())

    def test_answer_question_falls_back_from_function_to_method(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pkg").mkdir()
            (root / "pkg" / "service.py").write_text(
                "class Manager:\n"
                "    def rebuild_index(self):\n"
                "        return 1\n"
            )
            store = IndexStore()
            store.rebuild(root)

            answer = answer_question("где функция rebuild_index", store)

            self.assertEqual(answer["files"], ["pkg/service.py"])
            self.assertEqual(answer["matches"][0]["kind"], "method")


if __name__ == "__main__":
    unittest.main()