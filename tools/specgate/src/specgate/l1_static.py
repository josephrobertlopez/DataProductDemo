"""L1 layer for static code analysis (ruff, mypy, vulture, banned tokens, markdownlint)."""

import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from itertools import pairwise
from pathlib import Path, PurePath
from typing import Any

from specgate.l0_schema import npx

_BANNED_WORDS = ("TO" + "DO", "FIX" + "ME", "X" + "XX", "@sk" + "ip", "skip" + "Test", "expected" + "Failure")
_BANNED = re.compile(r"\b(" + "|".join(_BANNED_WORDS) + r")\b")
_MARKER_LINE = re.compile(r"^\s*# (implements|covers): AC-\d+")
_MDL_LINE = re.compile(
    r"^(?P<file>(?:[A-Za-z]:)?[^:]+):(?P<line>\d+)(?::\d+)?\s+(?:error\s+|warning\s+)?"
    r"(?P<rule>MD\d+(?:/[\w-]+)*)\s+(?P<msg>.+)$"
)


def _is_marker_line(path: str, row: int) -> bool:
    """True when line `row` of `path` is an AC marker comment (not commented-out code)."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return False
    return 0 < row <= len(lines) and bool(_MARKER_LINE.match(lines[row - 1]))


def _is_fixture(path: str) -> bool:
    parts = PurePath(path).parts
    return any(pair == ("tests", "fixtures") for pair in pairwise(parts))


# implements: AC-2
def check_ruff(paths: list[str], runner: Callable[..., Any] | None = None) -> list[dict[str, Any]]:
    """
    Run ruff on Python files to check for F401, ERA, T20 violations.

    Args:
        paths: List of file paths to check
        runner: Optional subprocess runner (default: subprocess.run)

    Returns:
        List of findings with rule='SG101'
    """
    if runner is None:
        runner = subprocess.run

    if not paths:
        return []

    # Run ruff with select F401,ERA,T20
    cmd = [
        sys.executable, '-m', 'ruff', 'check',
        '--select', 'F401,ERA,T20',
        '--output-format', 'json', '--no-cache'
    ] + paths

    try:
        result = runner(cmd, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as e:
        return [{'rule': 'SG101', 'file': '', 'line': 0, 'message': f'ruff error: {e}'}]

    if result.returncode == 0:
        return []

    findings = []
    if result.stdout:
        try:
            output = json.loads(result.stdout)
            for item in output:
                location = item.get('location', {})
                row = location.get('row', 0) if isinstance(location, dict) else 0
                if item.get('code') == 'ERA001' and _is_marker_line(item.get('filename', ''), row):
                    continue
                findings.append({
                    'rule': 'SG101',
                    'file': item.get('filename', ''),
                    'line': row,
                    'message': item.get('message', '')
                })
        except json.JSONDecodeError:
            # Fallback to stderr or empty
            pass

    if not findings and result.stderr:
        findings.append({
            'rule': 'SG101',
            'file': '',
            'line': 0,
            'message': result.stderr
        })

    return findings


# implements: AC-2
def check_mypy(paths: list[str], runner: Callable[..., Any] | None = None) -> list[dict[str, Any]]:
    """
    Run mypy --strict on Python files.

    Args:
        paths: List of file paths to check
        runner: Optional subprocess runner (default: subprocess.run)

    Returns:
        List of findings with rule='SG102'
    """
    if runner is None:
        runner = subprocess.run

    if not paths:
        return []

    # This used to check the literal target 'specgate' whatever it was given.
    # Outside this repo mypy answered "Cannot read file", the parser below
    # found no `file:line:col: error:` lines in that, and L1 stayed green
    # without type-checking anything.
    with tempfile.TemporaryDirectory() as cache:
        cmd = [sys.executable, '-m', 'mypy', '--strict', '--show-column-numbers',
               '--cache-dir', cache] + paths
        try:
            result = runner(cmd, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError) as e:
            return [{'rule': 'SG102', 'file': '', 'line': 0, 'message': f'mypy error: {e}'}]

    if result.returncode == 0:
        return []

    findings = []
    for line in result.stdout.split('\n'):
        if not line.strip():
            continue
        # path/to/file.py:123:45: error: message (a drive letter's colon is allowed)
        match = re.match(r'^((?:[A-Za-z]:)?[^:]+):(\d+):\d+: error: (.+)$', line)
        if match:
            findings.append({
                'rule': 'SG102',
                'file': match.group(1),
                'line': int(match.group(2)),
                'message': match.group(3)
            })

    # A non-zero exit that names no file:line is a tool failure, not a pass.
    if not findings:
        detail = (result.stderr or result.stdout or '').strip()
        findings.append({'rule': 'SG102', 'file': '', 'line': 0,
                         'message': f'mypy exited {result.returncode}: {detail[:500]}'})

    return findings


# implements: AC-2
def check_vulture(paths: list[str], runner: Callable[..., Any] | None = None) -> list[dict[str, Any]]:
    """
    Run vulture on Python files with min-confidence 80.

    Args:
        paths: List of file paths to check
        runner: Optional subprocess runner (default: subprocess.run)

    Returns:
        List of findings with rule='SG103'
    """
    if runner is None:
        runner = subprocess.run

    if not paths:
        return []

    # Run vulture with min-confidence 80
    cmd = [sys.executable, '-m', 'vulture', '--min-confidence', '80'] + paths

    try:
        result = runner(cmd, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as e:
        return [{'rule': 'SG103', 'file': '', 'line': 0, 'message': f'vulture error: {e}'}]

    if result.returncode == 0:
        return []

    findings = []
    # Parse vulture output: path/to/file.py:123: unused variable 'foo' (90% confidence)
    for line in result.stdout.split('\n'):
        if not line.strip():
            continue
        # Match pattern: file.py:line: message
        match = re.match(r'^([^:]+):(\d+): (.+)$', line)
        if match:
            findings.append({
                'rule': 'SG103',
                'file': match.group(1),
                'line': int(match.group(2)),
                'message': match.group(3)
            })

    return findings


# implements: AC-2
def check_banned_tokens(paths: list[str]) -> list[dict[str, Any]]:
    """
    Check Python files for banned work-marker and test-disabling tokens.

    Files under a tests/fixtures directory are test data and are ignored.

    Args:
        paths: List of file paths to check

    Returns:
        List of findings with rule='SG104'
    """
    banned_pattern = _BANNED
    findings = []

    for path in paths:
        if _is_fixture(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except (OSError, UnicodeDecodeError) as e:
            findings.append({
                'rule': 'SG104',
                'file': path,
                'line': 0,
                'message': f'Error reading file: {e}'
            })
            continue

        for line_num, line in enumerate(lines, start=1):
            match = banned_pattern.search(line)
            if match:
                findings.append({
                    'rule': 'SG104',
                    'file': path,
                    'line': line_num,
                    'message': f"Banned token '{match.group(1)}' found"
                })

    return findings


# implements: AC-8
def check_markdownlint(md_paths: list[str], runner: Callable[..., Any] | None = None) -> list[dict[str, Any]]:
    """
    Run markdownlint-cli2 on Markdown files.

    Args:
        md_paths: List of markdown file paths to check
        runner: Optional subprocess runner (default: subprocess.run)

    Returns:
        List of findings with rule='SG105'
    """
    if runner is None:
        runner = subprocess.run

    if not md_paths:
        return []

    cmd = [npx(), '-y', 'markdownlint-cli2'] + md_paths

    try:
        result = runner(cmd, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as e:
        return [{'rule': 'SG105', 'file': '', 'line': 0, 'message': f'markdownlint error: {e}'}]

    if result.returncode == 0:
        return []

    # markdownlint-cli2 writes findings to stderr as
    # `file.md:3:81 error MD013/line-length Line length [...]`, with the column
    # and the severity word optional. This used to read stdout only, expect
    # `file:line:col MD001 msg`, and drop everything else -- exit 1, no
    # findings, L1 green: markdownlint had never failed a file.
    findings = []
    output = '\n'.join(s for s in (result.stdout, result.stderr) if isinstance(s, str) and s)
    for line in output.split('\n'):
        match = _MDL_LINE.match(line.strip())
        if match:
            findings.append({
                'rule': 'SG105',
                'file': match.group('file'),
                'line': int(match.group('line')),
                'message': f"{match.group('rule')}: {match.group('msg')}"
            })

    if not findings:
        findings.append({'rule': 'SG105', 'file': '', 'line': 0,
                         'message': f'markdownlint exited {result.returncode}: {output.strip()[:500]}'})

    return findings


def check(
    paths: list[str],
    md_paths: list[str] | None = None,
    runner: Callable[..., Any] | None = None,
    typed_paths: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Run all L1 static checks on Python and Markdown files.

    Args:
        paths: List of Python file paths to check
        md_paths: List of markdown file paths to check (optional)
        runner: Optional subprocess runner (default: subprocess.run)
        typed_paths: Files held to mypy --strict (default: all of `paths`).
            The CLI passes implementation files only: strict typing is a
            bar for the code an AC ships, not for the tests that exercise it.

    Returns:
        Combined list of findings from all checks
    """
    if runner is None:
        runner = subprocess.run

    if md_paths is None:
        md_paths = []

    findings = []

    # Run all checks
    if paths:
        findings.extend(check_ruff(paths, runner=runner))
        findings.extend(check_mypy(paths if typed_paths is None else typed_paths, runner=runner))
        findings.extend(check_vulture(paths, runner=runner))
        findings.extend(check_banned_tokens(paths))

    if md_paths:
        findings.extend(check_markdownlint(md_paths, runner=runner))

    return findings
