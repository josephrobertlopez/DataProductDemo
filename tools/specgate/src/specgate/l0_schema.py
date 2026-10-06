"""L0 layer for PRD schema validation."""

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]

# A wheel install puts the schema inside the package (pyproject force-include);
# an editable install leaves it at the project root. Looking only at the root
# made every non-editable install fail L0 on every PRD.
_PACKAGED_SCHEMA = Path(__file__).parent / "schema" / "prd.schema.json"
_SOURCE_SCHEMA = Path(__file__).parent.parent.parent / "schema" / "prd.schema.json"
DEFAULT_SCHEMA = _PACKAGED_SCHEMA if _PACKAGED_SCHEMA.is_file() else _SOURCE_SCHEMA


def npx() -> str:
    """The npx executable. On Windows it is `npx.cmd`, which a bare "npx" never finds."""
    return shutil.which("npx") or "npx"


def _finding(rule: str, file: str, message: str) -> dict[str, Any]:
    return {"rule": rule, "file": file, "line": 0, "message": message}


def parse_prd(path: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse YAML frontmatter from a PRD file.

    Returns (frontmatter, error); error is None on success.
    """
    try:
        content = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"Could not read file: {exc}"

    if not content.startswith("---"):
        return None, "No frontmatter found"

    lines = content.split("\n")
    end_idx = next((i for i in range(1, len(lines)) if lines[i].startswith("---")), None)
    if end_idx is None:
        return None, "No closing --- found for frontmatter"

    try:
        frontmatter = yaml.safe_load("\n".join(lines[1:end_idx]))
    except yaml.YAMLError as exc:
        return None, f"Invalid YAML: {exc}"
    if frontmatter is None:
        return None, "Frontmatter is empty"
    if not isinstance(frontmatter, dict):
        return None, "Frontmatter is not a mapping"
    return frontmatter, None


# implements: AC-1
def check_prd(path: str, schema_path: str | None = None) -> list[dict[str, Any]]:
    """Check a PRD file against the schema and rules SG001-SG003, SG005."""
    try:
        schema = json.loads(Path(schema_path or DEFAULT_SCHEMA).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [_finding("SG005", path, f"Could not load schema: {exc}")]

    frontmatter, parse_error = parse_prd(path)
    if parse_error or frontmatter is None:
        return [_finding("SG005", path, parse_error or "Frontmatter is empty")]

    findings: list[dict[str, Any]] = []
    try:
        jsonschema.validate(instance=frontmatter, schema=schema)
    except jsonschema.ValidationError as exc:
        findings.append(_finding("SG001", path, f"Schema validation failed: {exc.message}"))

    seen: set[Any] = set()
    for i, ac in enumerate(frontmatter.get("acs") or []):
        if not isinstance(ac, dict):
            continue
        if "id" in ac:
            if ac["id"] in seen:
                findings.append(_finding("SG002", path, f"Duplicate AC ID: {ac['id']}"))
            seen.add(ac["id"])
        for field in ("given", "when", "then"):
            if not ac.get(field):
                findings.append(_finding("SG003", path, f"AC {i + 1} missing '{field}' field"))
        if not ac.get("tests"):
            findings.append(_finding("SG003", path, f"AC {i + 1} missing or empty 'tests' field"))
    return findings


def check_openspec(
    change: str, cwd: str, runner: Callable[..., Any] | None = None
) -> list[dict[str, Any]]:
    """Run `openspec validate <change> --strict`; SG004 on failure."""
    run = runner or subprocess.run
    try:
        result = run(
            [npx(), "-y", "@fission-ai/openspec@1.14.0", "validate", change, "--strict"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return [_finding("SG004", change, f"Could not run openspec: {exc}")]
    if result.returncode != 0:
        return [_finding(
            "SG004", change,
            f"openspec validation failed: {result.stderr or result.stdout}",
        )]
    return []


def check(
    prd_path: str, change: str | None = None, cwd: str | None = None
) -> list[dict[str, Any]]:
    """Run all L0 checks; SG004 only when both change and cwd are given."""
    findings = check_prd(prd_path)
    if change and cwd:
        findings.extend(check_openspec(change, cwd))
    return findings
