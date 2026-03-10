# SQLFS RAM Disk Server

A small Python project for macOS that:

- creates a RAM disk via `hdiutil` + `diskutil`
- serves an HTTP JSON API
- exposes the root contents as typed models and a tree with technical file metadata
- automatically initializes a **BM25 search index** at server startup and keeps it in RAM

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
- 🔴 Continuous rebuilds are now the main remaining performance hazard under load.

## Tests

```bash
python3 -m unittest discover -s tests -v
```
