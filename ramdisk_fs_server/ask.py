from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .indexer import IndexStore

WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
PATH_WORD_RE = re.compile(r"[A-Za-z0-9_./-]+")
STOPWORDS = {
    "a",
    "all",
    "and",
    "are",
    "does",
    "find",
    "for",
    "from",
    "get",
    "function",
    "in",
    "is",
    "lay",
    "lies",
    "list",
    "located",
    "me",
    "of",
    "on",
    "only",
    "path",
    "paths",
    "class",
    "search",
    "show",
    "the",
    "there",
    "these",
    "tests",
    "test",
    "use",
    "used",
    "uses",
    "where",
    "who",
    "with",
    "в",
    "все",
    "внутри",
    "где",
    "директории",
    "директория",
    "для",
    "есть",
    "и",
    "из",
    "какие",
    "какой",
    "какую",
    "каталог",
    "каталоги",
    "класс",
    "класса",
    "классы",
    "лежит",
    "лежат",
    "ли",
    "мне",
    "найди",
    "найти",
    "находится",
    "находятся",
    "папка",
    "папки",
    "покажи",
    "показать",
    "по",
    "путь",
    "пути",
    "с",
    "содержимом",
    "содержимому",
    "содержимое",
    "список",
    "тест",
    "тесты",
    "текст",
    "тексте",
    "только",
    "использует",
    "используется",
    "используют",
    "метод",
    "методы",
    "функция",
    "функции",
    "файл",
    "файла",
    "файле",
    "файлов",
    "что",
}
FILE_WORDS = {"file", "files", "файл", "файла", "файле", "файлы", "файлов"}
DIRECTORY_WORDS = {"dir", "dirs", "directory", "directories", "folder", "folders", "директория", "директории", "каталог", "каталоги", "папка", "папки"}
CONTENT_WORDS = {"content", "contents", "inside", "text", "содержимое", "содержимому", "содержимом", "внутри", "текст", "тексте"}
LOCATION_WORDS = {"where", "located", "lies", "path", "paths", "где", "лежит", "лежат", "находится", "находятся", "путь", "пути"}
LIST_WORDS = {"all", "list", "show", "все", "список", "покажи", "показать"}
FUNCTION_WORDS = {"function", "functions", "func", "функция", "функции"}
CLASS_WORDS = {"class", "classes", "класс", "классы", "класса"}
METHOD_WORDS = {"method", "methods", "метод", "методы"}
TEST_WORDS = {"test", "tests", "тест", "тесты"}
USAGE_PATTERNS = ("кто использует", "где используется", "where is used", "who uses", "used by", "uses ")
SUFFIX_ALIASES = {
    "markdown": ".md",
    "md": ".md",
    "python": ".py",
    "py": ".py",
    "json": ".json",
    "yaml": ".yaml",
    "yml": ".yml",
    "toml": ".toml",
    "text": ".txt",
    "txt": ".txt",
    "лог": ".log",
    "логов": ".log",
    "логи": ".log",
    "markdown-файл": ".md",
    "маркдаун": ".md",
    "питон": ".py",
    "текст": ".txt",
}


@dataclass(slots=True)
class AskQuery:
    question: str
    intent: str = "search_files"
    subject: str | None = None
    query: str = ""
    content_query: str = ""
    entry_type: str | None = None
    suffix: str | None = None
    symbol_kind: str | None = None
    path_prefix: str | None = None
    limit: int = 5
    wants_location: bool = False
    wants_list: bool = False

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def parse_question(question: str, index_store: IndexStore | None = None) -> AskQuery:
    normalized = question.strip()
    lowered = normalized.lower()
    words = WORD_RE.findall(lowered)
    if not words:
        raise ValueError("question must not be empty")

    entry_type = None
    if any(word in FILE_WORDS for word in words):
        entry_type = "file"
    elif any(word in DIRECTORY_WORDS for word in words):
        entry_type = "directory"

    symbol_kind = None
    if any(word in FUNCTION_WORDS for word in words):
        symbol_kind = "function"
    elif any(word in CLASS_WORDS for word in words):
        symbol_kind = "class"
    elif any(word in METHOD_WORDS for word in words):
        symbol_kind = "method"

    content_mode = any(word in CONTENT_WORDS for word in words) or "по содержим" in lowered
    wants_location = any(word in LOCATION_WORDS for word in words)
    wants_list = any(word in LIST_WORDS for word in words)
    path_prefix = _infer_path_prefix(lowered, index_store)
    path_prefix_tokens = set(WORD_RE.findall(path_prefix.lower())) if path_prefix else set()
    subject = _infer_subject(normalized, path_prefix)

    intent = "search_files"
    if any(pattern in lowered for pattern in USAGE_PATTERNS) and subject:
        intent = "find_usage"
    elif any(word in TEST_WORDS for word in words) and subject:
        intent = "find_tests"
    elif symbol_kind is not None and subject:
        intent = "find_symbol"

    suffix = None
    for word in words:
        if word in SUFFIX_ALIASES:
            suffix = SUFFIX_ALIASES[word]
            break

    reserved = (
        STOPWORDS
        | FILE_WORDS
        | DIRECTORY_WORDS
        | CONTENT_WORDS
        | LOCATION_WORDS
        | LIST_WORDS
        | FUNCTION_WORDS
        | CLASS_WORDS
        | METHOD_WORDS
        | TEST_WORDS
        | set(SUFFIX_ALIASES)
    )
    terms = [word for word in words if word not in reserved and word not in path_prefix_tokens]
    payload = " ".join(terms)
    return AskQuery(
        question=normalized,
        intent=intent,
        subject=subject,
        query="" if content_mode else payload,
        content_query=payload if content_mode else "",
        entry_type=entry_type,
        suffix=suffix,
        symbol_kind=symbol_kind,
        path_prefix=path_prefix,
        limit=10 if wants_list else 5,
        wants_location=wants_location,
        wants_list=wants_list,
    )


def answer_question(question: str, index_store: IndexStore) -> dict[str, object]:
    ask_query = parse_question(question, index_store=index_store)
    if ask_query.intent == "find_symbol" and ask_query.subject:
        symbols = index_store.search_symbols(
            ask_query.subject,
            kind=ask_query.symbol_kind,
            path_prefix=ask_query.path_prefix,
            limit=ask_query.limit,
        )
        if not symbols and ask_query.symbol_kind is not None:
            symbols = index_store.search_symbols(
                ask_query.subject,
                path_prefix=ask_query.path_prefix,
                limit=ask_query.limit,
            )
        return _symbol_answer(ask_query, symbols, index_store)
    if ask_query.intent == "find_usage" and ask_query.subject:
        usages = index_store.find_symbol_usages(
            ask_query.subject,
            path_prefix=ask_query.path_prefix,
            limit=ask_query.limit,
        )
        return _usage_answer(ask_query, usages)
    if ask_query.intent == "find_tests" and ask_query.subject:
        tests = index_store.find_related_tests(
            ask_query.subject,
            path_prefix=ask_query.path_prefix,
            limit=ask_query.limit,
        )
        return _test_answer(ask_query, tests, index_store)

    matches = index_store.search_with_scores(
        ask_query.query,
        content_query=ask_query.content_query,
        entry_type=ask_query.entry_type,
        suffix=ask_query.suffix,
        path_prefix=ask_query.path_prefix,
        limit=ask_query.limit,
    )
    if ask_query.entry_type == "directory" and not ask_query.query and not ask_query.content_query:
        matches = [(model, score) for model, score in matches if model.path != "."]
    files = [model.path for model, _ in matches]
    evidence = []
    query_terms = f"{ask_query.query} {ask_query.content_query}".strip()
    for model, score in matches[:5]:
        evidence.append(
            {
                "path": model.path,
                "parent_path": "." if model.path == "." or "/" not in model.path else model.path.rsplit("/", 1)[0],
                "entry_type": model.entry_type,
                "score": round(score, 6),
                "excerpt": index_store.get_excerpt(model.path, query_terms),
            }
        )
    return {
        "question": ask_query.question,
        "parsed_query": ask_query.to_dict(),
        "answer": _answer_text(ask_query, files),
        "files": files,
        "matches": evidence,
    }


def _symbol_answer(ask_query: AskQuery, symbols: list[object], index_store: IndexStore) -> dict[str, object]:
    files = list(dict.fromkeys(symbol.path for symbol in symbols))
    matches = [
        {
            "path": symbol.path,
            "name": symbol.name,
            "qualname": symbol.qualname,
            "kind": symbol.kind,
            "line": symbol.line,
            "end_line": symbol.end_line,
            "excerpt": index_store.get_symbol_excerpt(symbol, ask_query.subject or symbol.name),
        }
        for symbol in symbols
    ]
    kind_label = ask_query.symbol_kind or "symbol"
    if not symbols:
        answer = f"Не нашёл {kind_label}: {ask_query.subject}"
    elif len(symbols) == 1:
        symbol = symbols[0]
        answer = f"{kind_label.capitalize()} {symbol.qualname} определён в {symbol.path}:{symbol.line}"
    else:
        answer = f"Нашёл {len(symbols)} определений для {ask_query.subject}: {', '.join(files[:3])}"
    return {
        "question": ask_query.question,
        "parsed_query": ask_query.to_dict(),
        "answer": answer,
        "files": files,
        "matches": matches,
    }


def _usage_answer(ask_query: AskQuery, usages: list[dict[str, object]]) -> dict[str, object]:
    files = [str(item["path"]) for item in usages]
    if not usages:
        answer = f"Не нашёл usages для {ask_query.subject}"
    elif len(usages) == 1:
        answer = f"{ask_query.subject} используется в {files[0]}"
    else:
        answer = f"{ask_query.subject} используется в {len(usages)} файлах: {', '.join(files[:3])}"
    return {
        "question": ask_query.question,
        "parsed_query": ask_query.to_dict(),
        "answer": answer,
        "files": files,
        "matches": usages,
    }


def _test_answer(ask_query: AskQuery, tests: list[object], index_store: IndexStore) -> dict[str, object]:
    files = list(dict.fromkeys(symbol.path for symbol in tests))
    matches = [
        {
            "path": symbol.path,
            "name": symbol.name,
            "qualname": symbol.qualname,
            "kind": symbol.kind,
            "line": symbol.line,
            "excerpt": index_store.get_symbol_excerpt(symbol, ask_query.subject or symbol.name),
        }
        for symbol in tests
    ]
    if not tests:
        answer = f"Не нашёл тесты для {ask_query.subject}"
    elif len(tests) == 1:
        answer = f"Нашёл тест для {ask_query.subject}: {tests[0].qualname} в {tests[0].path}:{tests[0].line}"
    else:
        answer = f"Нашёл {len(tests)} тестов для {ask_query.subject}: {', '.join(files[:3])}"
    return {
        "question": ask_query.question,
        "parsed_query": ask_query.to_dict(),
        "answer": answer,
        "files": files,
        "matches": matches,
    }


def _answer_text(ask_query: AskQuery, files: list[str]) -> str:
    if not files:
        return f"Ничего не нашёл по запросу: {ask_query.question}"
    noun = "директорий" if ask_query.entry_type == "directory" else "файлов"
    location_suffix = f" внутри {ask_query.path_prefix}" if ask_query.path_prefix else ""
    if ask_query.wants_location:
        if len(files) == 1:
            return f"Самый релевантный путь{location_suffix}: {files[0]}"
        return f"Нашёл {len(files)} {noun}{location_suffix}. Самые релевантные пути: {', '.join(files[:3])}"
    if len(files) == 1:
        return f"Нашёл 1 результат{location_suffix}: {files[0]}"
    return f"Нашёл {len(files)} {noun}{location_suffix}: {', '.join(files[:3])}"


def _infer_path_prefix(question: str, index_store: IndexStore | None) -> str | None:
    candidates = _extract_path_prefix_candidates(question)
    if not candidates:
        return None
    if index_store is None:
        return candidates[0]
    directory_paths = index_store.type_index.get("directory", set())
    for candidate in candidates:
        normalized = _normalize_path_candidate(candidate)
        if not normalized:
            continue
        if normalized in directory_paths:
            return normalized
        basename_matches = [path for path in directory_paths if path != "." and Path(path).name.lower() == normalized.lower()]
        if len(basename_matches) == 1:
            return basename_matches[0]
    return None


def _extract_path_prefix_candidates(question: str) -> list[str]:
    patterns = [
        r"path_prefix\s*[:=]\s*([A-Za-z0-9_./-]+)",
        r"(?:inside|under|within)\s+([A-Za-z0-9_./-]+)",
        r"(?:in\s+(?:folder|directory))\s+([A-Za-z0-9_./-]+)",
        r"(?:внутри|в папке|в директории|в каталоге|из папки|из директории|из каталога)\s+([A-Za-z0-9_./-]+)",
        r"\bв\s+([A-Za-z0-9_./-]+)\s*$",
    ]
    candidates: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, question):
            candidate = _normalize_path_candidate(match.group(1))
            if candidate and candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _normalize_path_candidate(value: str) -> str | None:
    match = PATH_WORD_RE.search(value.strip())
    if match is None:
        return None
    normalized = match.group(0).strip().strip("/")
    return normalized or None


def _infer_subject(question: str, path_prefix: str | None) -> str | None:
    identifiers = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", question)
    path_tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", path_prefix or ""))
    reserved = (
        STOPWORDS
        | FILE_WORDS
        | DIRECTORY_WORDS
        | CONTENT_WORDS
        | LOCATION_WORDS
        | LIST_WORDS
        | FUNCTION_WORDS
        | CLASS_WORDS
        | METHOD_WORDS
        | TEST_WORDS
    )
    candidates = [identifier for identifier in identifiers if identifier.lower() not in reserved and identifier not in path_tokens]
    return candidates[-1] if candidates else None