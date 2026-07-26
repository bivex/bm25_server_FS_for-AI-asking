from __future__ import annotations

import mimetypes
import os
import stat
import time
from pathlib import Path

from .models import FsEntryModel, FsSnapshot, FsSummary, RamDiskInfo, TreeNode


_MIME_CACHE: dict[str, str | None] = {}


def _fast_guess_mime(name: str, suffix: str | None) -> str | None:
    if suffix is None:
        return None
    suf_lower = suffix.lower()
    if suf_lower in _MIME_CACHE:
        return _MIME_CACHE[suf_lower]
    mime = mimetypes.guess_type(name)[0]
    _MIME_CACHE[suf_lower] = mime
    return mime


def _entry_type(mode: int, is_symlink: bool) -> str:
    if is_symlink:
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "file"
    return "other"


def _build_entry_from_stat(
    name: str,
    rel_path: str,
    st: os.stat_result,
    is_symlink: bool,
    full_path_str: str,
) -> FsEntryModel:
    target = None
    if is_symlink:
        try:
            target = os.readlink(full_path_str)
        except OSError:
            target = None

    dot_index = name.rfind(".")
    suffix = name[dot_index:] if dot_index > 0 else None

    return FsEntryModel(
        path=rel_path,
        name=name,
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
        suffix=suffix,
        mime_type=_fast_guess_mime(name, suffix),
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
    root_str = str(root_path)
    ignored = set(ignore_names or set())

    models: list[FsEntryModel] = []
    totals = {
        "entries": 0,
        "files": 0,
        "dirs": 0,
        "symlinks": 0,
        "size": 0,
    }

    def walk_dir(dir_path_str: str, rel_prefix: str) -> TreeNode:
        if rel_prefix == ".":
            st = os.lstat(dir_path_str)
            is_symlink = stat.S_ISLNK(st.st_mode)
            entry = _build_entry_from_stat(
                name=root_path.name,
                rel_path=".",
                st=st,
                is_symlink=is_symlink,
                full_path_str=dir_path_str,
            )
        else:
            raise ValueError("Root call expects rel_prefix='.'")

        models.append(entry)
        totals["entries"] += 1
        totals["dirs"] += 1
        node = TreeNode(entry=entry)

        children: list[TreeNode] = []
        try:
            with os.scandir(dir_path_str) as iterator:
                entries = sorted(iterator, key=lambda item: item.name.lower())
        except OSError:
            entries = []

        for child in entries:
            if child.name in ignored:
                continue
            child_rel = child.name if rel_prefix == "." else f"{rel_prefix}/{child.name}"
            try:
                st = child.stat(follow_symlinks=False)
                is_symlink = child.is_symlink()
            except OSError:
                continue

            child_entry = _build_entry_from_stat(
                name=child.name,
                rel_path=child_rel,
                st=st,
                is_symlink=is_symlink,
                full_path_str=child.path,
            )
            models.append(child_entry)
            totals["entries"] += 1
            if child_entry.entry_type == "file":
                totals["files"] += 1
                totals["size"] += child_entry.size_bytes
            elif child_entry.entry_type == "directory":
                totals["dirs"] += 1
            elif child_entry.entry_type == "symlink":
                totals["symlinks"] += 1

            child_node = TreeNode(entry=child_entry)
            if child_entry.entry_type == "directory":
                child_children = _walk_subdir(child.path, child_rel)
                child_node.children = child_children
                child_node.entry.children_count = len(child_children)
            children.append(child_node)

        node.children = children
        node.entry.children_count = len(children)
        return node

    def _walk_subdir(dir_path_str: str, rel_prefix: str) -> list[TreeNode]:
        children: list[TreeNode] = []
        try:
            with os.scandir(dir_path_str) as iterator:
                entries = sorted(iterator, key=lambda item: item.name.lower())
        except OSError:
            return []

        for child in entries:
            if child.name in ignored:
                continue
            child_rel = f"{rel_prefix}/{child.name}"
            try:
                st = child.stat(follow_symlinks=False)
                is_symlink = child.is_symlink()
            except OSError:
                continue

            child_entry = _build_entry_from_stat(
                name=child.name,
                rel_path=child_rel,
                st=st,
                is_symlink=is_symlink,
                full_path_str=child.path,
            )
            models.append(child_entry)
            totals["entries"] += 1
            if child_entry.entry_type == "file":
                totals["files"] += 1
                totals["size"] += child_entry.size_bytes
            elif child_entry.entry_type == "directory":
                totals["dirs"] += 1
            elif child_entry.entry_type == "symlink":
                totals["symlinks"] += 1

            child_node = TreeNode(entry=child_entry)
            if child_entry.entry_type == "directory":
                sub_children = _walk_subdir(child.path, child_rel)
                child_node.children = sub_children
                child_node.entry.children_count = len(sub_children)
            children.append(child_node)
        return children

    tree = walk_dir(root_str, ".")
    summary = FsSummary(
        root=root_str,
        generated_at=time.time(),
        total_entries=totals["entries"],
        total_files=totals["files"],
        total_directories=totals["dirs"],
        total_symlinks=totals["symlinks"],
        total_size_bytes=totals["size"],
    )
    return FsSnapshot(summary=summary, ramdisk=ramdisk, models=models, tree=tree)

