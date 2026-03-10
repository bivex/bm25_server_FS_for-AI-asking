from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
        return asdict(self)


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
        return asdict(self)


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
        return asdict(self)


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
        return asdict(self)
