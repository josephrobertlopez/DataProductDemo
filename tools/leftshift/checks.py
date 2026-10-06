"""Left-shift checks for this repo: one script, run by lefthook and by CI.

The same command runs on a laptop before the commit and on GitHub after the
push, so a local pass and a CI pass mean the same thing. Each check prints
what it looked at; a check that prints nothing cannot be told from one that
checked nothing.

    python tools/leftshift/checks.py raw --staged | --base origin/main
    python tools/leftshift/checks.py pii --rev index | --rev HEAD
    python tools/leftshift/checks.py scenarios
    python tools/leftshift/checks.py kt-docs --base origin/main
    python tools/leftshift/checks.py specgate --layers L0-L2 --stage | --layers L3-L5
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

RAW = "data/raw"
KT = "docs/kt/"

# Compared after lowercasing and dropping everything but letters and digits,
# so `First Name`, `firstName` and `first_name` are one name. CLAUDE.md names
# the first three; the rest are the obvious respellings of the same fields.
PII_COLUMNS = frozenset({
    "firstname", "lastname", "postalcode",
    "fname", "lname", "givenname", "surname", "fullname",
    "zip", "zipcode", "postcode",
})


def git(repo: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return result.stdout


# implements: AC-1
def raw_changes(repo: str, base: str | None) -> list[str]:
    """`<status>\\t<path>` for every difference under data/raw.

    Against `base`, the changes this branch made since it left base; without
    it, the staged index against HEAD. --no-renames makes a file moved out of
    data/raw show up as a deletion instead of disappearing from the diff.
    """
    span = [f"{base}...HEAD"] if base else ["--cached", "HEAD"]
    out = git(repo, "diff", "--name-status", "--no-renames", *span, "--", RAW)
    return [line for line in out.splitlines() if line.strip()]


def _normalise(column: str) -> str:
    return re.sub(r"[^a-z0-9]", "", column.lower())


def _tracked(repo: str, rev: str) -> list[str]:
    if rev == "index":
        listing = git(repo, "ls-files", "-z")
    else:
        listing = git(repo, "ls-tree", "-r", "--name-only", "-z", rev)
    return [p for p in listing.split("\0") if p]


# implements: AC-2
def pii_columns(repo: str, rev: str) -> list[tuple[str, str]]:
    """(path, column) for each CSV header outside data/raw naming a PII field.

    Reads the header row only and reports the column name only: the check
    must never print a value, because the values are what it protects.
    """
    hits: list[tuple[str, str]] = []
    for path in _tracked(repo, rev):
        if not path.lower().endswith(".csv") or path.startswith(RAW + "/"):
            continue
        content = git(repo, "show", f"{'' if rev == 'index' else rev}:{path}")
        header = next(csv.reader(io.StringIO(content.lstrip("﻿"))), [])
        hits.extend((path, col.strip()) for col in header if _normalise(col) in PII_COLUMNS)
    return hits


# implements: AC-3
def missing_scenarios(repo: str) -> list[tuple[str, int, str]]:
    """(spec, line, requirement) for each `### Requirement:` with no `#### Scenario:`.

    `openspec validate --strict` accepts a change whose requirements have no
    scenarios at all, so this is checked here instead of trusted to it.
    """
    missing: list[tuple[str, int, str]] = []
    root = Path(repo)
    for spec in sorted((root / "openspec").rglob("spec.md")):
        rel = spec.relative_to(root).as_posix()
        current: tuple[int, str] | None = None
        has_scenario = False
        for number, line in enumerate(spec.read_text(encoding="utf-8").splitlines(), start=1):
            if line.startswith("#### Scenario:"):
                has_scenario = True
            elif line.startswith(("### ", "## ")):
                if current is not None and not has_scenario:
                    missing.append((rel, current[0], current[1]))
                current = None
                if line.startswith("### Requirement:"):
                    current, has_scenario = (number, line[len("### Requirement:"):].strip()), False
        if current is not None and not has_scenario:
            missing.append((rel, current[0], current[1]))
    return missing


def changed_files(repo: str, base: str) -> list[str]:
    """Files this branch added, modified or renamed since it left base.

    Under act there is no real base branch; the event payload may list the
    files instead, as in .github/act-events/.
    """
    event = os.environ.get("GITHUB_EVENT_PATH")
    if os.environ.get("ACT") == "true" and event:
        payload = json.loads(Path(event).read_text(encoding="utf-8"))
        if "act_changed_files" in payload:
            return [str(p) for p in payload["act_changed_files"]]
    out = git(repo, "diff", "--name-only", "--diff-filter=AMR", f"{base}...HEAD")
    return [p for p in out.splitlines() if p]


# implements: AC-4
def kt_docs(changed: list[str]) -> list[str]:
    """The changed files under docs/kt/; the check fails when there are none."""
    return [p for p in changed if p.startswith(KT)]


def run_specgate(repo: str, layers: str, stage: bool) -> int:
    """specgate on every change with a prd.md; with `stage`, write and stage its trace.json.

    Staging the trace here, before the attestation's digest is taken, is what
    keeps a regenerated trace from making a fresh attestation stale.
    """
    with tempfile.TemporaryDirectory() as scratch:
        for prd in sorted(Path(repo, "openspec", "changes").glob("*/prd.md")):
            change = prd.parent.relative_to(repo).as_posix()
            out = f"{change}/trace.json" if stage else str(Path(scratch, f"{prd.parent.name}.json"))
            sys.stdout.write(f"specgate {layers} {change}\n")
            sys.stdout.flush()
            code = subprocess.run([sys.executable, "-m", "specgate", "check", "--change", change,
                                   "--layers", layers, "--output", out], cwd=repo, check=False).returncode
            if code != 0:
                return code
            if stage:
                git(repo, "add", out)
    return 0


def _report(ok: bool, good: str, bad: str, details: list[str]) -> int:
    if ok:
        sys.stdout.write(f"ok: {good}\n")
        return 0
    sys.stderr.write(f"FAIL: {bad}\n")
    for line in details:
        sys.stderr.write(f"  {line}\n")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="checks.py")
    parser.add_argument("--repo", default=".")
    sub = parser.add_subparsers(dest="check", required=True)
    raw = sub.add_parser("raw")
    raw.add_argument("--base")
    raw.add_argument("--staged", action="store_true")
    pii = sub.add_parser("pii")
    pii.add_argument("--rev", default="HEAD")
    sub.add_parser("scenarios")
    kt = sub.add_parser("kt-docs")
    kt.add_argument("--base", required=True)
    gate = sub.add_parser("specgate")
    gate.add_argument("--layers", default="L0-L2")
    gate.add_argument("--stage", action="store_true")
    args = parser.parse_args(argv)
    repo: str = args.repo

    if args.check == "specgate":
        return run_specgate(repo, args.layers, args.stage)
    if args.check == "raw":
        if not args.staged and not args.base:
            parser.error("raw needs --staged or --base")
        diff = raw_changes(repo, None if args.staged else args.base)
        where = "staged" if args.staged else f"since {args.base}"
        return _report(not diff, f"{RAW} unchanged ({where})",
                       f"{RAW} is read-only and was changed ({where})", diff)
    if args.check == "pii":
        hits = pii_columns(repo, args.rev)
        return _report(not hits, f"no PII column names in CSV headers outside {RAW} ({args.rev})",
                       "PII column names in CSV headers", [f"{p}: {c}" for p, c in hits])
    if args.check == "scenarios":
        gaps = missing_scenarios(repo)
        return _report(not gaps, "every OpenSpec requirement has a scenario",
                       "requirements with no '#### Scenario:'", [f"{s}:{n} {t}" for s, n, t in gaps])
    docs = kt_docs(changed_files(repo, args.base))
    return _report(bool(docs), f"KT doc changed: {', '.join(docs)}",
                   f"the PR must add or change a file under {KT}", [])


if __name__ == "__main__":
    sys.exit(main())
