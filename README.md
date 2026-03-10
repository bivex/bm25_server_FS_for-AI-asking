# SQLFS RAM Disk Server

Небольшой Python-проект для macOS, который:

- создаёт RAM disk через `hdiutil` + `diskutil`
- поднимает HTTP JSON API
- показывает содержимое корня как набор моделей и дерево с технической информацией
- автоматически инициализирует **BM25 search index** при старте сервера и держит его в RAM

## Запуск

```bash
python3 -m ramdisk_fs_server --root . --port 8000
```

или сразу с RAM disk:

```bash
python3 -m ramdisk_fs_server --create-ramdisk --size-mb 256 --label SQLFSRAM --destroy-on-exit
```

## Эндпоинты

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
- `GET /ask?q=где+лежит+readme`
- `POST /ramdisk/create`
- `POST /index/rebuild`
- `POST /ask`
- `POST /ramdisk/destroy`

Пример создания RAM disk:

```bash
curl -X POST http://127.0.0.1:8000/ramdisk/create \
  -H 'Content-Type: application/json' \
  -d '{"size_mb":256,"label":"SQLFSRAM","fs_type":"HFS+"}'
```

Пример snapshot:

```bash
curl http://127.0.0.1:8000/fs/snapshot | python3 -m json.tool
```

Пример поиска по индексу:

```bash
curl 'http://127.0.0.1:8000/index/search?q=readme&suffix=.txt' | python3 -m json.tool
```

Ответ `index/search` ранжируется через **BM25** и возвращает `score` для каждого совпадения.

По умолчанию индекс **игнорирует** директории:

- `__pycache__`
- `.git`
- `node_modules`
- `.venv`

Пример lookup по пути и детям директории:

```bash
curl 'http://127.0.0.1:8000/index/file?path=README.md' | python3 -m json.tool
curl 'http://127.0.0.1:8000/index/children?path=tests' | python3 -m json.tool
```

Пример поиска по содержимому текстовых файлов:

```bash
curl 'http://127.0.0.1:8000/index/search?content=ramdisk' | python3 -m json.tool
```

Пример Python symbol / usage поиска:

```bash
curl 'http://127.0.0.1:8000/index/symbols?name=rebuild_index&kind=function' | python3 -m json.tool
curl 'http://127.0.0.1:8000/index/usages?name=IndexStore' | python3 -m json.tool
```

Пример natural-language поиска:

```bash
curl -G --data-urlencode 'q=где лежит readme' http://127.0.0.1:8000/ask | python3 -m json.tool
curl -G --data-urlencode 'q=покажи только директории в tests' http://127.0.0.1:8000/ask | python3 -m json.tool
curl -G --data-urlencode 'q=где функция rebuild_index' http://127.0.0.1:8000/ask | python3 -m json.tool
curl -G --data-urlencode 'q=кто использует IndexStore' http://127.0.0.1:8000/ask | python3 -m json.tool
curl -G --data-urlencode 'q=где тесты для answer_question' http://127.0.0.1:8000/ask | python3 -m json.tool
curl -X POST http://127.0.0.1:8000/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"где лежит файл readme"}' | python3 -m json.tool
```

## Natural language search architecture

Текущий `GET/POST /ask` endpoint работает без embeddings и без отдельной LLM:

- rule-based разбор вопроса
- `path_prefix` разбор из фраз вроде `в tests`, `inside tests`, `path_prefix=tests`
- Python symbol index: `class`, `function`, `method`, `import`, test symbols
- BM25 для ранжирования кандидатов
- symbol usage index по AST references (`Name` / `Attribute`)
- tree context через путь/родительскую директорию
- content excerpts для текстовых файлов с подсветкой совпавших терминов

Сеньорские вопросы, которые теперь поддерживаются:

- `где функция rebuild_index`
- `кто использует IndexStore`
- `где тесты для answer_question`

Пример ответа:

```json
{
  "answer": "Самый релевантный путь: README.md",
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

Рекомендуемый следующий слой для более умного поиска с LLM:

- `index` — быстрый поиск по имени, пути, типу, suffix и content tokens
- `tree` — структура директорий для навигации и контекста
- `content excerpts` — короткие фрагменты релевантных текстовых файлов
- `LLM` — отвечает по подготовленному контексту и возвращает итоговый список файлов

Рекомендуемый flow:

1. пользователь отправляет вопрос вроде `где лежит файл readme`
2. сервер использует индекс для предварительного отбора кандидатов
3. сервер добавляет tree context и excerpts из найденных текстовых файлов
4. LLM получает только этот суженный контекст, без embeddings
5. сервер возвращает текстовый ответ и список файлов

В этой схеме LLM уже получает суженный контекст из `index + tree + excerpts`, а не весь проект целиком.

## BM25 runtime

- BM25 **автоматически строится при старте сервера** через существующий `context.start_indexing()`
- BM25 **не требует скачивания модели**
- BM25 **не использует GPU**
- BM25 хранится **в RAM / памяти процесса** и работает на CPU

Проверить состояние можно через:

- `GET /health`
- `GET /index/stats`

В stats есть поля:

- `bm25_ready`
- `bm25_backend`
- `bm25_loaded_in_memory`
- `bm25_loaded_in_gpu`
- `bm25_documents`
- `bm25_avg_document_length`

## Тесты

```bash
python3 -m unittest discover -s tests -v
```
