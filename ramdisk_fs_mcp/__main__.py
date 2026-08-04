import argparse
import sys
from pathlib import Path
from .infrastructure.mcp_server import create_mcp_server


def main():
    parser = argparse.ArgumentParser(
        description="DDD Hexagonal Codebase Skeleton MCP Server (Model Context Protocol)"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Root directory of codebase to index (default: current directory)",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    print(f"[MCP] Starting Codebase Skeleton MCP Server for root: {root}", file=sys.stderr)

    server = create_mcp_server(root_path=root)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
