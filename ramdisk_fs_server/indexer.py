from __future__ import annotations

import math
import re
import time
from collections.abc import Callable
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .fs_tree import build_snapshot
from .models import FsEntryModel, FsSnapshot, PythonSymbol, RamDiskInfo
from .python_symbols import extract_python_symbols

TOKEN_RE = re.compile(r"[a-z0-9]+")
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
DEFAULT_IGNORED_NAMES = frozenset({"__pycache__", ".git", "node_modules", ".venv"})
MAX_TEXT_FILE_SIZE = 1024 * 1024
BM25_K1 = 1.5
BM25_B = 0.75
HIGHLIGHT_TEMPLATE = "[[{term}]]"


def _tokenize(value: str) -> set[str]:
    return set(TOKEN_RE.findall(value.lower()))


def _tokenize_terms(value: str) -> list[str]:
    return TOKEN_RE.findall(value.lower())


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
    file_signature_cache: dict[str, tuple[int, int, float]] = field(default_factory=dict)
    content_terms_cache: dict[str, list[str]] = field(default_factory=dict)
    python_symbols: list[PythonSymbol] = field(default_factory=list)
    symbols_by_path: dict[str, list[PythonSymbol]] = field(default_factory=dict)
    symbol_index: dict[str, list[PythonSymbol]] = field(default_factory=dict)
    qualname_index: dict[str, list[PythonSymbol]] = field(default_factory=dict)
    symbol_definition_paths: dict[str, set[str]] = field(default_factory=dict)
    symbol_reference_index: dict[str, Counter[str]] = field(default_factory=dict)
    test_symbols: list[PythonSymbol] = field(default_factory=list)
    python_symbol_cache: dict[str, tuple[tuple[int, int, float], list[PythonSymbol], Counter[str]]] = field(default_factory=dict)
    bm25_term_frequencies: dict[str, Counter[str]] = field(default_factory=dict)
    bm25_document_frequencies: Counter[str] = field(default_factory=Counter)
    bm25_document_lengths: dict[str, int] = field(default_factory=dict)
    bm25_average_document_length: float = 0.0
    bm25_ready: bool = False

    def clear(self, *, preserve_file_caches: bool = False) -> None:
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
        self._warm_line_cache()

        return self.stats()

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
            "ignored_names": sorted(self.ignored_names),
        }

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
            self.file_signature_cache.clear()
            self.content_terms_cache.clear()
            self.python_symbol_cache.clear()
            return
        active_paths = {model.path for model in models if model.entry_type == "file"}
        for cache in (
            self.file_text_cache,
            self.file_lines_cache,
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
        text = self._read_cached_text(root_path, path, signature)
        if text is None:
            return None
        cached = text.splitlines() or [text]
        self.file_lines_cache[path] = cached
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
            if not candidates:
                candidates = [
                    symbol
                    for symbol in self.python_symbols
                    if normalized in symbol.name.lower() or normalized in symbol.qualname.lower()
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
        if lines is None:
            return None
        start = max(symbol.line - 1, 0)
        end = min(len(lines), max(symbol.end_line, symbol.line) + max_lines - 1)
        query_terms = _tokenize_terms(query)
        highlighter = _build_highlighter(query_terms)
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

        scored = [(path, self._bm25_score(path, bm25_tokens)) for path in candidates]
        scored.sort(key=lambda item: (-item[1], item[0].lower()))
        return [(self.by_path[path], score) for path, score in scored[: max(limit, 0)]]

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
        if model.entry_type != "file" or (model.suffix or "").lower() != ".py":
            return
        file_path = root_path / model.path if model.path != "." else root_path
        signature = self._model_signature(model)
        cached = self.python_symbol_cache.get(model.path)
        if cached is not None and cached[0] == signature:
            symbols, references = cached[1], cached[2]
        else:
            source = self._read_cached_text(root_path, model.path, signature)
            symbols, references = extract_python_symbols(file_path, model.path, source=source)
            self.python_symbol_cache[model.path] = (signature, symbols, references)
        if not symbols and not references:
            return
        self.python_symbols.extend(symbols)
        self.symbols_by_path[model.path] = symbols
        for symbol in symbols:
            self.symbol_index.setdefault(symbol.name.lower(), []).append(symbol)
            self.qualname_index.setdefault(symbol.qualname.lower(), []).append(symbol)
            if symbol.kind in {"class", "function", "method"}:
                self.symbol_definition_paths.setdefault(symbol.name.lower(), set()).add(symbol.path)
                self.symbol_definition_paths.setdefault(symbol.qualname.lower(), set()).add(symbol.path)
            if symbol.is_test:
                self.test_symbols.append(symbol)
        for reference_name, count in references.items():
            self.symbol_reference_index.setdefault(reference_name, Counter())[model.path] = count

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

    def _document_terms(self, model: FsEntryModel, *, root_path: Path | None = None) -> list[str]:
        terms: list[str] = []
        metadata_terms = _tokenize_terms(f"{model.name} {model.path}")
        terms.extend(metadata_terms)
        terms.extend(metadata_terms)
        if model.path in self.content_indexed_paths and root_path is not None:
            content_terms = self._get_cached_content_terms(root_path, model)
            if content_terms is not None:
                terms.extend(content_terms)
        return terms

    def _bm25_score(self, path: str, query_terms: list[str]) -> float:
        if not self.bm25_ready or not query_terms:
            return 0.0
        term_freq = self.bm25_term_frequencies.get(path)
        if not term_freq:
            return 0.0
        doc_length = self.bm25_document_lengths.get(path, 0)
        if doc_length == 0 or self.bm25_average_document_length == 0:
            return 0.0
        score = 0.0
        doc_count = len(self.bm25_term_frequencies)
        for token in query_terms:
            frequency = term_freq.get(token, 0)
            if frequency == 0:
                continue
            doc_frequency = self.bm25_document_frequencies.get(token, 0)
            numerator = doc_count - doc_frequency + 0.5
            denominator = doc_frequency + 0.5
            idf = math.log(1 + (numerator / denominator))
            norm = BM25_K1 * (1 - BM25_B + BM25_B * (doc_length / self.bm25_average_document_length))
            score += idf * ((frequency * (BM25_K1 + 1)) / (frequency + norm))
        return score

    def get_excerpt(self, path: str, query: str, *, max_chars: int = 220) -> str | None:
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
        if lines is None:
            return None
        best_index = 0
        best_score = -1
        if terms:
            for index, line in enumerate(lines):
                lowered = line.lower()
                score = sum(lowered.count(term) for term in terms)
                if score > best_score:
                    best_score = score
                    best_index = index
        else:
            for index, line in enumerate(lines):
                if line.strip():
                    best_index = index
                    break
        if terms and best_score <= 0:
            if any(term in path.lower() for term in terms):
                return f"path match: {_highlight_terms(path, terms)}"
        start = max(0, best_index - 1)
        end = min(len(lines), best_index + 2)
        highlighter = _build_highlighter(terms)
        excerpt_lines: list[str] = []
        for index in range(start, end):
            snippet = lines[index].strip()
            if not snippet:
                continue
            if highlighter is not None:
                snippet = highlighter(snippet)
            if len(snippet) > max_chars:
                snippet = snippet[: max_chars - 1] + "…"
            excerpt_lines.append(f"line {index + 1}: {snippet}")
        if excerpt_lines:
            return "\n".join(excerpt_lines)
        if terms and any(term in path.lower() for term in terms):
            return f"path match: {_highlight_terms(path, terms)}"
        snippet = next((line.strip() for line in lines if line.strip()), "")
        if not snippet:
            return None
        if len(snippet) > max_chars:
            snippet = snippet[: max_chars - 1] + "…"
        return f"line 1: {snippet}"


def _highlight_terms(text: str, terms: list[str]) -> str:
    highlighter = _build_highlighter(terms)
    return text if highlighter is None else highlighter(text)


def _build_highlighter(terms: list[str]) -> Callable[[str], str] | None:
    unique_terms = sorted({term for term in terms if term}, key=len, reverse=True)
    if not unique_terms:
        return None
    patterns = [re.compile(re.escape(term), re.IGNORECASE) for term in unique_terms]

    def highlight(text: str) -> str:
        highlighted = text
        for pattern in patterns:
            highlighted = pattern.sub(lambda match: HIGHLIGHT_TEMPLATE.format(term=match.group(0)), highlighted)
        return highlighted

    return highlight
