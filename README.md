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

## Tests

```bash
python3 -m unittest discover -s tests -v
```
