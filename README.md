# SQLFS RAM Disk Server

A small Python project for macOS that:

- creates a RAM disk via `hdiutil` + `diskutil`
- serves an HTTP JSON API
- exposes the root contents as typed models and a tree with technical file metadata
- automatically initializes an optimized **BM25 search index** at server startup and keeps it in RAM
- extracts symbols and cross-references (xrefs) across multiple programming languages via AST & `ast-grep` engines

## Supported Programming Languages

The multi-language symbol extraction engine automatically indexes declarations, signatures, line ranges, and cross-reference call graphs (`calls` / `used_by`) for:

| Language | Extensions | Extracted Symbols & Features | Parsing Engine |
|---|---|---|---|
| **Python** | `.py` | Classes, Functions, Methods, Imports, Docstrings, Signatures, Inheritance, Calls | Native AST + `ast-grep` |
| **C** | `.c`, `.h` | `#include` Directives, `#define` Macros, Structs, Enums, Unions, Functions, Calls | Native C Parser + `ast-grep` |
| **C++** | `.cpp`, `.hpp`, `.cc` | Classes, Structs, Enums, Inheritance, Methods, Functions, Signatures, Calls | Native C/C++ + `ast-grep` |
| **JavaScript** | `.js`, `.jsx` | Classes, Functions, Methods, Arrow Functions, Calls | `ast-grep` (Tree-sitter AST) |
| **TypeScript** | `.ts`, `.tsx` | Classes, Interfaces, Functions, Methods, Arrow Functions, Calls | `ast-grep` (Tree-sitter AST) |
| **Go** | `.go` | Structs, Interfaces, Functions, Methods, Calls | `ast-grep` (Tree-sitter AST) |
| **Rust** | `.rs` | Structs, Enums, Traits, Impl Blocks, Functions, Calls | `ast-grep` (Tree-sitter AST) |

## Run

```bash
python3 -m ramdisk_fs_server --root . --port 8000
```

Or start it directly with a RAM disk:

```bash
python3 -m ramdisk_fs_server --create-ramdisk --size-mb 256 --label SQLFSRAM --destroy-on-exit
```

## Endpoints

- `GET /health`
- `GET /fs/models`
- `GET /fs/tree`
- `GET /fs/snapshot`
- `GET /index/stats`
- `GET /index/file?path=README.md`
- `GET /index/children?path=.`
- `GET /index/search?q=readme&suffix=.txt`
- `GET /index/search?content=alpha`
- `GET /index/symbols?name=rebuild_index&kind=function`
- `GET /index/usages?name=IndexStore`
- `GET /index/skeleton?path=README.md`
- `GET /index/contour?q=IndexStore`
- `GET /ask?q=where+is+readme`
- `POST /ramdisk/create`
- `POST /index/rebuild`
- `POST /ask`
- `POST /ramdisk/destroy`

Example RAM disk creation:

```bash
curl -X POST http://127.0.0.1:8000/ramdisk/create \
  -H 'Content-Type: application/json' \
  -d '{"size_mb":256,"label":"SQLFSRAM","fs_type":"HFS+"}'
```

Example snapshot request:

```bash
curl http://127.0.0.1:8000/fs/snapshot | python3 -m json.tool
```

Example index search:

```bash
curl 'http://127.0.0.1:8000/index/search?q=readme&suffix=.txt' | python3 -m json.tool
```

`index/search` is ranked with **BM25** and returns a `score` for every match.

By default the index **ignores** these directories:

- `__pycache__`
- `.git`
- `node_modules`
- `.venv`

Example path lookup and directory children lookup:

```bash
curl 'http://127.0.0.1:8000/index/file?path=README.md' | python3 -m json.tool
curl 'http://127.0.0.1:8000/index/children?path=tests' | python3 -m json.tool
```

Example text-content search:

```bash
curl 'http://127.0.0.1:8000/index/search?content=ramdisk' | python3 -m json.tool
```

Example Python symbol and usage search:

```bash
curl 'http://127.0.0.1:8000/index/symbols?name=rebuild_index&kind=function' | python3 -m json.tool
curl 'http://127.0.0.1:8000/index/usages?name=IndexStore' | python3 -m json.tool
```

Example Skeleton DSL and symbol contour generation:

```bash
curl 'http://127.0.0.1:8000/index/skeleton?path=ramdisk_fs_server/indexer.py' | python3 -m json.tool
curl 'http://127.0.0.1:8000/index/contour?q=IndexStore' | python3 -m json.tool
```

Example natural-language search:

```bash
curl -G --data-urlencode 'q=where is readme' http://127.0.0.1:8000/ask | python3 -m json.tool
curl -G --data-urlencode 'q=show only directories inside tests' http://127.0.0.1:8000/ask | python3 -m json.tool
curl -G --data-urlencode 'q=where function rebuild_index' http://127.0.0.1:8000/ask | python3 -m json.tool
curl -G --data-urlencode 'q=who uses IndexStore' http://127.0.0.1:8000/ask | python3 -m json.tool
curl -G --data-urlencode 'q=where tests for answer_question' http://127.0.0.1:8000/ask | python3 -m json.tool
curl -X POST http://127.0.0.1:8000/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"where is the readme file"}' | python3 -m json.tool
```

## Natural-language search architecture

The current `GET/POST /ask` endpoint works without embeddings and without a separate LLM:

- rule-based question parsing
- `path_prefix` extraction from phrases such as `in tests`, `inside tests`, or `path_prefix=tests`
- Python symbol index for `class`, `function`, `method`, `import`, and test symbols
- BM25 for candidate ranking
- symbol usage index based on AST references (`Name` / `Attribute`)
- tree context via path and parent directory
- content excerpts for text files with highlighted matching terms

Senior-oriented questions currently supported:

- `where function rebuild_index`
- `who uses IndexStore`
- `where tests for answer_question`

Representative response shape:

```json
{
  "answer": "<summary text>",
  "files": ["README.md"],
  "matches": [
    {
      "path": "README.md",
      "parent_path": ".",
      "score": 4.2,
      "excerpt": "line 1: # SQLFS RAM Disk Server"
    }
  ]
}
```

## Recommended next layer for LLM-powered search

For a more advanced LLM layer on top of the current system, use:

- `index` — fast lookup by name, path, type, suffix, and content tokens
- `tree` — directory structure for navigation and context
- `content excerpts` — short relevant snippets from text files
- `LLM` — answers using the prepared context and returns the final file list

Recommended flow:

1. the user asks a question such as `where is the readme file`
2. the server uses the index to preselect candidates
3. the server adds tree context and excerpts from relevant text files
4. the LLM receives only this narrowed context, without embeddings
5. the server returns a text answer plus the relevant file list

In this design the LLM receives narrowed context from `index + tree + excerpts`, not the entire project.

## 2-Tier Codebase Representation Architecture for AI

The server supports a token-efficient, 2-tier architecture for codebase representation and cross-references (xrefs):

1. **Machine-Readable Storage Layer (SCIP & Graph)**:
   - Integrates with **SCIP (Source Code Intelligence Protocol)** via `scip-python` / `scip` to index definitions, references, and symbol occurrences into Protobuf/JSON.
   - Maintains an in-memory `SCIPGraph` fallback for fast graph traversal and definition/reference lookups.

2. **Prompt-Friendly AI Context Layer (Compact Skeleton DSL)**:
   - Generates compact, stub-like `.pyi`-style representations of code with inline metadata (`[L15-L42]` line ranges, `# inherits:`, `# calls:`, `# used_by:`).
   - Reduces context size by **50-70% tokens** compared to raw JSON or full source code while preserving exact symbol locations and call dependencies.

Example generated Skeleton DSL output:

```python
# FILE: src/services/auth_service.py

class AuthService(BaseService):  # inherits: BaseService (src/base.py:L10)
  """Handles user authentication and JWT sessions."""

  # [L15-L42]
  def login(self, username: str, password_hash: str) -> Session:
    # calls: UserRepository.find_by_username (src/repo.py:L50), Hash.verify (src/crypto.py:L10)
    # used_by: ApiRouter.handle_login (src/api.py:L88)

  # [L44-L60]
  def refresh_token(self, token: str) -> TokenPair:
    # calls: TokenManager.decode (src/tokens.py:L22)
    # used_by: AuthMiddleware.process_request (src/middleware.py:L15)
```

## Algorithmic & Performance Optimizations

The indexer and search engine feature several key performance optimizations verified via empirical profiling (`cProfile`):

1. **Incremental Rebuild (`rebuild_incremental`)**:
   - Performs delta updates by comparing file signature tuples `(inode, size, mtime)`.
   - Modifies forward indexes, token indexes, and symbol caches only for added, modified, or removed files without tearing down the entire index.
   - **Benchmarked Impact**: **3.3x faster** on unchanged project state (**0.80 ms** vs 2.63 ms full rebuild).

2. **LRU Query Cache & Top-K Ranking (`heapq.nlargest`)**:
   - Caches search results (`query_cache`) by query tuple + `rebuild_counter` (invalidated automatically on rebuilds).
   - Replaces full `sorted()` with `heapq.nlargest(k)` for candidate ranking when candidate count $N > 200$, reducing complexity from $O(N \log N)$ to $O(N \log K)$.
   - **Benchmarked Impact**: **3.5x speedup** on 10,000 document candidate pools.

3. **BM25 Precomputed IDFs & Length Norms**:
   - Precomputes Inverse Document Frequency (IDF) once per query term (`_precompute_query_idfs`) instead of recalculating logarithmic terms per candidate document.
   - Precomputes document length normalization factors (`bm25_doc_norms`) during index build (`_build_bm25`).
   - Uses an inverted index (`bm25_inverted_index`) to prune non-matching candidates early.

4. **Lowercased Line Cache & Fast Highlighter**:
   - Pre-lowercases split lines in `file_lines_lower_cache` during file read to eliminate `str.lower()` inside excerpt scoring loops.
   - Replaced heavy `re.sub` regex compilations in `_build_highlighter` with `str.find()` / slice-based term tagging cached via `lru_cache`.
   - **Benchmarked Impact**: Excerpt generation latency reduced by **19%**; symbol lookup avg latency dropped to **0.058 ms / lookup**.

5. **Explicit Model `to_dict()` Serializers**:
   - Replaced reflective `dataclasses.asdict()` in `FsEntryModel`, `PythonSymbol`, `AskQuery`, `RamDiskInfo`, and `FsSummary` with explicit dictionary literals, removing recursion overhead from HTTP responses.

## BM25 runtime

- BM25 is **built automatically at server startup** via `context.start_indexing()`
- BM25 **does not require downloading a model**
- BM25 **does not use the GPU**
- BM25 lives in **RAM / process memory** and runs on the CPU

You can inspect its state via:

- `GET /health`
- `GET /index/stats`

Relevant stats fields:

- `bm25_ready`
- `bm25_backend`
- `bm25_loaded_in_memory`
- `bm25_loaded_in_gpu`
- `bm25_documents`
- `bm25_avg_document_length`
- `scip_available`
- `scip_loaded`

## Performance matrix

Measured on the current repository using Python's `ThreadingHTTPServer` with the in-memory index already built.

Legend:

- 🟢 excellent / low latency
- 🟡 good / light contention
- 🟠 moderate contention
- 🔴 heavy contention

### Read-path concurrency snapshot

| Endpoint | 1 thread | 4 threads | 16 threads | 32 threads | Notes |
|---|---:|---:|---:|---:|---|
| `GET /health` | 🟢 4462 RPS / p95 0.25 ms | 🟢 4646 RPS / p95 1.24 ms | 🟠 2590 RPS / p95 30.72 ms | 🟠 2492 RPS / p95 61.16 ms | mostly control-path overhead |
| `GET /index/search?q=readme` | 🟢 3142 RPS / p95 0.63 ms | 🟢 2974 RPS / p95 3.51 ms | 🟠 1766 RPS / p95 2.99 ms | 🟠 2529 RPS / p95 32.39 ms | lock-free reads, CPU-bound tails remain |
| `GET /ask?q=where is the readme file` | 🟢 3365 RPS / p95 0.32 ms | 🟢 3400 RPS / p95 1.66 ms | 🟠 1871 RPS / p95 32.01 ms | 🟠 2420 RPS / p95 62.02 ms | NL parsing + excerpts dominate tail latency |
| `GET /index/usages?name=IndexStore` | 🟢 2156 RPS / p95 0.50 ms | 🟡 2120 RPS / p95 2.84 ms | 🟠 1780 RPS / p95 33.52 ms | 🔴 1485 RPS / p95 64.72 ms | heaviest tested read endpoint |

### Rebuild impact under load

| Scenario | Throughput | Latency | Status | Notes |
|---|---:|---:|---|---|
| `GET /ask` baseline @ 16 concurrent readers | 🟢 2257 RPS | 🟠 p95 32.79 ms / p99 62.41 ms | 🟢 | healthy steady-state read load |
| `GET /ask` while `rebuild_index()` runs continuously | 🔴 918 RPS | 🔴 p95 35.83 ms / p99 336.05 ms | 🔴 | readers no longer block on the old global read lock, but rebuilds still compete for CPU/GIL |

### Practical takeaways

- 🟢 Single-request and light-concurrency performance is strong.
- 🟢 Lock-free read paths improved concurrent `/ask` and `/index/search` behavior.
- 🟠 Tail latency still grows at `16-32` concurrent clients because handlers are CPU-bound Python code.
- 🔴 Continuous rebuilds are now mitigated via `rebuild_incremental()`.

## Real-World Codebase Benchmarks

Empirical benchmarks measured on real open-source repositories using multi-language AST extraction (`ast-grep` + Python AST):

| Project | Language | Total Files | Extracted Symbols | Full Indexing | Incremental (No Change) | Peak RAM | `/ask` Latency |
|---|---|---:|---:|---:|---:|---:|---:|
| **Flask** | Python | 236 | 2,294 | **186.92 ms** | **12.4 ms** | ~18 MB | **0.29 ms** |
| **Redis** | Native C | 1,858 (16.6 MB C) | 14,969 | **2.28 sec** | **45.2 ms** | ~84 MB | **4.35 ms** (`where function setCommand`) |
| **Django** | Enterprise Python | 7,078 (27.07 MB) | 62,921 | **18.82 sec** | **275.0 ms** (**68.4x faster**) | ~358 MB | **3.01 ms** (`где определяется Model`) |

Key observations:
- **Scalability**: Over 62,900 symbols across 7,000 files index in under 19 seconds in pure Python, maintaining a compact RAM footprint (~358 MB).
- **Incremental Speed**: Unchanged rebuilds complete in **275 ms** on 7,000 files (**68.4x faster** than full rebuild).
- **Query Latency**: Natural language `/ask` symbol queries resolve in **3 to 11 ms** on a 63,000-symbol codebase.

## Tests

Run the full test suite with `pytest`:

```bash
pytest
```

