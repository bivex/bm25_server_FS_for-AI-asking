import tempfile
import unittest
from pathlib import Path

from ramdisk_fs_server.fs_tree import build_snapshot


class BuildSnapshotTests(unittest.TestCase):
    def test_build_snapshot_returns_models_and_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "readme.txt").write_text("hello")
            (root / "data.bin").write_bytes(b"abc")

            snapshot = build_snapshot(root)

            self.assertEqual(snapshot.summary.total_files, 2)
            self.assertEqual(snapshot.summary.total_directories, 2)
            self.assertEqual(snapshot.summary.total_entries, 4)
            self.assertEqual(snapshot.summary.total_size_bytes, 8)
            self.assertIsNotNone(snapshot.tree)
            self.assertEqual(snapshot.tree.entry.path, ".")
            self.assertEqual(sorted(child.entry.name for child in snapshot.tree.children), ["data.bin", "docs"])

    def test_build_snapshot_marks_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.txt"
            target.write_text("hello")
            link = root / "target-link.txt"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlinks not supported in this environment")

            snapshot = build_snapshot(root)
            link_model = next(item for item in snapshot.models if item.name == "target-link.txt")
            self.assertEqual(link_model.entry_type, "symlink")
            self.assertTrue(link_model.is_symlink)

    def test_build_snapshot_can_ignore_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "visible").mkdir()
            (root / "visible" / "file.txt").write_text("hello")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "hidden.pyc").write_bytes(b"123")

            snapshot = build_snapshot(root, ignore_names={"__pycache__"})

            paths = {item.path for item in snapshot.models}
            self.assertIn("visible/file.txt", paths)
            self.assertNotIn("__pycache__", paths)
            self.assertNotIn("__pycache__/hidden.pyc", paths)


if __name__ == "__main__":
    unittest.main()
