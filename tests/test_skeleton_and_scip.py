import json
import tempfile
import threading
from pathlib import Path
from urllib.request import urlopen

from ramdisk_fs_server.indexer import IndexStore
from ramdisk_fs_server.python_symbols import extract_python_symbols
from ramdisk_fs_server.scip_integration import (
    SCIPGraph,
    is_scip_available,
    parse_scip_json_export,
)
from ramdisk_fs_server.server import AppContext, ThreadingHTTPServer, make_handler
from ramdisk_fs_server.skeleton_dsl import (
    render_contour_skeleton_dsl,
    render_file_skeleton_dsl,
)


def test_ast_symbol_extraction_inherits_calls_signature():
    with tempfile.TemporaryDirectory() as tmp:
        file_path = Path(tmp) / "auth_service.py"
        file_path.write_text(
            "class BaseService:\n"
            "    pass\n\n"
            "class AuthService(BaseService):\n"
            '    """Handles authentication."""\n'
            "    def login(self, username: str, password_hash: str) -> bool:\n"
            "        return UserRepository.find_by_username(username)\n"
        )

        symbols, references = extract_python_symbols(file_path, "auth_service.py")
        sym_map = {s.name: s for s in symbols}

        assert "AuthService" in sym_map
        auth_class = sym_map["AuthService"]
        assert auth_class.kind == "class"
        assert auth_class.inherits == ["BaseService"]

        assert "login" in sym_map
        login_method = sym_map["login"]
        assert login_method.kind == "method"
        assert login_method.signature == "(self, username: str, password_hash: str) -> bool"
        assert "UserRepository.find_by_username" in login_method.calls


def test_skeleton_dsl_rendering():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "base.py").write_text("class BaseService:\n    pass\n")
        (root / "repo.py").write_text("class UserRepository:\n    def find_by_username(self, u):\n        pass\n")
        (root / "auth.py").write_text(
            "from base import BaseService\n"
            "from repo import UserRepository\n\n"
            "class AuthService(BaseService):\n"
            '    """Auth module service."""\n'
            "    def login(self, username: str) -> bool:\n"
            "        return UserRepository.find_by_username(username)\n"
        )
        (root / "api.py").write_text(
            "from auth import AuthService\n\n"
            "def handle_login():\n"
            "    service = AuthService()\n"
            "    return service.login('admin')\n"
        )

        store = IndexStore()
        store.rebuild(root)

        dsl_out = store.get_skeleton_dsl("auth.py")
        assert "# FILE: auth.py" in dsl_out
        assert "class AuthService(BaseService):" in dsl_out
        assert "def login(self, username: str) -> bool:" in dsl_out
        assert "# calls: UserRepository.find_by_username" in dsl_out
        assert "# used_by: handle_login (api.py:L" in dsl_out

        contour_out = store.get_contour_skeleton_dsl("AuthService")
        assert "# CONTOUR: AuthService" in contour_out
        assert "# FILE: auth.py" in contour_out


def test_skeleton_and_contour_http_endpoints():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "service.py").write_text(
            "class PaymentService:\n"
            "    def process_payment(self, amount: float) -> bool:\n"
            "        return True\n"
        )
        context = AppContext(root_path=root, index_refresh_seconds=0)
        context.rebuild_index()

        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(context))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"

        try:
            with urlopen(f"{base_url}/index/skeleton?path=service.py") as resp:
                data = json.load(resp)
                assert data["path"] == "service.py"
                assert "class PaymentService:" in data["skeleton_dsl"]
                assert "def process_payment(self, amount: float) -> bool:" in data["skeleton_dsl"]

            with urlopen(f"{base_url}/index/contour?q=PaymentService") as resp:
                data = json.load(resp)
                assert data["query"] == "PaymentService"
                assert "# CONTOUR: PaymentService" in data["skeleton_dsl"]
        finally:
            server.shutdown()


def test_scip_integration_parsing_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        json_file = Path(tmp) / "index.scip.json"
        json_file.write_text(
            json.dumps(
                {
                    "documents": [
                        {
                            "relative_path": "main.py",
                            "language": "python",
                            "occurrences": [
                                {
                                    "symbol": "scip-python python main main_func.",
                                    "symbol_roles": 1,
                                    "range": [1, 0, 1, 9],
                                }
                            ],
                        }
                    ]
                }
            )
        )

        graph = parse_scip_json_export(json_file)
        assert "main.py" in graph.documents
        resolved = graph.resolve_definition("scip-python python main main_func.")
        assert resolved == ("main.py", [1, 0, 1, 9])
