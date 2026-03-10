from __future__ import annotations

import argparse
import json
import signal
import subprocess
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Any
from urllib.parse import parse_qs, urlparse

from .ask import answer_question
from .fs_tree import build_snapshot
from .indexer import IndexStore
from .ramdisk import RamDiskManager


@dataclass(slots=True)
class AppContext:
    root_path: Path | None = None
    ramdisk_manager: RamDiskManager = field(default_factory=RamDiskManager)
    destroy_on_exit: bool = False
    index_refresh_seconds: float = 2.0
    index_store: IndexStore = field(default_factory=IndexStore)
    lock: RLock = field(default_factory=RLock)
    stop_event: Event = field(default_factory=Event)
    refresh_thread: Thread | None = None

    def current_root(self) -> Path:
        if self.ramdisk_manager.current is not None:
            return Path(self.ramdisk_manager.current.mount_point)
        if self.root_path is None:
            raise RuntimeError("No root path configured and no RAM disk mounted")
        return self.root_path.resolve()

    def resolve_requested_path(self, requested: str | None) -> Path:
        base = self.current_root().resolve()
        if not requested or requested == ".":
            return base
        candidate = (base / requested).resolve()
        candidate.relative_to(base)
        return candidate

    def health_payload(self) -> dict[str, Any]:
        root = None
        try:
            root = str(self.current_root())
        except RuntimeError:
            root = None
        ramdisk = None if self.ramdisk_manager.current is None else self.ramdisk_manager.current.to_dict()
        return {
            "status": "ok",
            "root": root,
            "ramdisk": ramdisk,
            "index": self.index_store.stats(),
        }

    def rebuild_index(self) -> dict[str, object]:
        return self.index_store.rebuild(self.current_root(), self.ramdisk_manager.current)

    def rebuild_index_if_possible(self) -> dict[str, object] | None:
        try:
            return self.rebuild_index()
        except (FileNotFoundError, RuntimeError):
            self.index_store.clear()
            return None

    def start_indexing(self) -> None:
        with self.lock:
            self.rebuild_index_if_possible()
        if self.index_refresh_seconds <= 0:
            return
        self.stop_event.clear()
        self.refresh_thread = Thread(target=self._refresh_loop, name="sqlfs-index-refresh", daemon=True)
        self.refresh_thread.start()

    def stop_indexing(self) -> None:
        self.stop_event.set()
        if self.refresh_thread is not None:
            self.refresh_thread.join(timeout=max(self.index_refresh_seconds, 1.0) + 0.5)
            self.refresh_thread = None

    def _refresh_loop(self) -> None:
        while not self.stop_event.wait(self.index_refresh_seconds):
            with self.lock:
                self.rebuild_index_if_possible()


def make_handler(context: AppContext) -> type[BaseHTTPRequestHandler]:
    class RequestHandler(BaseHTTPRequestHandler):
        server_version = "SQLFSRamDisk/0.1"

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _handle_snapshot(self, tree_only: bool = False, models_only: bool = False) -> None:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            requested = params.get("path", [None])[0]
            with context.lock:
                target = context.resolve_requested_path(requested)
                snapshot = build_snapshot(target, context.ramdisk_manager.current)
            payload = snapshot.to_dict()
            if tree_only:
                payload = {"summary": payload["summary"], "tree": payload["tree"]}
            elif models_only:
                payload = {"summary": payload["summary"], "models": payload["models"]}
            self._send_json(200, payload)

        def _handle_index_search(self) -> None:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            query = params.get("q", [""])[0]
            content_query = params.get("content", [""])[0]
            entry_type = params.get("type", [None])[0]
            suffix = params.get("suffix", [None])[0]
            path_prefix = params.get("path_prefix", [None])[0]
            limit = int(params.get("limit", ["50"])[0])
            with context.lock:
                matches = context.index_store.search_with_scores(
                    query,
                    content_query=content_query,
                    entry_type=entry_type,
                    suffix=suffix,
                    path_prefix=path_prefix,
                    limit=limit,
                )
                stats = context.index_store.stats()
            self._send_json(
                200,
                {
                    "query": {
                        "q": query,
                        "content": content_query,
                        "type": entry_type,
                        "suffix": suffix,
                        "path_prefix": path_prefix,
                        "limit": limit,
                    },
                    "count": len(matches),
                    "ranking": "bm25",
                    "stats": stats,
                    "matches": [
                        {"score": round(score, 6), "entry": item.to_dict()}
                        for item, score in matches
                    ],
                },
            )

        def _handle_index_file(self) -> None:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            requested_path = params.get("path", [None])[0]
            with context.lock:
                entry = context.index_store.get_by_path(str(requested_path))
                self._send_json(
                    200,
                    {
                        "path": entry.path,
                        "content_indexed": entry.path in context.index_store.content_indexed_paths,
                        "entry": entry.to_dict(),
                    },
                )

        def _handle_index_children(self) -> None:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            requested_path = params.get("path", ["."])[0]
            with context.lock:
                parent = context.index_store.get_by_path(requested_path)
                children = context.index_store.get_children(requested_path)
                self._send_json(
                    200,
                    {
                        "path": parent.path,
                        "entry": parent.to_dict(),
                        "count": len(children),
                        "children": [child.to_dict() for child in children],
                    },
                )

        def _handle_index_symbols(self) -> None:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            name = params.get("name", [""])[0]
            kind = params.get("kind", [None])[0]
            path_prefix = params.get("path_prefix", [None])[0]
            limit = int(params.get("limit", ["20"])[0])
            with context.lock:
                matches = context.index_store.search_symbols(name, kind=kind, path_prefix=path_prefix, limit=limit)
            self._send_json(
                200,
                {
                    "query": {"name": name, "kind": kind, "path_prefix": path_prefix, "limit": limit},
                    "count": len(matches),
                    "matches": [
                        {
                            **symbol.to_dict(),
                            "excerpt": context.index_store.get_symbol_excerpt(symbol, name or symbol.name),
                        }
                        for symbol in matches
                    ],
                },
            )

        def _handle_index_usages(self) -> None:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            name = params.get("name", [""])[0]
            path_prefix = params.get("path_prefix", [None])[0]
            limit = int(params.get("limit", ["20"])[0])
            with context.lock:
                matches = context.index_store.find_symbol_usages(name, path_prefix=path_prefix, limit=limit)
            self._send_json(
                200,
                {
                    "query": {"name": name, "path_prefix": path_prefix, "limit": limit},
                    "count": len(matches),
                    "matches": matches,
                },
            )

        def _handle_ask_get(self) -> None:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            question = params.get("q", [""])[0]
            with context.lock:
                payload = answer_question(question, context.index_store)
            self._send_json(200, payload)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_json(
                    200,
                    {
                        "service": "sqlfs-ramdisk-server",
                        "endpoints": [
                            "GET /health",
                            "GET /fs/models?path=.",
                            "GET /fs/tree?path=.",
                            "GET /fs/snapshot?path=.",
                            "GET /index/stats",
                            "GET /index/file?path=...",
                            "GET /index/children?path=.",
                            "GET /index/search?q=...&content=...",
                            "GET /index/symbols?name=...&kind=function",
                            "GET /index/usages?name=...",
                            "GET /ask?q=где+лежит+readme",
                            "POST /ramdisk/create",
                            "POST /index/rebuild",
                            "POST /ask",
                            "POST /ramdisk/destroy",
                        ],
                    },
                )
                return
            try:
                if parsed.path == "/health":
                    self._send_json(200, context.health_payload())
                elif parsed.path == "/fs/models":
                    self._handle_snapshot(models_only=True)
                elif parsed.path == "/fs/tree":
                    self._handle_snapshot(tree_only=True)
                elif parsed.path == "/fs/snapshot":
                    self._handle_snapshot()
                elif parsed.path == "/index/stats":
                    with context.lock:
                        self._send_json(200, {"index": context.index_store.stats()})
                elif parsed.path == "/index/file":
                    self._handle_index_file()
                elif parsed.path == "/index/children":
                    self._handle_index_children()
                elif parsed.path == "/index/search":
                    self._handle_index_search()
                elif parsed.path == "/index/symbols":
                    self._handle_index_symbols()
                elif parsed.path == "/index/usages":
                    self._handle_index_usages()
                elif parsed.path == "/ask":
                    self._handle_ask_get()
                else:
                    self._send_json(404, {"error": f"Unknown endpoint: {parsed.path}"})
            except FileNotFoundError as exc:
                self._send_json(404, {"error": str(exc)})
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
            except RuntimeError as exc:
                self._send_json(409, {"error": str(exc)})
            except Exception as exc:  # pragma: no cover
                self._send_json(500, {"error": str(exc)})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            try:
                payload = self._read_json()
                with context.lock:
                    if parsed.path == "/ramdisk/create":
                        info = context.ramdisk_manager.create(
                            size_mb=int(payload.get("size_mb", 256)),
                            label=str(payload.get("label", "SQLFSRAM")),
                            fs_type=str(payload.get("fs_type", "HFS+")),
                        )
                        context.rebuild_index_if_possible()
                        self._send_json(201, {"ramdisk": info.to_dict()})
                    elif parsed.path == "/index/rebuild":
                        stats = context.rebuild_index()
                        self._send_json(200, {"index": stats})
                    elif parsed.path == "/ask":
                        question = str(payload.get("question") or payload.get("q") or "")
                        response = answer_question(question, context.index_store)
                        self._send_json(200, response)
                    elif parsed.path == "/ramdisk/destroy":
                        context.ramdisk_manager.destroy()
                        context.rebuild_index_if_possible()
                        self._send_json(200, {"status": "destroyed"})
                    else:
                        self._send_json(404, {"error": f"Unknown endpoint: {parsed.path}"})
            except subprocess.CalledProcessError as exc:
                self._send_json(500, {"error": exc.stderr or str(exc)})
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
            except RuntimeError as exc:
                self._send_json(409, {"error": str(exc)})
            except Exception as exc:  # pragma: no cover
                self._send_json(500, {"error": str(exc)})

        def log_message(self, format: str, *args: Any) -> None:
            return

    return RequestHandler


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RAM disk filesystem model server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--root", type=Path, default=None, help="Existing path to expose")
    parser.add_argument("--create-ramdisk", action="store_true", help="Create a RAM disk on startup")
    parser.add_argument("--size-mb", type=int, default=256)
    parser.add_argument("--label", default="SQLFSRAM")
    parser.add_argument("--fs-type", default="HFS+")
    parser.add_argument("--destroy-on-exit", action="store_true")
    parser.add_argument("--index-refresh-seconds", type=float, default=2.0)
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    context = AppContext(
        root_path=args.root,
        destroy_on_exit=args.destroy_on_exit,
        index_refresh_seconds=args.index_refresh_seconds,
    )
    if args.create_ramdisk:
        context.ramdisk_manager.create(size_mb=args.size_mb, label=args.label, fs_type=args.fs_type)

    context.start_indexing()

    server = ThreadingHTTPServer((args.host, args.port), make_handler(context))

    def shutdown_handler(*_: object) -> None:
        server.shutdown()

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    try:
        print(f"Serving on http://{args.host}:{args.port}")
        server.serve_forever()
    finally:
        server.server_close()
        context.stop_indexing()
        if context.destroy_on_exit:
            context.ramdisk_manager.destroy()

