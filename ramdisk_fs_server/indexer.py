from __future__ import annotations

import heapq
import math
import pickle
import re
import time
from collections.abc import Callable
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .fs_tree import build_snapshot
from .models import FsEntryModel, FsSnapshot, PythonSymbol, RamDiskInfo
from .python_symbols import extract_python_symbols
from .scip_integration import SCIPGraph, build_scip_index, is_scip_available, load_scip_or_fallback
from .skeleton_dsl import render_contour_skeleton_dsl, render_file_skeleton_dsl
from .symbol_extractor import C_EXTENSIONS, OTHER_EXTENSIONS, PYTHON_EXTENSIONS, extract_code_symbols

# ── Legacy regex kept for backward compat (used internally below) ────────────
_TOKEN_RE_LEGACY = re.compile(r"[a-z0-9]+")
# ── CamelCase / PascalCase splitter ─────────────────────────────────────────
# Matches: sequences of uppercase+lowercase, all-caps acronyms, lowercase runs
_CAMEL_RE = re.compile(r"[A-Z][a-z0-9]+|[A-Z]{2,}(?=[A-Z][a-z]|[0-9]|$)|[a-z][a-z0-9]*|[0-9]+")
# ── Identifier boundary splitter (handles snake_case, kebab-case, dots) ──────
_IDENT_SPLIT_RE = re.compile(r"[^a-zA-Z0-9]+")

TEXT_SUFFIXES = {
    ".c",
    ".cfg",
    ".conf",
    ".cpp",
    ".css",
    ".csv",
    ".go",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".log",
    ".md",
    ".py",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
DEFAULT_IGNORED_NAMES = frozenset({"__pycache__", ".git", "node_modules", ".venv", ".venv_dd", "output", "vendor", "demos", ".pytest_cache", ".ruff_cache", "scratch", "autoresearch", "tymoczko_code"})
MAX_TEXT_FILE_SIZE = 1024 * 1024
BM25_K1 = 1.5
BM25_B = 0.75
HIGHLIGHT_TEMPLATE = "[[{term}]]"

# ── Field-weight constants for BM25 document construction ────────────────────
# Higher weight = term repeated N times → higher TF → higher BM25 score
_W_SYMBOL   = 8   # AST symbol name (function/class/method) — most important
_W_FILENAME = 4   # filename (without extension)
_W_PATH     = 2   # parent directory path segments
_W_CONTENT  = 1   # file body content (baseline)

# ── Code-aware stop words (applied to INDEX documents only, NOT to queries) ──
# These tokens appear in virtually every source file and add noise to IDF.
CODE_STOP_WORDS: frozenset[str] = frozenset({
    # Python keywords
    "def", "class", "return", "import", "from", "self", "none",
    "true", "false", "if", "else", "elif", "for", "while",
    "try", "except", "with", "as", "in", "pass", "raise",
    "yield", "lambda", "global", "nonlocal", "del", "assert",
    # JavaScript / TypeScript
    "const", "let", "var", "function", "export", "default",
    "async", "await", "typeof", "instanceof", "prototype",
    # Rust
    "fn", "mut", "pub", "use", "mod", "impl", "match", "where",
    "trait", "derive", "unsafe", "move", "ref", "dyn",
    # Go
    "func", "package", "type", "interface", "chan", "defer", "go",
    # C / C++
    "void", "char", "static", "extern", "inline", "typedef",
    "unsigned", "signed", "long", "short", "struct", "enum",
    # Java / Kotlin
    "public", "private", "protected", "final", "abstract", "override",
    "extends", "implements", "super", "throws",
    # Generic noise (ultra-common identifiers with near-zero discrimination)
    "null", "nil", "undefined", "this", "that",
    # Single chars are already filtered by len ≥ 2 below
})


def _split_identifier(token: str) -> list[str]:
    """Split a camelCase/PascalCase/snake_case/SCREAMING_SNAKE token into parts.

    Returns the original lowercased token PLUS all its sub-parts, so that
    both exact and partial queries match.

    Examples:
        getUserAccount  → [getuseraccount, get, user, account]
        RateLimiter     → [ratelimiter, rate, limiter]
        HTTP_STATUS_CODE→ [http_status_code, http, status, code]
        parse_JSON_resp → [parse_json_resp, parse, json, resp]
    """
    lower = token.lower()
    result = [lower]  # always include the original lowercased form
    # Step 1: split on non-alphanumeric boundaries (snake_case, kebab, dots)
    boundary_parts = _IDENT_SPLIT_RE.split(token)
    for part in boundary_parts:
        if not part:
            continue
        # Step 2: CamelCase / PascalCase split within each boundary segment
        camel_parts = _CAMEL_RE.findall(part)
        for p in camel_parts:
            pl = p.lower()
            if pl and pl != lower and len(pl) >= 2:
                result.append(pl)
    return result



def _tokenize(value: str) -> set[str]:
    """Tokenize a string into a set of lowercase tokens with CamelCase splitting.

    Used for boolean candidate filtering (token_index, content_index).
    """
    tokens: set[str] = set()
    for raw_token in _IDENT_SPLIT_RE.split(value):
        if not raw_token:
            continue
        tokens.update(_split_identifier(raw_token))
    tokens.discard("")
    return tokens


def _tokenize_terms(value: str) -> list[str]:
    """Tokenize into an ordered list with CamelCase expansion.

    Used for BM25 query term lists and excerpt highlighting.
    NOTE: Does NOT apply stop-word filtering — callers that build the
    document index should call _tokenize_terms_for_index() instead.
    """
    result: list[str] = []
    for raw_token in _IDENT_SPLIT_RE.split(value):
        if not raw_token:
            continue
        result.extend(_split_identifier(raw_token))
    return result


def _tokenize_terms_for_index(value: str) -> list[str]:
    """Like _tokenize_terms but with CODE_STOP_WORDS filtered out.

    Apply ONLY when building BM25 document vectors, never to query terms.
    Filtering stop words from documents improves IDF discrimination without
    breaking queries that explicitly search for 'self', 'def', etc.
    """
    return [
        t for t in _tokenize_terms(value)
        if t not in CODE_STOP_WORDS and len(t) >= 2
    ]


@dataclass(slots=True)
class IndexStore:
    snapshot: FsSnapshot | None = None
    last_built_at: float | None = None
    cache_root: str | None = None
    ignored_names: frozenset[str] = field(default_factory=lambda: DEFAULT_IGNORED_NAMES)
    by_path: dict[str, FsEntryModel] = field(default_factory=dict)
    token_index: dict[str, set[str]] = field(default_factory=dict)
    content_index: dict[str, set[str]] = field(default_factory=dict)
    suffix_index: dict[str, set[str]] = field(default_factory=dict)
    type_index: dict[str, set[str]] = field(default_factory=dict)
    mime_index: dict[str, set[str]] = field(default_factory=dict)
    children_index: dict[str, list[str]] = field(default_factory=dict)
    content_indexed_paths: set[str] = field(default_factory=set)
    text_files_indexed: int = 0
    text_bytes_indexed: int = 0
    file_text_cache: dict[str, str] = field(default_factory=dict)
    file_lines_cache: dict[str, list[str]] = field(default_factory=dict)
    file_lines_lower_cache: dict[str, list[str]] = field(default_factory=dict)
    file_signature_cache: dict[str, tuple[int, int, float]] = field(default_factory=dict)
    content_terms_cache: dict[str, list[str]] = field(default_factory=dict)
    python_symbols: list[PythonSymbol] = field(default_factory=list)
    symbols_by_path: dict[str, list[PythonSymbol]] = field(default_factory=dict)
    symbol_index: dict[str, list[PythonSymbol]] = field(default_factory=dict)
    symbol_prefix_index: dict[str, list[PythonSymbol]] = field(default_factory=dict)  # 3-char prefix → symbols
    qualname_index: dict[str, list[PythonSymbol]] = field(default_factory=dict)
    symbol_definition_paths: dict[str, set[str]] = field(default_factory=dict)
    symbol_reference_index: dict[str, Counter[str]] = field(default_factory=dict)
    # ── O(1) skeleton DSL cache ──────────────────────────────────────────────
    skeleton_dsl_cache: dict[str, str] = field(default_factory=dict)  # path → rendered DSL
    # ── BM25 rebuild guard ───────────────────────────────────────────────────
    _bm25_built_key: str = field(default="")  # root+rebuild_counter; skip rebuild if unchanged
    test_symbols: list[PythonSymbol] = field(default_factory=list)
    python_symbol_cache: dict[str, tuple[tuple[int, int, float], list[PythonSymbol], Counter[str]]] = field(default_factory=dict)
    bm25_term_frequencies: dict[str, Counter[str]] = field(default_factory=dict)
    bm25_document_frequencies: Counter[str] = field(default_factory=Counter)
    bm25_document_lengths: dict[str, int] = field(default_factory=dict)
    bm25_doc_norms: dict[str, float] = field(default_factory=dict)
    bm25_inverted_index: dict[str, dict[str, int]] = field(default_factory=dict)
    query_cache: dict[tuple[str, str, str | None, str | None, str | None, int], list[tuple[FsEntryModel, float]]] = field(default_factory=dict)
    rebuild_counter: int = 0
    bm25_average_document_length: float = 0.0
    bm25_ready: bool = False
    scip_graph: SCIPGraph | None = None

    def clear(self, *, preserve_file_caches: bool = False) -> None:
        self.rebuild_counter += 1
        self.query_cache.clear()
        self.scip_graph = None
        self.snapshot = None
        self.last_built_at = None
        self.by_path.clear()
        self.token_index.clear()
        self.content_index.clear()
        self.suffix_index.clear()
        self.type_index.clear()
        self.mime_index.clear()
        self.children_index.clear()
        self.content_indexed_paths.clear()
        self.text_files_indexed = 0
        self.text_bytes_indexed = 0
        self.python_symbols.clear()
        self.symbols_by_path.clear()
        self.symbol_index.clear()
        self.symbol_prefix_index.clear()
        self.skeleton_dsl_cache.clear()
        self._bm25_built_key = ""
        self.qualname_index.clear()
        self.symbol_definition_paths.clear()
        self.symbol_reference_index.clear()
        self.test_symbols.clear()
        if not preserve_file_caches:
            self.cache_root = None
            self.file_text_cache.clear()
            self.file_lines_cache.clear()
            self.file_signature_cache.clear()
            self.content_terms_cache.clear()
            self.python_symbol_cache.clear()
        self.bm25_term_frequencies.clear()
        self.bm25_document_frequencies.clear()
        self.bm25_document_lengths.clear()
        self.bm25_doc_norms.clear()
        self.bm25_inverted_index.clear()
        self.bm25_average_document_length = 0.0
        self.bm25_ready = False

    def rebuild(self, root: str | Path, ramdisk: RamDiskInfo | None = None) -> dict[str, object]:
        snapshot = build_snapshot(root, ramdisk, ignore_names=self.ignored_names)
        root_path = Path(snapshot.summary.root)
        self._prepare_rebuild_caches(root_path, snapshot.models)
        self.clear(preserve_file_caches=True)
        self.snapshot = snapshot
        self.last_built_at = time.time()

        for model in snapshot.models:
            self.by_path[model.path] = model
            for token in _tokenize(f"{model.name} {model.path}"):
                self.token_index.setdefault(token, set()).add(model.path)
            if model.suffix:
                self.suffix_index.setdefault(model.suffix.lower(), set()).add(model.path)
            self.type_index.setdefault(model.entry_type, set()).add(model.path)
            if model.mime_type:
                self.mime_index.setdefault(model.mime_type.lower(), set()).add(model.path)
            self._index_content(root_path, model)
            self._index_python_symbols(root_path, model)

        for path, model in self.by_path.items():
            if path == ".":
                parent = None
            elif "/" in path:
                parent = path.rsplit("/", 1)[0]
            else:
                parent = "."
            if parent is not None:
                self.children_index.setdefault(parent, []).append(path)
            if model.entry_type == "directory":
                self.children_index.setdefault(path, [])

        for child_paths in self.children_index.values():
            child_paths.sort(key=str.lower)

        self._build_bm25()
        self._try_build_scip(root_path, ramdisk)

        return self.stats()

    def rebuild_incremental(self, root: str | Path, ramdisk: RamDiskInfo | None = None) -> dict[str, object]:
        """Delta-update: only re-index files that are new or have changed signatures.

        Falls back to a full rebuild when the root changes or no previous snapshot exists.
        Returns stats with an extra ``incremental_changes`` key describing what was processed.
        """
        if self.snapshot is None or self.cache_root != str(Path(root).expanduser().resolve()):
            stats = self.rebuild(root, ramdisk)
            stats["incremental_changes"] = {"mode": "full_fallback", "added": 0, "modified": 0, "removed": 0}
            return stats

        snapshot = build_snapshot(root, ramdisk, ignore_names=self.ignored_names)
        root_path = Path(snapshot.summary.root)
        self._prepare_rebuild_caches(root_path, snapshot.models)

        # Classify changes vs previous snapshot
        new_by_path: dict[str, FsEntryModel] = {m.path: m for m in snapshot.models}
        old_paths = set(self.by_path)
        new_paths = set(new_by_path)

        removed_paths = old_paths - new_paths
        added_paths = new_paths - old_paths
        modified_paths = {
            path for path in old_paths & new_paths
            if self._model_signature(self.by_path[path]) != self._model_signature(new_by_path[path])
        }

        changed_paths = added_paths | modified_paths

        # Nothing changed — return current stats immediately
        if not removed_paths and not changed_paths:
            stats = self.stats()
            stats["incremental_changes"] = {"mode": "incremental", "added": 0, "modified": 0, "removed": 0}
            return stats

        # 1. Remove stale entries from all forward indexes
        for path in removed_paths | modified_paths:
            old_model = self.by_path.pop(path, None)
            if old_model is None:
                continue
            # token_index
            for token in _tokenize(f"{old_model.name} {old_model.path}"):
                s = self.token_index.get(token)
                if s:
                    s.discard(path)
                    if not s:
                        del self.token_index[token]
            # suffix_index
            if old_model.suffix:
                s = self.suffix_index.get(old_model.suffix.lower())
                if s:
                    s.discard(path)
            # type_index
            s = self.type_index.get(old_model.entry_type)
            if s:
                s.discard(path)
            # mime_index
            if old_model.mime_type:
                s = self.mime_index.get(old_model.mime_type.lower())
                if s:
                    s.discard(path)
            # content_index
            self.content_indexed_paths.discard(path)
            for token_set in list(self.content_index.values()):
                token_set.discard(path)
            # symbol indexes
            for sym in self.symbols_by_path.pop(path, []):
                sl = self.symbol_index.get(sym.name.lower())
                if sl:
                    try:
                        sl.remove(sym)
                    except ValueError:
                        pass
                sl = self.qualname_index.get(sym.qualname.lower())
                if sl:
                    try:
                        sl.remove(sym)
                    except ValueError:
                        pass
                self.symbol_definition_paths.get(sym.name.lower(), set()).discard(path)
                self.symbol_definition_paths.get(sym.qualname.lower(), set()).discard(path)
            self.python_symbols = [s for s in self.python_symbols if s.path != path]
            self.test_symbols = [s for s in self.test_symbols if s.path != path]
            self.symbol_reference_index.pop(path, None)

        # 2. Add/re-index new and modified entries
        for path in changed_paths:
            model = new_by_path[path]
            self.by_path[path] = model
            for token in _tokenize(f"{model.name} {model.path}"):
                self.token_index.setdefault(token, set()).add(path)
            if model.suffix:
                self.suffix_index.setdefault(model.suffix.lower(), set()).add(path)
            self.type_index.setdefault(model.entry_type, set()).add(path)
            if model.mime_type:
                self.mime_index.setdefault(model.mime_type.lower(), set()).add(path)
            self._index_content(root_path, model)
            self._index_python_symbols(root_path, model)

        # 3. Rebuild children_index (cheap — just dicts of lists)
        self.children_index.clear()
        for path, model in self.by_path.items():
            if path == ".":
                parent = None
            elif "/" in path:
                parent = path.rsplit("/", 1)[0]
            else:
                parent = "."
            if parent is not None:
                self.children_index.setdefault(parent, []).append(path)
            if model.entry_type == "directory":
                self.children_index.setdefault(path, [])
        for child_paths in self.children_index.values():
            child_paths.sort(key=str.lower)

        # 4. Rebuild BM25 over updated by_path (still O(N) but unavoidable for avgdl)
        self.bm25_term_frequencies.clear()
        self.bm25_document_frequencies.clear()
        self.bm25_document_lengths.clear()
        self.bm25_doc_norms.clear()
        self.bm25_inverted_index.clear()
        self.bm25_ready = False
        self._build_bm25()

        # 5. Commit new snapshot and invalidate query cache
        self.snapshot = snapshot
        self.last_built_at = time.time()
        self.rebuild_counter += 1
        self.query_cache.clear()

        stats = self.stats()
        stats["incremental_changes"] = {
            "mode": "incremental",
            "added": len(added_paths),
            "modified": len(modified_paths),
            "removed": len(removed_paths),
        }
        return stats

    def stats(self) -> dict[str, object]:
        if self.snapshot is None:
            return {
                "indexed": False,
                "root": None,
                "last_built_at": self.last_built_at,
                "total_entries": 0,
                "total_files": 0,
                "total_directories": 0,
                "total_symlinks": 0,
                "total_size_bytes": 0,
                "token_count": 0,
                "content_token_count": 0,
                "suffix_count": 0,
                "mime_count": 0,
                "text_files_indexed": 0,
                "text_bytes_indexed": 0,
                "python_symbol_count": 0,
                "python_test_symbol_count": 0,
                "python_file_count": 0,
                "python_reference_name_count": 0,
                "bm25_ready": False,
                "bm25_backend": "cpu",
                "bm25_loaded_in_memory": False,
                "bm25_loaded_in_gpu": False,
                "bm25_documents": 0,
                "bm25_avg_document_length": 0.0,
                "ignored_names": sorted(self.ignored_names),
            }
        summary = self.snapshot.summary
        return {
            "indexed": True,
            "root": summary.root,
            "last_built_at": self.last_built_at,
            "total_entries": summary.total_entries,
            "total_files": summary.total_files,
            "total_directories": summary.total_directories,
            "total_symlinks": summary.total_symlinks,
            "total_size_bytes": summary.total_size_bytes,
            "token_count": len(self.token_index),
            "content_token_count": len(self.content_index),
            "suffix_count": len(self.suffix_index),
            "mime_count": len(self.mime_index),
            "text_files_indexed": self.text_files_indexed,
            "text_bytes_indexed": self.text_bytes_indexed,
            "python_symbol_count": len(self.python_symbols),
            "python_test_symbol_count": len(self.test_symbols),
            "python_file_count": len(self.symbols_by_path),
            "python_reference_name_count": len(self.symbol_reference_index),
            "bm25_ready": self.bm25_ready,
            "bm25_backend": "cpu",
            "bm25_loaded_in_memory": self.bm25_ready,
            "bm25_loaded_in_gpu": False,
            "bm25_documents": len(self.bm25_term_frequencies),
            "bm25_avg_document_length": self.bm25_average_document_length,
            "scip_available": is_scip_available(),
            "scip_loaded": self.scip_graph is not None,
            "ignored_names": sorted(self.ignored_names),
        }

    def get_skeleton_dsl(self, path: str) -> str:
        """Render a file's skeleton DSL with cross-references for AI context."""
        normalized = self.normalize_path(path)
        # O(1) cache — skeleton is deterministic for a given index rebuild
        cached = self.skeleton_dsl_cache.get(normalized)
        if cached is not None:
            return cached
        result = render_file_skeleton_dsl(normalized, self)
        self.skeleton_dsl_cache[normalized] = result
        return result

    def get_contour_skeleton_dsl(self, query: str, limit: int = 10) -> str:
        """Render a search query's matching symbol contour in skeleton DSL format."""
        symbols = self.search_symbols(query, limit=limit)
        return render_contour_skeleton_dsl(symbols, self, contour_name=query)

    def _try_build_scip(self, root_path: Path, ramdisk: RamDiskInfo | None) -> None:
        if not is_scip_available():
            return
        out_dir = Path(ramdisk.mount_point) if ramdisk else root_path
        scip_out = out_dir / "index.scip"
        built_file = build_scip_index(root_path, scip_out)
        if built_file:
            self.scip_graph = load_scip_or_fallback(built_file)

    def save_disk_cache(self, cache_file: Path) -> bool:
        """Persist index state and symbol caches to disk / RAM Disk for instant startup."""
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "file_signature_cache": self.file_signature_cache,
                "python_symbols": [s.to_dict() for s in self.python_symbols],
                "last_built_at": self.last_built_at,
                "cache_root": self.cache_root,
            }
            cache_file.write_bytes(pickle.dumps(data))
            return True
        except Exception:
            return False

    def load_disk_cache(self, cache_file: Path) -> bool:
        """Load index state from disk / RAM Disk cache if valid."""
        if not cache_file.exists():
            return False
        try:
            data = pickle.loads(cache_file.read_bytes())
            if not data or not data.get("cache_root"):
                return False
            self.cache_root = data["cache_root"]
            self.last_built_at = data.get("last_built_at")
            self.file_signature_cache = data.get("file_signature_cache", {})
            sym_dicts = data.get("python_symbols", [])
            self.python_symbols = [PythonSymbol(**d) for d in sym_dicts]
            return True
        except Exception:
            return False

    def normalize_path(self, path: str | None) -> str:
        if path is None:
            raise ValueError("path is required")
        normalized = path.strip()
        if not normalized or normalized == ".":
            return "."
        normalized = normalized.removeprefix("./").strip("/")
        if not normalized:
            return "."
        return normalized

    def get_by_path(self, path: str) -> FsEntryModel:
        normalized = self.normalize_path(path)
        try:
            return self.by_path[normalized]
        except KeyError as exc:
            raise FileNotFoundError(normalized) from exc

    def get_children(self, path: str) -> list[FsEntryModel]:
        parent = self.get_by_path(path)
        if parent.entry_type != "directory":
            raise ValueError(f"Path is not a directory: {parent.path}")
        return [self.by_path[child_path] for child_path in self.children_index.get(parent.path, [])]

    def _prepare_rebuild_caches(self, root_path: Path, models: list[FsEntryModel]) -> None:
        root_str = str(root_path)
        if self.cache_root != root_str:
            self.cache_root = root_str
            self.file_text_cache.clear()
            self.file_lines_cache.clear()
            self.file_lines_lower_cache.clear()
            self.file_signature_cache.clear()
            self.content_terms_cache.clear()
            self.python_symbol_cache.clear()
            return
        active_paths = {model.path for model in models if model.entry_type == "file"}
        for cache in (
            self.file_text_cache,
            self.file_lines_cache,
            self.file_lines_lower_cache,
            self.file_signature_cache,
            self.content_terms_cache,
            self.python_symbol_cache,
        ):
            for path in list(cache.keys()):
                if path not in active_paths:
                    del cache[path]

    def _model_signature(self, model: FsEntryModel) -> tuple[int, int, float]:
        return (model.inode, model.size_bytes, model.modified_at)

    def _read_cached_text(self, root_path: Path, path: str, signature: tuple[int, int, float]) -> str | None:
        cached_signature = self.file_signature_cache.get(path)
        cached = self.file_text_cache.get(path)
        if cached is not None and cached_signature == signature:
            return cached
        if cached_signature != signature:
            self.file_lines_cache.pop(path, None)
            self.file_lines_lower_cache.pop(path, None)
            self.content_terms_cache.pop(path, None)
            self.python_symbol_cache.pop(path, None)
        if cached is not None:
            self.file_text_cache.pop(path, None)
        file_path = root_path if path == "." else root_path / path
        try:
            cached = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        self.file_text_cache[path] = cached
        self.file_signature_cache[path] = signature
        return cached

    def _get_cached_lines(self, root_path: Path, path: str, signature: tuple[int, int, float]) -> list[str] | None:
        cached = self.file_lines_cache.get(path)
        if cached is not None and self.file_signature_cache.get(path) == signature:
            return cached
        if cached is not None:
            self.file_lines_cache.pop(path, None)
            self.file_lines_lower_cache.pop(path, None)
        text = self._read_cached_text(root_path, path, signature)
        if text is None:
            return None
        cached = text.splitlines() or [text]
        self.file_lines_cache[path] = cached
        self.file_lines_lower_cache[path] = [line.lower() for line in cached]
        return cached

    def _get_cached_content_terms(self, root_path: Path, model: FsEntryModel) -> list[str] | None:
        cached = self.content_terms_cache.get(model.path)
        signature = self._model_signature(model)
        if cached is not None and self.file_signature_cache.get(model.path) == signature:
            return cached
        if cached is not None:
            self.content_terms_cache.pop(model.path, None)
        text = self._read_cached_text(root_path, model.path, signature)
        if text is None:
            return None
        cached = _tokenize_terms(text)
        self.content_terms_cache[model.path] = cached
        # LRU cap: drop the oldest entry when cache exceeds limit
        if len(self.content_terms_cache) > self._CONTENT_TERMS_CACHE_MAX:
            self.content_terms_cache.pop(next(iter(self.content_terms_cache)))
        return cached

    def _warm_line_cache(self) -> None:
        warmed: dict[str, list[str]] = {}
        for path, text in self.file_text_cache.items():
            if path in self.file_signature_cache:
                warmed[path] = text.splitlines() or [text]
        self.file_lines_cache = warmed

    def _peek_cached_text(self, path: str, signature: tuple[int, int, float]) -> str | None:
        if self.file_signature_cache.get(path) != signature:
            return None
        return self.file_text_cache.get(path)

    def _peek_cached_lines(self, path: str, signature: tuple[int, int, float]) -> list[str] | None:
        if self.file_signature_cache.get(path) != signature:
            return None
        return self.file_lines_cache.get(path)

    def _peek_cached_lines_lower(self, path: str, signature: tuple[int, int, float]) -> list[str] | None:
        """Return pre-lowercased lines if available — avoids per-query str.lower() in scoring."""
        if self.file_signature_cache.get(path) != signature:
            return None
        return self.file_lines_lower_cache.get(path)

    def search_symbols(
        self,
        name: str,
        *,
        kind: str | None = None,
        path_prefix: str | None = None,
        limit: int = 20,
    ) -> list[PythonSymbol]:
        normalized = name.strip().lower()
        if not normalized:
            candidates = list(self.python_symbols)
        else:
            candidates = [*self.symbol_index.get(normalized, []), *self.qualname_index.get(normalized, [])]
            if not candidates and len(normalized) >= 3:
                # FIX: O(1) prefix lookup instead of O(N) linear scan over all symbols
                pfx = normalized[:3]
                prefix_candidates = self.symbol_prefix_index.get(pfx, [])
                candidates = [
                    sym for sym in prefix_candidates
                    if normalized in sym.name.lower() or normalized in sym.qualname.lower()
                ]
            elif not candidates:
                # Short query (<3 chars): still need linear scan, but rare in practice
                candidates = [
                    sym for sym in self.python_symbols
                    if normalized in sym.name.lower()
                ]
        deduped: dict[tuple[str, str, int], PythonSymbol] = {}
        for symbol in candidates:
            deduped[(symbol.path, symbol.qualname, symbol.line)] = symbol
        filtered = list(deduped.values())
        if kind:
            filtered = [symbol for symbol in filtered if symbol.kind == kind]
        if path_prefix:
            prefix = path_prefix.strip("/")
            filtered = [symbol for symbol in filtered if symbol.path == prefix or symbol.path.startswith(f"{prefix}/")]
        filtered.sort(key=lambda symbol: (symbol.path.lower(), symbol.line, symbol.qualname.lower()))
        return filtered[: max(limit, 0)]

    def find_symbol_usages(
        self,
        name: str,
        *,
        path_prefix: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        normalized = name.strip().lower()
        if not normalized:
            return []
        path_counts = self.symbol_reference_index.get(normalized, Counter())
        rows: list[dict[str, object]] = []
        for path, count in path_counts.most_common():
            if path_prefix:
                prefix = path_prefix.strip("/")
                if not (path == prefix or path.startswith(f"{prefix}/")):
                    continue
            definition_paths = self.symbol_definition_paths.get(normalized, set())
            rows.append(
                {
                    "path": path,
                    "count": count,
                    "defines_symbol": path in definition_paths,
                    "excerpt": self.get_excerpt(path, name),
                }
            )
            if len(rows) >= max(limit, 0):
                break
        return rows

    def find_related_tests(
        self,
        name: str,
        *,
        path_prefix: str | None = None,
        limit: int = 20,
    ) -> list[PythonSymbol]:
        normalized = name.strip().lower()
        if not normalized:
            return []
        scored: list[tuple[int, PythonSymbol]] = []
        for symbol in self.test_symbols:
            if path_prefix:
                prefix = path_prefix.strip("/")
                if not (symbol.path == prefix or symbol.path.startswith(f"{prefix}/")):
                    continue
            score = 0
            if normalized in symbol.name.lower() or normalized in symbol.qualname.lower():
                score += 4
            reference_count = self.symbol_reference_index.get(normalized, Counter()).get(symbol.path, 0)
            if reference_count:
                score += reference_count * 3
            if normalized in symbol.path.lower():
                score += 2
            if score > 0:
                scored.append((score, symbol))
        scored.sort(key=lambda item: (-item[0], item[1].path.lower(), item[1].line))
        return [symbol for _, symbol in scored[: max(limit, 0)]]

    def get_symbol_excerpt(self, symbol: PythonSymbol, query: str, *, max_lines: int = 6, max_chars: int = 240) -> str | None:
        if self.snapshot is None:
            return None
        model = self.by_path.get(symbol.path)
        if model is None:
            return None
        signature = self._model_signature(model)
        lines = self._peek_cached_lines(symbol.path, signature)
        if lines is None:
            text = self._peek_cached_text(symbol.path, signature)
            if text is None:
                file_path = Path(self.snapshot.summary.root) / symbol.path
                try:
                    text = file_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    return None
            lines = text.splitlines() or [text]
            # Populate both caches so future calls (and get_excerpt) get warm hits.
            self.file_lines_cache[symbol.path] = lines
            self.file_lines_lower_cache[symbol.path] = [line.lower() for line in lines]
        if lines is None:
            return None
        start = max(symbol.line - 1, 0)
        end = min(len(lines), max(symbol.end_line, symbol.line) + max_lines - 1)
        query_terms = _tokenize_terms(query)
        highlighter = _build_highlighter(frozenset(query_terms))
        excerpt_lines: list[str] = []
        for index in range(start, end):
            snippet = lines[index].rstrip()
            if highlighter is not None:
                snippet = highlighter(snippet)
            if len(snippet) > max_chars:
                snippet = snippet[: max_chars - 1] + "…"
            excerpt_lines.append(f"line {index + 1}: {snippet}")
        return "\n".join(excerpt_lines) if excerpt_lines else None

    def search(
        self,
        query: str = "",
        *,
        content_query: str = "",
        entry_type: str | None = None,
        suffix: str | None = None,
        path_prefix: str | None = None,
        limit: int = 50,
    ) -> list[FsEntryModel]:
        ranked = self.search_with_scores(
            query,
            content_query=content_query,
            entry_type=entry_type,
            suffix=suffix,
            path_prefix=path_prefix,
            limit=limit,
        )
        return [model for model, _ in ranked]

    _QUERY_CACHE_MAX = 256
    _CONTENT_TERMS_CACHE_MAX = 2048

    def search_with_scores(
        self,
        query: str = "",
        *,
        content_query: str = "",
        entry_type: str | None = None,
        suffix: str | None = None,
        path_prefix: str | None = None,
        limit: int = 50,
    ) -> list[tuple[FsEntryModel, float]]:
        cache_key = (query, content_query, entry_type, suffix, path_prefix, limit, self.rebuild_counter)
        cached = self.query_cache.get(cache_key)
        if cached is not None:
            return cached

        candidates = set(self.by_path)
        bm25_tokens: list[str] = []
        normalized_query = query.strip().lower()
        if normalized_query:
            tokens = _tokenize(normalized_query)
            if tokens:
                bm25_tokens.extend(_tokenize_terms(normalized_query))
                token_hits: set[str] = set()
                for token in tokens:
                    token_hits |= self.token_index.get(token, set())
                candidates &= token_hits if token_hits else set()
            else:
                candidates = {
                    path
                    for path, model in self.by_path.items()
                    if normalized_query in model.name.lower() or normalized_query in model.path.lower()
                }

        normalized_content_query = content_query.strip().lower()
        if normalized_content_query:
            content_tokens = _tokenize(normalized_content_query)
            if content_tokens:
                bm25_tokens.extend(_tokenize_terms(normalized_content_query))
                token_hits: set[str] = set()
                for token in content_tokens:
                    token_hits |= self.content_index.get(token, set())
                candidates &= token_hits if token_hits else set()
            else:
                candidates = set()

        if entry_type:
            candidates &= self.type_index.get(entry_type, set())
        if suffix:
            normalized_suffix = suffix.lower() if suffix.startswith(".") else f".{suffix.lower()}"
            candidates &= self.suffix_index.get(normalized_suffix, set())
        if path_prefix:
            prefix = "." if path_prefix in {"", "."} else path_prefix.strip("/")
            candidates = {path for path in candidates if path == prefix or path.startswith(f"{prefix}/")}

        safe_limit = max(limit, 0)
        query_idfs = self._precompute_query_idfs(bm25_tokens)
        scored_pairs = [(path, self._bm25_score(path, bm25_tokens, query_idfs=query_idfs)) for path in candidates]

        if safe_limit > 0 and len(scored_pairs) > safe_limit:
            top = heapq.nlargest(safe_limit, scored_pairs, key=lambda item: (item[1], item[0].lower()))
            top.sort(key=lambda item: (-item[1], item[0].lower()))
        else:
            top = sorted(scored_pairs, key=lambda item: (-item[1], item[0].lower()))[:safe_limit]

        result = [(self.by_path[path], score) for path, score in top]

        if len(self.query_cache) >= self._QUERY_CACHE_MAX:
            self.query_cache.pop(next(iter(self.query_cache)))
        self.query_cache[cache_key] = result
        return result

    def _precompute_query_idfs(self, query_terms: list[str]) -> dict[str, float]:
        if not self.bm25_ready or not query_terms:
            return {}
        doc_count = len(self.bm25_term_frequencies)
        idfs: dict[str, float] = {}
        for token in set(query_terms):
            df = self.bm25_document_frequencies.get(token, 0)
            if df > 0:
                idfs[token] = math.log(1 + ((doc_count - df + 0.5) / (df + 0.5)))
        return idfs

    def _index_content(self, root_path: Path, model: FsEntryModel) -> None:
        if not self._should_index_content(model):
            return
        content_terms = self._get_cached_content_terms(root_path, model)
        if content_terms is None:
            return
        tokens = set(content_terms)
        if not tokens:
            return
        self.content_indexed_paths.add(model.path)
        self.text_files_indexed += 1
        self.text_bytes_indexed += model.size_bytes
        for token in tokens:
            self.content_index.setdefault(token, set()).add(model.path)

    def _index_python_symbols(self, root_path: Path, model: FsEntryModel) -> None:
        if model.entry_type != "file":
            return
        suffix = (model.suffix or "").lower()
        if suffix not in PYTHON_EXTENSIONS and suffix not in C_EXTENSIONS and suffix not in OTHER_EXTENSIONS:
            return
        file_path = root_path / model.path if model.path != "." else root_path
        signature = self._model_signature(model)
        cached = self.python_symbol_cache.get(model.path)
        if cached is not None and cached[0] == signature:
            symbols, references = cached[1], cached[2]
        else:
            source = self._read_cached_text(root_path, model.path, signature)
            symbols, references = extract_code_symbols(file_path, model.path, source=source)
            self.python_symbol_cache[model.path] = (signature, symbols, references)
        if not symbols and not references:
            return
        self.python_symbols.extend(symbols)
        self.symbols_by_path[model.path] = symbols
        for symbol in symbols:
            self.symbol_index.setdefault(symbol.name.lower(), []).append(symbol)
            self.qualname_index.setdefault(symbol.qualname.lower(), []).append(symbol)
            # Build prefix index for O(1) substring lookup (replaces O(N) linear scan fallback)
            _nm_lower = symbol.name.lower()
            for _start in range(len(_nm_lower) - 2):  # all 3+ char prefixes
                _pfx = _nm_lower[_start:_start + 3]
                self.symbol_prefix_index.setdefault(_pfx, []).append(symbol)
            if symbol.kind in {"class", "function", "method", "struct", "enum", "union", "macro"}:
                self.symbol_definition_paths.setdefault(symbol.name.lower(), set()).add(symbol.path)
                self.symbol_definition_paths.setdefault(symbol.qualname.lower(), set()).add(symbol.path)
            if symbol.is_test:
                self.test_symbols.append(symbol)
        for reference_name, count in references.items():
            # FIX: reuse existing Counter instead of creating a new one per reference
            ref_entry = self.symbol_reference_index.get(reference_name)
            if ref_entry is None:
                ref_entry = Counter()
                self.symbol_reference_index[reference_name] = ref_entry
            ref_entry[model.path] = count

    def _should_index_content(self, model: FsEntryModel) -> bool:
        if model.entry_type != "file":
            return False
        if model.size_bytes > MAX_TEXT_FILE_SIZE:
            return False
        suffix = (model.suffix or "").lower()
        mime_type = (model.mime_type or "").lower()
        if suffix in TEXT_SUFFIXES:
            return True
        return mime_type.startswith("text/") or mime_type in {
            "application/json",
            "application/sql",
            "application/xml",
        }

    def _build_bm25(self) -> None:
        # Guard: skip rebuild if already built for this exact root + rebuild_counter
        current_key = f"{getattr(self.snapshot, 'summary', None) and self.snapshot.summary.root}:{self.rebuild_counter}"
        if self._bm25_built_key == current_key and self.bm25_ready:
            return
        total_length = 0
        root_path = Path(self.snapshot.summary.root) if self.snapshot is not None else None
        for path, model in self.by_path.items():
            terms = self._document_terms(model, root_path=root_path)
            if not terms:
                continue
            term_freq = Counter(terms)
            self.bm25_term_frequencies[path] = term_freq
            self.bm25_document_lengths[path] = sum(term_freq.values())
            total_length += self.bm25_document_lengths[path]
            for token in term_freq:
                self.bm25_document_frequencies[token] += 1
        doc_count = len(self.bm25_term_frequencies)
        self.bm25_average_document_length = total_length / doc_count if doc_count else 0.0
        self.bm25_ready = doc_count > 0

        if self.bm25_ready:
            for path, term_freq in self.bm25_term_frequencies.items():
                doc_length = self.bm25_document_lengths[path]
                self.bm25_doc_norms[path] = BM25_K1 * (1 - BM25_B + BM25_B * (doc_length / self.bm25_average_document_length))
                for token, freq in term_freq.items():
                    self.bm25_inverted_index.setdefault(token, {})[path] = freq
            self._bm25_built_key = current_key  # mark as done

    def _document_terms(self, model: FsEntryModel, *, root_path: Path | None = None) -> list[str]:
        """Build the weighted term list used by BM25 for this document.

        Field weights (repeated token copies simulate higher TF):
          _W_SYMBOL=8  — AST symbol names (functions, classes, methods)
          _W_FILENAME=4 — stem of the filename
          _W_PATH=2    — parent directory path segments
          _W_CONTENT=1 — raw file body tokens (stop-word filtered)
        """
        terms: list[str] = []

        # 1. Filename tokens — high weight (4x)
        name_stem = model.name.rsplit(".", 1)[0] if "." in model.name else model.name
        for t in _tokenize_terms_for_index(name_stem):
            terms.extend([t] * _W_FILENAME)

        # 2. Parent directory path segments — medium weight (2x)
        path_parts = model.path.replace("\\", "/").split("/")
        for segment in path_parts[:-1]:  # skip the filename itself
            for t in _tokenize_terms_for_index(segment):
                terms.extend([t] * _W_PATH)

        # 3. AST symbols for this file — highest weight (8x)
        #    This is the key enrichment: function/class names are the most
        #    discriminative signal and were previously absent from BM25.
        for sym in self.symbols_by_path.get(model.path, []):
            sym_name_terms = _tokenize_terms_for_index(sym.name)
            for t in sym_name_terms:
                terms.extend([t] * _W_SYMBOL)
            # qualname (e.g. ClassName.method_name) at half symbol weight
            if sym.qualname and sym.qualname != sym.name:
                for t in _tokenize_terms_for_index(sym.qualname):
                    terms.extend([t] * (_W_SYMBOL // 2))

        # 4. File body content — baseline weight (1x), stop-word filtered
        if model.path in self.content_indexed_paths and root_path is not None:
            content_terms = self._get_cached_content_terms(root_path, model)
            if content_terms is not None:
                # content_terms already tokenized; apply stop-word filter here
                terms.extend(
                    t for t in content_terms
                    if t not in CODE_STOP_WORDS and len(t) >= 2
                )

        return terms

    def _proximity_boost(self, path: str, query_terms: list[str]) -> float:
        """Extra score when multiple query terms co-occur on the same line.

        Motivation: a file where 'rate' and 'limit' appear on the same line
        is more relevant to a 'rate limit' query than one where both words
        exist but are 500 lines apart.

        The boost is capped to avoid drowning the base BM25 signal.
        """
        if len(query_terms) < 2:
            return 0.0
        model = self.by_path.get(path)
        if model is None:
            return 0.0
        sig = self._model_signature(model)
        lines_lower = self._peek_cached_lines_lower(path, sig)
        if not lines_lower:
            return 0.0
        unique_terms = list(dict.fromkeys(query_terms))  # deduplicated
        boost = 0.0
        for line in lines_lower:
            hits = sum(1 for t in unique_terms if t in line)
            if hits >= len(unique_terms):      # ALL terms on one line
                boost += 3.0
            elif hits >= 2:                    # at least 2 terms together
                boost += 0.4 * hits
        return min(boost, 5.0)  # hard cap

    def _bm25_score(self, path: str, query_terms: list[str], query_idfs: dict[str, float] | None = None) -> float:
        if not self.bm25_ready or not query_terms:
            return 0.0
        term_freq = self.bm25_term_frequencies.get(path)
        if not term_freq:
            return 0.0
        norm = self.bm25_doc_norms.get(path)
        if norm is None:
            doc_length = self.bm25_document_lengths.get(path, 0)
            if doc_length == 0 or self.bm25_average_document_length == 0:
                return 0.0
            norm = BM25_K1 * (1 - BM25_B + BM25_B * (doc_length / self.bm25_average_document_length))
        score = 0.0
        for token in query_terms:
            frequency = term_freq.get(token, 0)
            if frequency == 0:
                continue
            if query_idfs is not None and token in query_idfs:
                idf = query_idfs[token]
            else:
                doc_frequency = self.bm25_document_frequencies.get(token, 0)
                numerator = len(self.bm25_term_frequencies) - doc_frequency + 0.5
                denominator = doc_frequency + 0.5
                idf = math.log(1 + (numerator / denominator))
            score += idf * ((frequency * (BM25_K1 + 1)) / (frequency + norm))
        # Proximity boost: reward co-occurrence of multiple terms on same line
        # Damped by 0.25 to avoid overriding base BM25 ordering.
        if len(query_terms) >= 2:
            score += self._proximity_boost(path, query_terms) * 0.25
        return score

    def get_excerpt(
        self,
        path: str,
        query: str,
        *,
        max_chars: int = 220,
        context_lines: int = 3,
        max_clusters: int = 2,
    ) -> str | None:
        """Return a rich code excerpt centred on the best query matches.

        Improvements over the original:
        - context_lines=3 → 7-line windows instead of 3-line windows
        - Returns up to max_clusters=2 separate match clusters, separated by
          a '---' divider, so callers see multiple relevant code sections.
        - Line scores based on multi-term count per line (most hits = best).
        """
        if self.snapshot is None or path not in self.content_indexed_paths:
            return None
        model = self.by_path.get(path)
        if model is None:
            return None
        signature = self._model_signature(model)
        text = self._peek_cached_text(path, signature)
        if text is None:
            file_path = Path(self.snapshot.summary.root) / path if path != "." else Path(self.snapshot.summary.root)
            try:
                text = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return None
        if text is None:
            return None
        terms = _tokenize_terms(query)
        lines = self._peek_cached_lines(path, signature)
        if lines is None:
            lines = text.splitlines() or [text]

        if not terms:
            # No query terms: return the first non-empty line
            for index, line in enumerate(lines):
                if line.strip():
                    return f"line {index + 1}: {line.strip()[:max_chars]}"
            return None

        lines_lower = self._peek_cached_lines_lower(path, signature)
        if lines_lower is None:
            lines_lower = [line.lower() for line in lines]

        # Score every line by how many query terms appear in it
        line_scores: list[tuple[int, int]] = []  # (score, line_index)
        for idx, lowered in enumerate(lines_lower):
            sc = sum(lowered.count(t) for t in terms if t in lowered)
            if sc > 0:
                line_scores.append((sc, idx))

        if not line_scores:
            # No hits in content — try path match
            if any(t in path.lower() for t in terms):
                return f"path match: {_highlight_terms(path, terms)}"
            snippet = next((ln.strip() for ln in lines if ln.strip()), "")
            if not snippet:
                return None
            return f"line 1: {snippet[:max_chars]}"

        # Sort by score descending, then group into clusters by line proximity
        line_scores.sort(key=lambda x: -x[0])
        selected_centers: list[int] = []
        used: set[int] = set()
        for _, idx in line_scores:
            # Skip if this line is already covered by a previously selected cluster
            if any(abs(idx - c) <= context_lines * 2 for c in selected_centers):
                continue
            selected_centers.append(idx)
            used.add(idx)
            if len(selected_centers) >= max_clusters:
                break
        selected_centers.sort()  # restore document order

        highlighter = _build_highlighter(frozenset(terms))
        cluster_texts: list[str] = []
        for center in selected_centers:
            start = max(0, center - context_lines)
            end = min(len(lines), center + context_lines + 1)
            cluster_lines: list[str] = []
            for idx in range(start, end):
                snippet = lines[idx].strip()
                if not snippet:
                    continue
                if highlighter is not None:
                    snippet = highlighter(snippet)
                if len(snippet) > max_chars:
                    snippet = snippet[: max_chars - 1] + "…"
                marker = "► " if idx == center else "  "
                cluster_lines.append(f"{marker}line {idx + 1}: {snippet}")
            if cluster_lines:
                cluster_texts.append("\n".join(cluster_lines))

        if cluster_texts:
            return "\n--- (next match) ---\n".join(cluster_texts)
        if any(t in path.lower() for t in terms):
            return f"path match: {_highlight_terms(path, terms)}"
        snippet = next((ln.strip() for ln in lines if ln.strip()), "")
        if not snippet:
            return None
        return f"line 1: {snippet[:max_chars]}"


def _highlight_terms(text: str, terms: list[str]) -> str:
    highlighter = _build_highlighter(frozenset(t for t in terms if t))
    return text if highlighter is None else highlighter(text)


@lru_cache(maxsize=1024)
def _build_highlighter(terms: frozenset[str]) -> Callable[[str], str] | None:
    """Case-insensitive highlighter using str.find/slice — faster than re.sub for [a-z0-9] terms."""
    if not terms:
        return None
    # Sort longest first so overlapping terms match correctly.
    sorted_terms = sorted(terms, key=len, reverse=True)
    open_tag = "[["
    close_tag = "]]"

    def highlight(text: str) -> str:
        result = text
        for term in sorted_terms:
            term_lower = term  # terms from _tokenize_terms are already lowercase
            tlen = len(term)
            out: list[str] = []
            lo = result.lower()
            pos = 0
            while True:
                idx = lo.find(term_lower, pos)
                if idx == -1:
                    out.append(result[pos:])
                    break
                out.append(result[pos:idx])
                out.append(open_tag)
                out.append(result[idx: idx + tlen])
                out.append(close_tag)
                pos = idx + tlen
                lo = lo  # reuse, positions stay valid
            result = "".join(out)
        return result

    return highlight
