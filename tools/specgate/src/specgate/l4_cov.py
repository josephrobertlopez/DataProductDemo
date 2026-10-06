"""L4 layer: every line of an AC's implementation must run under that AC's tests."""

import ast
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from coverage import CoverageData

from specgate.l2_trace import MarkerLocation, _extract_functions, get_marker_map


def _executable_lines(func_node: ast.FunctionDef) -> list[int]:
    """Line numbers of the statements in a function body (docstring excluded)."""
    docstring = func_node.body[0]
    has_doc = (
        isinstance(docstring, ast.Expr)
        and isinstance(docstring.value, ast.Constant)
        and isinstance(docstring.value.value, str)
    )
    lines = {
        node.lineno
        for node in ast.walk(func_node)
        if isinstance(node, ast.stmt) and node is not func_node and not (has_doc and node is docstring)
    }
    return sorted(lines)


def _covered_lines(test_dir: str, test_ids: list[str]) -> set[tuple[str, int]]:
    """Run exactly the given tests under coverage in a child process.

    Returns {(realpath, line)}. A child process keeps this run isolated from
    any tracer already active in the current one, and the data file lives in
    a temporary directory so nothing is written into the working tree.
    """
    with tempfile.TemporaryDirectory() as tmp:
        data_file = os.path.join(tmp, "l4.coverage")
        env = {k: v for k, v in os.environ.items() if not k.startswith("COVERAGE")}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        subprocess.run(
            [sys.executable, "-m", "coverage", "run", f"--rcfile={os.devnull}",
             f"--data-file={data_file}", "-m", "unittest", *test_ids],
            cwd=test_dir, env=env, capture_output=True, check=False,
        )
        data = CoverageData(basename=data_file)
        data.read()
        covered: set[tuple[str, int]] = set()
        for measured in data.measured_files():
            for line in data.lines(measured) or []:
                covered.add((os.path.realpath(measured), line))
        return covered


def _impl_lines(marker: MarkerLocation) -> list[int]:
    """Executable lines of the function a marker is attached to ([] if not found)."""
    source = Path(marker.file).read_text(encoding="utf-8")
    key = marker.qualname.split(":", 1)[1]
    info = _extract_functions(source, marker.file).get(key)
    return _executable_lines(info[1]) if info else []


# implements: AC-5
def check(
    ac_ids: set[str], src_dirs: list[str], test_dirs: list[str],
    stats: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """SG401 for each implementation line not executed by that AC's own tests.

    `stats`, when given, receives counts for the CLI's evidence line.
    """
    findings: list[dict[str, Any]] = []
    checked = 0
    for ac_id, markers in sorted(get_marker_map(src_dirs, test_dirs).items()):
        impls = [m for m in markers if m.marker_type == "implements"]
        covers = [m for m in markers if m.marker_type == "covers"]
        if not impls or not covers:
            continue  # L2 already reports an AC missing either side

        by_dir: dict[str, list[str]] = {}
        for cover in covers:
            test_id = cover.qualname.replace(":", ".")
            by_dir.setdefault(str(Path(cover.file).resolve().parent), []).append(test_id)
        covered: set[tuple[str, int]] = set()
        for test_dir, test_ids in sorted(by_dir.items()):
            covered |= _covered_lines(test_dir, sorted(test_ids))

        for impl in impls:
            real = os.path.realpath(impl.file)
            for line in _impl_lines(impl):
                checked += 1
                if (real, line) not in covered:
                    findings.append({
                        "rule": "SG401",
                        "file": impl.file,
                        "line": line,
                        "message": f"Line {line} in {impl.funcname} implementing {ac_id} "
                        f"not covered by test context {ac_id}",
                    })

    findings.sort(key=lambda f: (f["file"], f["line"], f["rule"]))
    if stats is not None:
        stats["lines"] = checked
    return findings
