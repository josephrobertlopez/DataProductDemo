"""L2 layer: AST-based AC marker tracing and validation."""

import ast
import io
import os
import tokenize
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache", ".tox"})

FuncInfo = tuple[int, ast.FunctionDef, str, str]
Found = tuple[str, str, int, ast.FunctionDef]


@dataclass
class MarkerLocation:
    """Represents a marker location in source code."""

    file: str
    funcname: str
    line: int
    ac_id: str
    marker_type: str  # 'implements' or 'covers'
    qualname: str = ""


SKIP_MARKER = ".specgate-skip"


def iter_py_files(directory: str) -> list[Path]:
    """Sorted .py files under directory, skipping tool caches, test-data `fixtures` dirs,
    and any directory holding a `.specgate-skip` file.

    The marker file exists for vendored code: a repo that keeps a copy of
    specgate (or any tool carrying its own `# implements:` markers) would
    otherwise have those markers read as its own ACs.
    """
    found: list[Path] = []
    for root, dirs, files in os.walk(directory):
        if SKIP_MARKER in files:
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and d != "fixtures")
        found.extend(Path(root) / name for name in sorted(files) if name.endswith(".py"))
    return found


def _tokenize_comments(source: str) -> dict[int, str]:
    """Map line number to comment text (without the leading '#')."""
    comments: dict[int, str] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                comments[tok.start[0]] = tok.string[1:].strip()
    except (tokenize.TokenError, SyntaxError):
        pass
    return comments


def _parse_marker(comment: str) -> tuple[str, str] | None:
    """Parse '<implements|covers>: AC-x ...' into (marker_type, ac_id)."""
    text = comment.strip()
    for marker_type in ("implements", "covers"):
        prefix = marker_type + ":"
        if text.startswith(prefix):
            rest = text[len(prefix):].split()
            return (marker_type, rest[0]) if rest else None
    return None


def _extract_functions(source: str, filepath: str) -> dict[str, FuncInfo]:
    """Extract module-level functions and class methods.

    Keyed by class-qualified name ('Class.method'); the value is
    (line, node, bare_name, 'module:Class.method'). Functions nested inside
    other functions are not extracted.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    module = Path(filepath).stem
    found: dict[str, FuncInfo] = {}

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.FunctionDef):
                key = prefix + node.name
                found[key] = (node.lineno, node, node.name, f"{module}:{key}")
            elif isinstance(node, ast.ClassDef):
                visit(node.body, f"{prefix}{node.name}.")

    visit(tree.body, "")
    return found


def _has_asserts(func_node: ast.FunctionDef) -> bool:
    """True if the function has an assert statement or a .assert* call."""
    for node in ast.walk(func_node):
        if isinstance(node, ast.Assert):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr.startswith("assert")
        ):
            return True
    return False


def _is_empty_body(func_node: ast.FunctionDef) -> bool:
    """True if the body is only a docstring, pass and/or '...'."""
    body = func_node.body
    first = body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        body = body[1:]
    for stmt in body:
        if isinstance(stmt, ast.Pass):
            continue
        if (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and stmt.value.value is ...
        ):
            continue
        return False
    return True


def _read(path: Path) -> str | None:
    """Read a file as text; None when it is unreadable or not UTF-8."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _markers_in_file(
    path: Path, marker_type: str, only_tests: bool = False
) -> list[tuple[MarkerLocation, ast.FunctionDef]]:
    """All markers of marker_type attached to functions of one file.

    A marker attaches to a function when its comment is on the line above
    the def or on the def line itself.
    """
    source = _read(path)
    if source is None:
        return []
    comments = _tokenize_comments(source)
    out: list[tuple[MarkerLocation, ast.FunctionDef]] = []
    for line, node, bare, qualname in _extract_functions(source, str(path)).values():
        if only_tests and not bare.startswith("test_"):
            continue
        for check_line in (line - 1, line):
            parsed = _parse_marker(comments.get(check_line, ""))
            if parsed and parsed[0] == marker_type:
                loc = MarkerLocation(str(path), bare, line, parsed[1], marker_type, qualname)
                out.append((loc, node))
    return out


def scan_markers(
    src_dirs: list[str], test_dirs: list[str]
) -> dict[str, list[MarkerLocation]]:
    """Map AC id to its implements markers (src dirs) and covers markers (test dirs)."""
    markers_by_ac: dict[str, list[MarkerLocation]] = {}
    for dirs, marker_type in ((src_dirs, "implements"), (test_dirs, "covers")):
        for directory in dirs:
            for py_file in iter_py_files(directory):
                for loc, _node in _markers_in_file(py_file, marker_type):
                    markers_by_ac.setdefault(loc.ac_id, []).append(loc)
    return markers_by_ac


def _collect(
    paths: Iterable[str], marker_type: str, only_tests: bool
) -> dict[str, list[Found]]:
    """AC id -> [(file, func_name, line, node)] for the given files."""
    found: dict[str, list[Found]] = {}
    for path in paths:
        for loc, node in _markers_in_file(Path(path), marker_type, only_tests):
            found.setdefault(loc.ac_id, []).append((path, loc.funcname, loc.line, node))
    return found


def _finding(rule: str, file: str, line: int, message: str) -> dict[str, Any]:
    return {"rule": rule, "file": file, "line": line, "message": message}


# implements: AC-3
def check(
    ac_ids: set[str], src_paths: list[str], test_paths: list[str], prd_path: str = ""
) -> list[dict[str, Any]]:
    """Check AC marker violations SG201..SG206 over source and test files."""
    findings: list[dict[str, Any]] = []
    implemented = _collect(src_paths, "implements", False)
    covered = _collect(test_paths, "covers", True)

    # SG201/SG202 need one side of the pair to exist, so an AC nobody had
    # started passed L2 -- the opposite of "every AC is implemented and covered".
    for ac_id in sorted(ac_ids - implemented.keys() - covered.keys()):
        findings.append(_finding(
            "SG206", prd_path, 0,
            f"{ac_id} has no '# implements: {ac_id}' code and no '# covers: {ac_id}' test",
        ))

    for ac_id, tests in covered.items():
        if ac_id not in implemented:
            for path, _name, line, _node in tests:
                findings.append(_finding(
                    "SG201", path, line,
                    f"Test covers {ac_id} but no implementation found "
                    f"with '# implements: {ac_id}' marker",
                ))

    for ac_id, impls in implemented.items():
        if ac_id not in covered:
            for path, _name, line, _node in impls:
                findings.append(_finding(
                    "SG202", path, line,
                    f"Implementation of {ac_id} found but no test with "
                    f"'# covers: {ac_id}' marker",
                ))

    for table in (implemented, covered):
        for ac_id, locs in table.items():
            if ac_id not in ac_ids:
                for path, _name, line, _node in locs:
                    findings.append(_finding(
                        "SG203", path, line, f"Marker references unknown AC id: {ac_id}"
                    ))

    for ac_id, tests in covered.items():
        for path, name, line, node in tests:
            if not _has_asserts(node):
                findings.append(_finding(
                    "SG204", path, line,
                    f"Test '{name}' with '# covers: {ac_id}' has no assert statements",
                ))

    for ac_id, impls in implemented.items():
        for path, name, line, node in impls:
            if _is_empty_body(node):
                findings.append(_finding(
                    "SG205", path, line,
                    f"Function '{name}' with '# implements: {ac_id}' has empty body",
                ))

    return findings


def get_marker_map(
    src_dirs: list[str], test_dirs: list[str]
) -> dict[str, list[MarkerLocation]]:
    """Marker map AC -> markers; alias for scan_markers."""
    return scan_markers(src_dirs, test_dirs)
