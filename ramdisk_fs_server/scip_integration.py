from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SCIPOccurrence:
    symbol: str
    symbol_roles: int  # 1 = Definition, 0 = Read/Reference, etc.
    range: list[int]   # [start_line, start_col, end_line, end_col]
    override_documentation: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SCIPDocument:
    relative_path: str
    language: str
    symbols: list[dict[str, Any]] = field(default_factory=list)
    occurrences: list[SCIPOccurrence] = field(default_factory=list)


@dataclass(slots=True)
class SCIPGraph:
    documents: dict[str, SCIPDocument] = field(default_factory=dict)
    symbol_definitions: dict[str, tuple[str, list[int]]] = field(default_factory=dict)
    symbol_references: dict[str, list[tuple[str, list[int]]]] = field(default_factory=list)

    def resolve_definition(self, symbol_name: str) -> tuple[str, list[int]] | None:
        """Find (relative_path, line_range) where symbol_name is defined."""
        return self.symbol_definitions.get(symbol_name)

    def resolve_references(self, symbol_name: str) -> list[tuple[str, list[int]]]:
        """Find all (relative_path, line_range) where symbol_name is referenced."""
        return self.symbol_references.get(symbol_name, [])


def is_scip_available() -> bool:
    """Return True if scip-python or scip binary is installed on system PATH."""
    return shutil.which("scip-python") is not None or shutil.which("scip") is not None


def build_scip_index(project_root: Path, output_path: Path | None = None) -> Path | None:
    """Run scip-python to generate an index.scip protobuf file in RAM disk or project_root."""
    scip_python_bin = shutil.which("scip-python")
    if not scip_python_bin:
        logger.warning("scip-python binary not found on PATH")
        return None

    target_dir = output_path.parent if output_path else project_root
    target_dir.mkdir(parents=True, exist_ok=True)
    out_file = output_path if output_path else project_root / "index.scip"

    try:
        cmd = [scip_python_bin, "--output", str(out_file)]
        res = subprocess.run(cmd, cwd=str(project_root), capture_output=True, text=True, timeout=60)
        if res.returncode == 0 and out_file.exists():
            logger.info("Successfully generated SCIP index at %s", out_file)
            return out_file
        else:
            logger.error("scip-python failed (code %d): %s", res.returncode, res.stderr)
            return None
    except Exception as e:
        logger.error("Error executing scip-python: %s", e)
        return None


def parse_scip_json_export(scip_json_file: Path) -> SCIPGraph:
    """Parse SCIP data exported as JSON (e.g. from `scip print --json index.scip`)."""
    graph = SCIPGraph()
    if not scip_json_file.exists():
        return graph

    try:
        data = json.loads(scip_json_file.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Failed to parse SCIP JSON: %s", e)
        return graph

    for doc_data in data.get("documents", []):
        rel_path = doc_data.get("relative_path", "")
        lang = doc_data.get("language", "")
        occurrences: list[SCIPOccurrence] = []

        for occ in doc_data.get("occurrences", []):
            sym = occ.get("symbol", "")
            roles = occ.get("symbol_roles", 0)
            rng = occ.get("range", [0, 0, 0, 0])
            occurrences.append(SCIPOccurrence(symbol=sym, symbol_roles=roles, range=rng))

            # 1 = Definition role in SCIP specification
            if roles & 1:
                graph.symbol_definitions[sym] = (rel_path, rng)
            else:
                graph.symbol_references.setdefault(sym, []).append((rel_path, rng))

        doc = SCIPDocument(
            relative_path=rel_path,
            language=lang,
            symbols=doc_data.get("symbols", []),
            occurrences=occurrences,
        )
        graph.documents[rel_path] = doc

    return graph


def load_scip_or_fallback(scip_file: Path | None) -> SCIPGraph | None:
    """Load SCIP graph if scip_file exists (or if `scip print --json` can convert it)."""
    if not scip_file or not scip_file.exists():
        return None

    scip_bin = shutil.which("scip")
    if scip_bin:
        json_out = scip_file.with_suffix(".json")
        try:
            res = subprocess.run([scip_bin, "print", "--json", str(scip_file)], capture_output=True, text=True, timeout=30)
            if res.returncode == 0 and res.stdout:
                json_out.write_text(res.stdout, encoding="utf-8")
                return parse_scip_json_export(json_out)
        except Exception:
            pass

    return None
