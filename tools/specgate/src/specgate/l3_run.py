"""L3 layer: unittest runner with JUnit XML emission and AC tracing."""

import os
import sys
import unittest
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from specgate.l2_trace import MarkerLocation, get_marker_map

ExcInfo = tuple[type[BaseException], BaseException, TracebackType] | tuple[None, None, None]


@dataclass
class TestResult:
    """Represents a test result."""

    __test__ = False  # not a pytest/unittest collectable

    test_id: str
    status: str  # PASS, FAIL, ERROR, SKIP, XFAIL, UXPASS
    funcname: str
    file: str


class CustomTestResult(unittest.TestResult):
    """Test result that records skips, expected failures and unexpected successes."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[TestResult] = []

    def addSuccess(self, test: unittest.TestCase) -> None:
        super().addSuccess(test)
        self._record_result(test, "PASS")

    def addError(self, test: unittest.TestCase, err: ExcInfo) -> None:
        super().addError(test, err)
        self._record_result(test, "ERROR")

    def addFailure(self, test: unittest.TestCase, err: ExcInfo) -> None:
        super().addFailure(test, err)
        self._record_result(test, "FAIL")

    def addSkip(self, test: unittest.TestCase, reason: str) -> None:
        super().addSkip(test, reason)
        self._record_result(test, "SKIP")

    def addExpectedFailure(self, test: unittest.TestCase, err: ExcInfo) -> None:
        super().addExpectedFailure(test, err)
        self._record_result(test, "XFAIL")

    def addUnexpectedSuccess(self, test: unittest.TestCase) -> None:
        super().addUnexpectedSuccess(test)
        self._record_result(test, "UXPASS")

    def _record_result(self, test: unittest.TestCase, status: str) -> None:
        """Record a result; file is the defining module's file."""
        test_id = str(test)  # "test_name (module.Class.test_name)"
        module_name = type(test).__module__
        module = sys.modules.get(module_name)
        file_path = getattr(module, "__file__", None) or module_name.replace(".", os.sep) + ".py"
        self.results.append(TestResult(test_id, status, test_id.split(" ")[0], file_path))


def run_tests(test_dir: str, top_level_dir: str | None = None) -> list[TestResult]:
    """Discover and run unittest tests under test_dir.

    sys.path and sys.modules are restored afterwards, so test modules
    from one directory never leak into the next run.
    """
    start = os.path.abspath(test_dir)
    top = os.path.abspath(top_level_dir or test_dir)

    saved_path = sys.path[:]
    stems = {p.stem for p in Path(top).glob("*.py")}
    saved_modules = {name: sys.modules.pop(name) for name in stems if name in sys.modules}
    before = set(sys.modules)
    sys.path.insert(0, top)
    try:
        suite = unittest.TestLoader().discover(start, pattern="test*.py", top_level_dir=top)
        result = CustomTestResult()
        suite.run(result)
        return result.results
    finally:
        sys.path[:] = saved_path
        for name in set(sys.modules) - before:
            del sys.modules[name]
        sys.modules.update(saved_modules)


# implements: AC-4
def write_junit(
    results: list[TestResult], ac_map: dict[str, list[MarkerLocation]], path: str
) -> None:
    """Write JUnit XML with a <property name="ac"> per AC a test covers."""
    testsuites = ET.Element("testsuites")
    testsuite = ET.SubElement(testsuites, "testsuite")
    testsuite.set("name", "all")
    testsuite.set("tests", str(len(results)))
    testsuite.set("failures", str(sum(r.status == "FAIL" for r in results)))
    testsuite.set("errors", str(sum(r.status == "ERROR" for r in results)))

    func_to_acs: dict[str, list[str]] = {}
    for ac_id, markers in ac_map.items():
        for marker in markers:
            if marker.marker_type == "covers":
                func_to_acs.setdefault(marker.funcname, []).append(ac_id)

    status_tag = {"FAIL": "failure", "ERROR": "error", "SKIP": "skipped"}
    for result in results:
        testcase = ET.SubElement(testsuite, "testcase")
        testcase.set("name", result.funcname)
        testcase.set("classname", result.file)
        if result.funcname in func_to_acs:
            properties = ET.SubElement(testcase, "properties")
            for ac_id in sorted(func_to_acs[result.funcname]):
                prop = ET.SubElement(properties, "property")
                prop.set("name", "ac")
                prop.set("value", ac_id)
        if result.status in status_tag:
            ET.SubElement(testcase, status_tag[result.status])

    ET.ElementTree(testsuites).write(path, encoding="utf-8", xml_declaration=True)


# implements: AC-4
def check(
    ac_ids: set[str], src_dirs: list[str], test_dirs: list[str], junit_path: str,
    stats: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """Run the tests, write JUnit, and flag SG301 (non-PASS) / SG302 (never ran).

    `stats`, when given, receives counts for the CLI's evidence line.
    """
    findings: list[dict[str, Any]] = []
    ac_map = get_marker_map(src_dirs, test_dirs)
    results = [r for test_dir in test_dirs for r in run_tests(test_dir, test_dir)]
    write_junit(results, ac_map, junit_path)
    if stats is not None:
        stats["tests"] = len(results)
        stats["passed"] = sum(r.status == "PASS" for r in results)

    covers = [
        (ac_id, marker)
        for ac_id, markers in ac_map.items()
        for marker in markers
        if marker.marker_type == "covers"
    ]
    for result in results:
        if result.status == "PASS":
            continue
        for ac_id, marker in covers:
            if marker.funcname == result.funcname:
                findings.append({
                    "rule": "SG301",
                    "file": result.file,
                    "line": marker.line,
                    "message": f"AC {ac_id} has test {result.funcname} with status {result.status}",
                })

    ran = {r.funcname for r in results}
    for ac_id, marker in covers:
        if marker.funcname not in ran:
            findings.append({
                "rule": "SG302",
                "file": marker.file,
                "line": marker.line,
                "message": f"AC {ac_id} has no test that ran (marker found but test not executed)",
            })
    return findings
