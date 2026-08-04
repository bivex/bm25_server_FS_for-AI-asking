import tempfile
from pathlib import Path

from ramdisk_fs_server.ast_grep_symbols import HAS_AST_GREP, extract_ast_grep_symbols
from ramdisk_fs_server.indexer import IndexStore


def test_ast_grep_extraction_c_and_js():
    if not HAS_AST_GREP:
        return

    with tempfile.TemporaryDirectory() as tmp:
        c_file = Path(tmp) / "main.c"
        c_file.write_text(
            "int process_data(int count) {\n"
            "    printf(\"processing %d\\n\", count);\n"
            "    return count * 2;\n"
            "}\n"
        )

        symbols, references = extract_ast_grep_symbols(c_file, "main.c")
        sym_map = {s.name: s for s in symbols}

        assert "process_data" in sym_map
        fn = sym_map["process_data"]
        assert fn.kind == "function"
        assert "printf" in fn.calls


def test_ast_grep_indexing_and_skeleton():
    if not HAS_AST_GREP:
        return

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "server.js").write_text(
            "class Server {\n"
            "    start(port) {\n"
            "        console.log('Listening on port ' + port);\n"
            "    }\n"
            "}\n"
        )

        store = IndexStore()
        store.rebuild(root)

        dsl_out = store.get_skeleton_dsl("server.js")
        assert "# FILE: server.js" in dsl_out
