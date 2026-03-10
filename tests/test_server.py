import json
import threading
import tempfile
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from ramdisk_fs_server.server import AppContext, make_handler


class AppContextTests(unittest.TestCase):
    def test_resolve_requested_path_stays_under_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = AppContext(root_path=Path(tmp))
            resolved = context.resolve_requested_path("nested")
            self.assertEqual(resolved, Path(tmp).resolve() / "nested")

    def test_resolve_requested_path_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            context = AppContext(root_path=Path(tmp))
            with self.assertRaises(ValueError):
                context.resolve_requested_path("../escape")

    def test_rebuild_index_populates_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "readme.txt").write_text("hello")

            context = AppContext(root_path=root, index_refresh_seconds=0)
            stats = context.rebuild_index()

            self.assertTrue(stats["indexed"])
            self.assertIn("docs/readme.txt", context.index_store.by_path)

    def test_rebuild_index_swaps_in_new_store_instance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "readme.txt"
            target.write_text("hello")
            context = AppContext(root_path=root, index_refresh_seconds=0)

            context.rebuild_index()
            first_store = context.index_store

            target.write_text("hello again")
            context.rebuild_index()
            second_store = context.index_store

            self.assertIsNot(first_store, second_store)
            self.assertTrue(second_store.stats()["indexed"])


class ServerIndexEndpointTests(unittest.TestCase):
    def test_index_endpoints_return_stats_and_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "readme.txt").write_text("hello")
            (root / "docs" / "guide.txt").write_text("alpha beta gamma")
            (root / "app.py").write_text(
                "class IndexStore:\n"
                "    pass\n\n"
                "def answer_question():\n"
                "    return IndexStore()\n"
            )
            (root / "tests").mkdir()
            (root / "tests" / "api").mkdir()
            (root / "tests" / "test_app.py").write_text(
                "from app import answer_question\n\n"
                "def test_answer_question():\n"
                "    answer_question()\n"
            )
            context = AppContext(root_path=root, index_refresh_seconds=0)
            context.rebuild_index()

            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(context))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(f"{base_url}/index/stats") as response:
                    stats_payload = json.load(response)
                self.assertTrue(stats_payload["index"]["indexed"])
                self.assertTrue(stats_payload["index"]["bm25_ready"])
                self.assertTrue(stats_payload["index"]["bm25_loaded_in_memory"])
                self.assertFalse(stats_payload["index"]["bm25_loaded_in_gpu"])
                self.assertIn("__pycache__", stats_payload["index"]["ignored_names"])

                with urlopen(f"{base_url}/index/search?q=readme") as response:
                    search_payload = json.load(response)
                self.assertEqual(search_payload["ranking"], "bm25")
                self.assertEqual(search_payload["count"], 1)
                self.assertEqual(search_payload["matches"][0]["entry"]["path"], "docs/readme.txt")
                self.assertGreater(search_payload["matches"][0]["score"], 0)

                with urlopen(f"{base_url}/index/file?path=docs/readme.txt") as response:
                    file_payload = json.load(response)
                self.assertEqual(file_payload["entry"]["path"], "docs/readme.txt")
                self.assertTrue(file_payload["content_indexed"])

                with urlopen(f"{base_url}/index/children?path=docs") as response:
                    children_payload = json.load(response)
                self.assertEqual(children_payload["count"], 2)
                self.assertEqual(
                    [item["path"] for item in children_payload["children"]],
                    ["docs/guide.txt", "docs/readme.txt"],
                )

                with urlopen(f"{base_url}/index/search?content=beta") as response:
                    content_payload = json.load(response)
                self.assertEqual(content_payload["count"], 1)
                self.assertEqual(content_payload["matches"][0]["entry"]["path"], "docs/guide.txt")

                with urlopen(f"{base_url}/index/symbols?name=answer_question&kind=function") as response:
                    symbols_payload = json.load(response)
                self.assertGreaterEqual(symbols_payload["count"], 1)
                self.assertEqual(symbols_payload["matches"][0]["name"], "answer_question")
                self.assertEqual(symbols_payload["matches"][0]["path"], "app.py")

                with urlopen(f"{base_url}/index/usages?name=IndexStore") as response:
                    usages_payload = json.load(response)
                self.assertGreaterEqual(usages_payload["count"], 1)
                self.assertEqual(usages_payload["matches"][0]["path"], "app.py")

                ask_request = Request(
                    f"{base_url}/ask",
                    method="POST",
                    data=json.dumps({"question": "where is the readme file"}).encode("utf-8"),
                )
                ask_request.add_header("Content-Type", "application/json")
                with urlopen(ask_request) as response:
                    ask_payload = json.load(response)
                self.assertEqual(ask_payload["files"][0], "docs/readme.txt")
                self.assertEqual(ask_payload["parsed_query"]["entry_type"], "file")
                self.assertEqual(ask_payload["matches"][0]["path"], "docs/readme.txt")
                self.assertIn("[[readme]]", ask_payload["matches"][0]["excerpt"])

                with urlopen(f"{base_url}/ask?q=show%20only%20directories%20inside%20tests") as response:
                    ask_dirs_payload = json.load(response)
                self.assertEqual(ask_dirs_payload["parsed_query"]["path_prefix"], "tests")
                self.assertEqual(ask_dirs_payload["files"], ["tests", "tests/api"])

                with urlopen(f"{base_url}/ask?q=where%20function%20answer_question") as response:
                    ask_symbol_payload = json.load(response)
                self.assertEqual(ask_symbol_payload["files"][0], "app.py")
                self.assertEqual(ask_symbol_payload["parsed_query"]["intent"], "find_symbol")

                with urlopen(f"{base_url}/ask?q=who%20uses%20IndexStore") as response:
                    ask_usage_payload = json.load(response)
                self.assertGreaterEqual(len(ask_usage_payload["files"]), 1)
                self.assertEqual(ask_usage_payload["parsed_query"]["intent"], "find_usage")

                with urlopen(f"{base_url}/ask?q=where%20tests%20for%20answer_question") as response:
                    ask_tests_payload = json.load(response)
                self.assertIn("tests/test_app.py", ask_tests_payload["files"])
                self.assertEqual(ask_tests_payload["parsed_query"]["intent"], "find_tests")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_index_ignores_default_directory_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("hello")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "module.pyc").write_bytes(b"123")
            context = AppContext(root_path=root, index_refresh_seconds=0)
            context.rebuild_index()

            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(context))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(f"{base_url}/index/search?q=module") as response:
                    payload = json.load(response)
                self.assertEqual(payload["count"], 0)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_rebuild_endpoint_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "readme.txt").write_text("hello")
            context = AppContext(root_path=root, index_refresh_seconds=0)
            context.rebuild_index()

            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(context))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            try:
                request = Request(f"{base_url}/index/rebuild", method="POST", data=b"{}")
                request.add_header("Content-Type", "application/json")
                with urlopen(request) as response:
                    rebuild_payload = json.load(response)
                self.assertTrue(rebuild_payload["index"]["indexed"])
                self.assertTrue(rebuild_payload["index"]["bm25_ready"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_read_endpoints_do_not_block_on_context_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "readme.txt").write_text("hello")
            context = AppContext(root_path=root, index_refresh_seconds=0)
            context.rebuild_index()

            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(context))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
            results: dict[str, object] = {}
            done = threading.Event()

            def request_worker() -> None:
                try:
                    with urlopen(f"{base_url}/index/search?q=readme", timeout=1.0) as response:
                        results["search"] = json.load(response)
                    ask_request = Request(
                        f"{base_url}/ask",
                        method="POST",
                        data=json.dumps({"question": "where is the readme file"}).encode("utf-8"),
                    )
                    ask_request.add_header("Content-Type", "application/json")
                    with urlopen(ask_request, timeout=1.0) as response:
                        results["ask"] = json.load(response)
                except Exception as exc:  # pragma: no cover - surfaced by assertion below
                    results["error"] = exc
                finally:
                    done.set()

            try:
                with context.lock:
                    worker = threading.Thread(target=request_worker, daemon=True)
                    worker.start()
                    self.assertTrue(done.wait(timeout=1.5), "read endpoints should not wait on AppContext.lock")
                    worker.join(timeout=1)
                self.assertNotIn("error", results)
                self.assertEqual(results["search"]["matches"][0]["entry"]["path"], "docs/readme.txt")
                self.assertEqual(results["ask"]["files"][0], "docs/readme.txt")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
