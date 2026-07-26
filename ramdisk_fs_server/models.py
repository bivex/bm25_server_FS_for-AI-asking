from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RamDiskInfo:
    device: str
    mount_point: str
    label: str
    size_mb: int
    fs_type: str
    sectors: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "mount_point": self.mount_point,
            "label": self.label,
            "size_mb": self.size_mb,
            "fs_type": self.fs_type,
            "sectors": self.sectors,
        }


@dataclass(slots=True)
class FsEntryModel:
    path: str
    name: str
    entry_type: str
    size_bytes: int
    mode: int
    permissions: str
    uid: int
    gid: int
    inode: int
    device: int
    hard_links: int
    created_at: float
    modified_at: float
    accessed_at: float
    suffix: str | None
    mime_type: str | None
    is_symlink: bool
    symlink_target: str | None
    children_count: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "entry_type": self.entry_type,
            "size_bytes": self.size_bytes,
            "mode": self.mode,
            "permissions": self.permissions,
            "uid": self.uid,
            "gid": self.gid,
            "inode": self.inode,
            "device": self.device,
            "hard_links": self.hard_links,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "accessed_at": self.accessed_at,
            "suffix": self.suffix,
            "mime_type": self.mime_type,
            "is_symlink": self.is_symlink,
            "symlink_target": self.symlink_target,
            "children_count": self.children_count,
            "error": self.error,
        }


@dataclass(slots=True)
class TreeNode:
    entry: FsEntryModel
    children: list["TreeNode"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry": self.entry.to_dict(),
            "children": [child.to_dict() for child in self.children],
        }


@dataclass(slots=True)
class FsSummary:
    root: str
    generated_at: float
    total_entries: int
    total_files: int
    total_directories: int
    total_symlinks: int
    total_size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "generated_at": self.generated_at,
            "total_entries": self.total_entries,
            "total_files": self.total_files,
            "total_directories": self.total_directories,
            "total_symlinks": self.total_symlinks,
            "total_size_bytes": self.total_size_bytes,
        }


@dataclass(slots=True)
class FsSnapshot:
    summary: FsSummary
    ramdisk: RamDiskInfo | None
    models: list[FsEntryModel]
    tree: TreeNode | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary.to_dict(),
            "ramdisk": None if self.ramdisk is None else self.ramdisk.to_dict(),
            "models": [item.to_dict() for item in self.models],
            "tree": None if self.tree is None else self.tree.to_dict(),
        }


@dataclass(slots=True)
class PythonSymbol:
    name: str
    qualname: str
    kind: str
    path: str
    line: int
    end_line: int
    parent: str | None = None
    is_test: bool = False
    docstring: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "qualname": self.qualname,
            "kind": self.kind,
            "path": self.path,
            "line": self.line,
            "end_line": self.end_line,
            "parent": self.parent,
            "is_test": self.is_test,
            "docstring": self.docstring,
        }
