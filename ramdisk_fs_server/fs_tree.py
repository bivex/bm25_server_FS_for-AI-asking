from __future__ import annotations

import mimetypes
import os
import stat
import time
from pathlib import Path

from .models import FsEntryModel, FsSnapshot, FsSummary, RamDiskInfo, TreeNode


def _entry_type(mode: int, is_symlink: bool) -> str:
    if is_symlink:
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "file"
    return "other"


def _build_entry(path: Path, root: Path) -> FsEntryModel:
    st = path.lstat()
    is_symlink = path.is_symlink()
    rel_path = "." if path == root else str(path.relative_to(root))
    target = None
    if is_symlink:
        try:
            target = os.readlink(path)
        except OSError:
            target = None

    return FsEntryModel(
        path=rel_path,
        name=path.name if path != root else root.name,
        entry_type=_entry_type(st.st_mode, is_symlink),
        size_bytes=st.st_size,
        mode=st.st_mode,
        permissions=stat.filemode(st.st_mode),
        uid=st.st_uid,
        gid=st.st_gid,
        inode=st.st_ino,
        device=st.st_dev,
        hard_links=st.st_nlink,
        created_at=st.st_ctime,
        modified_at=st.st_mtime,
        accessed_at=st.st_atime,
        suffix=path.suffix or None,
        mime_type=mimetypes.guess_type(path.name)[0],
        is_symlink=is_symlink,
        symlink_target=target,
    )


def build_snapshot(
    root: str | Path,
    ramdisk: RamDiskInfo | None = None,
    ignore_names: set[str] | frozenset[str] | None = None,
) -> FsSnapshot:
    root_path = Path(root).expanduser().resolve()
    if not root_path.exists():
        raise FileNotFoundError(root_path)
    ignored = set(ignore_names or set())

    models: list[FsEntryModel] = []
    totals = {
        "entries": 0,
        "files": 0,
        "dirs": 0,
        "symlinks": 0,
        "size": 0,
    }

    def walk(path: Path) -> TreeNode:
        entry = _build_entry(path, root_path)
        models.append(entry)
        totals["entries"] += 1
        if entry.entry_type == "file":
            totals["files"] += 1
            totals["size"] += entry.size_bytes
        elif entry.entry_type == "directory":
            totals["dirs"] += 1
        elif entry.entry_type == "symlink":
            totals["symlinks"] += 1

        node = TreeNode(entry=entry)
        if entry.entry_type != "directory":
            return node

        children: list[TreeNode] = []
        with os.scandir(path) as iterator:
            for child in sorted(iterator, key=lambda item: item.name.lower()):
                if child.name in ignored:
                    continue
                children.append(walk(Path(child.path)))
        node.children = children
        node.entry.children_count = len(children)
        return node

    tree = walk(root_path)
    summary = FsSummary(
        root=str(root_path),
        generated_at=time.time(),
        total_entries=totals["entries"],
        total_files=totals["files"],
        total_directories=totals["dirs"],
        total_symlinks=totals["symlinks"],
        total_size_bytes=totals["size"],
    )
    return FsSnapshot(summary=summary, ramdisk=ramdisk, models=models, tree=tree)
