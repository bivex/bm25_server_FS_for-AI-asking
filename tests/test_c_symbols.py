import tempfile
from pathlib import Path

from ramdisk_fs_server.c_symbols import extract_c_symbols
from ramdisk_fs_server.indexer import IndexStore
from ramdisk_fs_server.symbol_extractor import extract_code_symbols


def test_c_symbol_extraction():
    with tempfile.TemporaryDirectory() as tmp:
        c_file = Path(tmp) / "main.c"
        c_file.write_text(
            "#include <stdio.h>\n"
            '#include "utils.h"\n\n'
            "#define MAX_BUFFER 1024\n\n"
            "typedef struct {\n"
            "    int fd;\n"
            "} Socket;\n\n"
            "int send_data(Socket *sock, const char *data) {\n"
            "    printf(\"Sending data...\\n\");\n"
            "    return write(sock->fd, data, strlen(data));\n"
            "}\n"
        )

        symbols, references = extract_c_symbols(c_file, "main.c")
        sym_map = {s.name: s for s in symbols}

        assert "stdio.h" in sym_map
        assert sym_map["stdio.h"].kind == "include"

        assert "MAX_BUFFER" in sym_map
        assert sym_map["MAX_BUFFER"].kind == "macro"

        assert "send_data" in sym_map
        send_fn = sym_map["send_data"]
        assert send_fn.kind == "function"
        assert send_fn.language == "c"
        assert "printf" in send_fn.calls
        assert "write" in send_fn.calls
        assert "strlen" in send_fn.calls


def test_c_codebase_indexing_and_skeleton():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "network.h").write_text(
            "#include <stdint.h>\n"
            "#define PORT 8080\n"
            "struct Header {\n"
            "    uint32_t len;\n"
            "};\n"
            "int net_init(int port);\n"
        )
        (root / "network.c").write_text(
            '#include "network.h"\n'
            "int net_init(int port) {\n"
            "    struct Header h;\n"
            "    h.len = port;\n"
            "    return socket_create(port);\n"
            "}\n"
        )

        store = IndexStore()
        store.rebuild(root)

        stats = store.stats()
        assert stats["python_symbol_count"] > 0

        dsl_header = store.get_skeleton_dsl("network.h")
        assert "# FILE: network.h" in dsl_header
        assert "#include <stdint.h>" in dsl_header
        assert "#define PORT" in dsl_header

        dsl_c = store.get_skeleton_dsl("network.c")
        assert "# FILE: network.c" in dsl_c
        assert "def net_init" in dsl_c
        assert "# calls: socket_create" in dsl_c
