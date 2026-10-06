"""L5 layer: mutation testing of each AC's implementation with AST transformations."""

import ast
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from specgate.l2_trace import SKIP_DIRS, MarkerLocation, _extract_functions, get_marker_map

MUTATION_TYPES = ("cmp_flip", "bool_flip", "return_none", "arith_swap")

_CMP_FLIP: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Lt: ast.GtE,
    ast.LtE: ast.Gt,
    ast.Gt: ast.LtE,
    ast.GtE: ast.Lt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
}


class MutantGenerator(ast.NodeTransformer):
    """Apply one deterministic mutation of one type to the first eligible node."""

    def __init__(self, target_lines: set[int], mutation_type: str, seed: int):
        self.target_lines = target_lines
        self.mutation_type = mutation_type
        self.rng = random.Random(seed)
        self.mutated_line: int | None = None

    def _eligible(self, node: ast.expr | ast.stmt, mutation_type: str) -> bool:
        return (
            self.mutation_type == mutation_type
            and node.lineno in self.target_lines
            and self.mutated_line is None
        )

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        """Flip comparison operators (< <-> >=, > <-> <=, == <-> !=)."""
        if self._eligible(node, "cmp_flip"):
            new_ops = [_CMP_FLIP[type(op)]() if type(op) in _CMP_FLIP else op for op in node.ops]
            if new_ops != node.ops:
                self.mutated_line = node.lineno
                node.ops = new_ops
        return self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        """Flip and <-> or."""
        if self._eligible(node, "bool_flip"):
            self.mutated_line = node.lineno
            node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        return self.generic_visit(node)

    def visit_Return(self, node: ast.Return) -> ast.AST:
        """Replace a returned value with None."""
        if self._eligible(node, "return_none") and node.value is not None:
            self.mutated_line = node.lineno
            node.value = None
        return self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        """Swap + <-> -."""
        if self._eligible(node, "arith_swap") and isinstance(node.op, (ast.Add, ast.Sub)):
            self.mutated_line = node.lineno
            node.op = ast.Sub() if isinstance(node.op, ast.Add) else ast.Add()
        return self.generic_visit(node)


def _function_lines(source: str, qualname: str) -> set[int]:
    """Line numbers inside the function named by a marker qualname ('mod:Class.func')."""
    info = _extract_functions(source, "").get(qualname.split(":", 1)[1])
    if info is None:
        return set()
    return {child.lineno for child in ast.walk(info[1]) if hasattr(child, "lineno")}


def _generate_source_mutant(
    source: str, target_lines: set[int], mutation_type: str, seed: int
) -> tuple[str, int] | None:
    """Mutated source and the original line mutated, or None if nothing mutates."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    gen = MutantGenerator(target_lines, mutation_type, seed)
    new_tree = gen.visit(tree)
    if gen.mutated_line is None:
        return None
    ast.fix_missing_locations(new_tree)
    return ast.unparse(new_tree), gen.mutated_line


_TIMEOUT = 300
_GIT_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


def _run_tests(test_dir: str, *test_ids: str, env: dict[str, str] | None = None) -> bool:
    """Run unittest ids (modules or mod.Class.test) in test_dir; True when all pass.

    A timeout counts as a failure: a mutant that hangs its tests is caught.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "-f", *test_ids],
            cwd=test_dir,
            capture_output=True,
            timeout=_TIMEOUT,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
            env=env,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def _import_root(path: Path) -> Path:
    """The sys.path entry a module is imported from: above its outermost package."""
    root = path.parent
    while (root / "__init__.py").is_file():
        root = root.parent
    return root


class _Sandbox:
    """A copy of the tree holding the implementation and its tests, kept in its layout.

    The previous sandbox flattened every module into one directory. Tests that
    find fixtures beside `__file__`, or import their code as a package, then
    failed with no mutation at all, so every mutant counted as killed and L5
    reported green without measuring anything. Here the layout is preserved,
    the copy's import roots come first on PYTHONPATH (ahead of any installed
    or editable copy of the same package), and `baseline` must pass before a
    kill is believed.
    """

    def __init__(self, files: list[str]) -> None:
        reals = [Path(os.path.realpath(f)) for f in files]
        self.root = Path(os.path.commonpath([str(p.parent) for p in reals]))
        self._tmp = tempfile.TemporaryDirectory()
        self.copy = Path(self._tmp.name) / "tree"
        shutil.copytree(self.root, self.copy, ignore=shutil.ignore_patterns(*SKIP_DIRS))
        roots = sorted({str(_import_root(self.map(p))) for p in reals})
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("COVERAGE") and k not in _GIT_VARS}
        # Stale bytecode could be served for a mutant written within the same second.
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = os.pathsep.join(roots + [env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
        self.env = env

    def map(self, path: str | Path) -> Path:
        """Where an original file lives inside the copy."""
        return self.copy / Path(os.path.realpath(path)).relative_to(self.root)

    def passes(self, tests: dict[str, list[str]]) -> bool:
        """True when every test id, run from its directory in the copy, passes."""
        return all(_run_tests(str(self.map(d)), *ids, env=self.env) for d, ids in sorted(tests.items()))

    def write(self, original: str, source: str) -> None:
        self.map(original).write_text(source, encoding="utf-8")

    def close(self) -> None:
        self._tmp.cleanup()


def _survives(box: _Sandbox, impl_file: str, mutant_source: str, tests: dict[str, list[str]]) -> bool:
    """True when the AC's tests still pass with the mutant in place (restored afterwards)."""
    original = box.map(impl_file).read_text(encoding="utf-8")
    box.write(impl_file, mutant_source)
    try:
        return box.passes(tests)
    finally:
        box.write(impl_file, original)


def _test_ids(covers: list[MarkerLocation]) -> dict[str, list[str]]:
    """The AC's own covering tests as unittest ids, grouped by directory."""
    by_dir: dict[str, list[str]] = {}
    for cover in covers:
        by_dir.setdefault(str(Path(cover.file).parent), []).append(cover.qualname.replace(":", "."))
    return {d: sorted(set(ids)) for d, ids in by_dir.items()}


# implements: AC-6
def check(
    ac_ids: set[str], src_dirs: list[str], test_dirs: list[str],
    stats: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """SG501 for each mutant of an AC's implementation that its tests do not kill.

    SG502 when the AC's tests fail before anything is mutated: a kill would
    then be the sandbox's doing, not the tests'. `stats`, when given, receives
    counts for the CLI's evidence line.
    """
    findings: list[dict[str, Any]] = []
    mutants = survivors = 0
    marker_map = get_marker_map(src_dirs, test_dirs)
    plan = []
    for ac_id in sorted(ac_ids):
        markers = marker_map.get(ac_id, [])
        covers = [m for m in markers if m.marker_type == "covers"]
        impls = [m for m in markers if m.marker_type == "implements"]
        if covers and impls:
            plan.append((ac_id, impls, covers))

    if not plan:
        if stats is not None:
            stats.update(mutants=0, killed=0)
        return findings

    box = _Sandbox(sorted({m.file for _ac, impls, covers in plan for m in impls + covers}))
    try:
        for ac_id, impls, covers in plan:
            tests = _test_ids(covers)
            if not box.passes(tests):
                findings.append({
                    "rule": "SG502",
                    "file": covers[0].file,
                    "line": covers[0].line,
                    "message": f"{ac_id}: covering tests fail in the L5 sandbox before any "
                               f"mutation, so no mutant result can be trusted",
                })
                continue
            before = mutants
            for impl in impls:
                source = Path(impl.file).read_text(encoding="utf-8")
                lines = _function_lines(source, impl.qualname)
                for mutation_type in MUTATION_TYPES:
                    mutant = _generate_source_mutant(source, lines, mutation_type, 0)
                    if mutant is None:
                        continue
                    mutant_source, line = mutant
                    mutants += 1
                    if _survives(box, impl.file, mutant_source, tests):
                        survivors += 1
                        findings.append({
                            "rule": "SG501",
                            "file": impl.file,
                            "line": line,
                            "message": f"Surviving mutant in {impl.funcname}: "
                                       f"{mutation_type} at line {line}",
                        })
            # Nothing to mutate is not the same as every mutant killed: a marked
            # function that only delegates, or uses no mutable operator, would
            # otherwise print "0/0 mutants killed" as a pass.
            if mutants == before:
                findings.append({
                    "rule": "SG503",
                    "file": impls[0].file,
                    "line": impls[0].line,
                    "message": f"{ac_id}: no mutable operation in its implementation, "
                               f"so L5 cannot show the tests check anything",
                })
    finally:
        box.close()
    findings.sort(key=lambda f: (f["file"], f["line"], f["rule"], f["message"]))
    if stats is not None:
        stats["mutants"] = mutants
        stats["killed"] = mutants - survivors
    return findings
