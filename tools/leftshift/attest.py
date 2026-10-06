"""Attest that the left-shift gates and a headless Claude review ran on this exact tree.

    python tools/leftshift/attest.py make      # pre-commit, last step: gates, then claude -p
    python tools/leftshift/attest.py verify    # CI and pre-push: never calls a model

The attestation is an A2A v1.0 Task (package lf.a2a.v1, proto3 JSON) written
to .attestations/attestation.a2a.json and committed with the change. It binds
three things to a digest of the tree: which gates ran and how they exited,
what the reviewer found, and the verdict.

What it is and is not: proof that the local run happened on this tree, so a
commit made with the hooks skipped cannot go green. It is not proof against a
person who hand-writes the JSON -- the digest is a public function of the
tree. Review of .attestations/ (CODEOWNERS) is the control for that.

Verdict rule, deterministic: fail if and only if a HIGH finding's evidence
(file, line, quote) matches the code. A finding that points at nothing is
recorded and ignored, so the model can point at problems but never decide.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ATTESTATION = ".attestations/attestation.a2a.json"
A2A_VERSION = "1.0.0"
TASK_STATES = frozenset({
    "TASK_STATE_SUBMITTED", "TASK_STATE_WORKING", "TASK_STATE_COMPLETED", "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED", "TASK_STATE_INPUT_REQUIRED", "TASK_STATE_REJECTED",
    "TASK_STATE_AUTH_REQUIRED",
})
# Never shown to the model: data is PII or stands in for systems we do not own,
# the attestation is our own output, and the vendored gate is not this change.
# icase: a plain `*.csv` exclude let `Export.CSV` through to the model -- found by
# this script's own review on its first real run.
PROMPT_EXCLUDES = (":(exclude,icase)data/**", ":(exclude,icase)*.csv", ":(exclude).attestations/**",
                   ":(exclude)tools/specgate/**")
PROMPT_LIMIT = 200_000
NPX = shutil.which("npx") or "npx"
GATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("raw", (sys.executable, str(HERE / "checks.py"), "raw", "--staged")),
    ("pii", (sys.executable, str(HERE / "checks.py"), "pii", "--rev", "index")),
    ("openspec", (NPX, "-y", "@fission-ai/openspec@1.14.0", "validate", "--all", "--strict",
                  "--no-interactive")),
    ("scenarios", (sys.executable, str(HERE / "checks.py"), "scenarios")),
    ("specgate", (sys.executable, str(HERE / "checks.py"), "specgate", "--layers", "L0-L2", "--stage")),
    ("unittest", (sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", ".")),
)
REQUIRED_GATES = tuple(name for name, _ in GATES)
VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "summary", "findings"],
    "properties": {
        "verdict": {"enum": ["pass", "fail"]},
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["severity", "ac", "claim", "file", "line", "quote"],
            "properties": {
                "severity": {"enum": ["HIGH", "MEDIUM", "LOW"]},
                "ac": {"type": "string"},
                "claim": {"type": "string"},
                "file": {"type": "string"},
                "line": {"type": "integer"},
                "quote": {"type": "string"},
            },
        }},
    },
}

Runner = Callable[..., "subprocess.CompletedProcess[str]"]
Ask = Callable[[str], dict[str, Any]]


def git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout


def _entries(listing: str, index: bool) -> list[tuple[str, str, str]]:
    """(mode, object, path) from `ls-files -s -z` or `ls-tree -r -z`, the same either way."""
    found = []
    for record in filter(None, listing.split("\0")):
        meta, path = record.split("\t", 1)
        fields = meta.split()
        found.append((fields[0], fields[1] if index else fields[2], path))
    return found


def tree_digest(entries: list[tuple[str, str, str]]) -> str:
    """sha256 of the tree minus .attestations/, which cannot contain its own digest."""
    lines = sorted(f"{m} {o} {p}" for m, o, p in entries if not p.startswith(".attestations/"))
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def staged_digest(repo: str) -> str:
    return tree_digest(_entries(git(repo, "ls-files", "-s", "-z"), index=True))


def commit_digest(repo: str, rev: str) -> str:
    return tree_digest(_entries(git(repo, "ls-tree", "-r", "-z", rev), index=False))


def run_gates(repo: str, run: Runner = subprocess.run) -> list[dict[str, Any]]:
    """Run each gate in order and stop at the first red one."""
    results: list[dict[str, Any]] = []
    for name, cmd in GATES:
        sys.stdout.write(f"gate {name}\n")
        proc = run(list(cmd), cwd=repo, capture_output=True, text=True, encoding="utf-8",
                   errors="replace", check=False)
        sys.stdout.write((proc.stdout or "") + (proc.stderr or ""))
        results.append({"name": name, "exit": proc.returncode})
        if proc.returncode != 0:
            break
    return results


# implements: AC-6
def review_files(repo: str) -> dict[str, str]:
    """Staged files the reviewer may see, with their staged content.

    Built from a pathspec that excludes data/ and every CSV, so PII cannot
    reach the model by any route this function knows about.
    """
    names = git(repo, "diff", "--cached", "--name-only", "--diff-filter=AMR", "-z",
                "--", ".", *PROMPT_EXCLUDES)
    return {p: git(repo, "show", f":{p}") for p in filter(None, names.split("\0"))}


def build_prompt(files: dict[str, str], acceptance: str, gates: list[dict[str, Any]]) -> str:
    parts = ["## Acceptance criteria (from the PRD frontmatter)", acceptance,
             "## Gates that ran on this tree", json.dumps(gates),
             "## Staged files, with line numbers"]
    for path, text in sorted(files.items()):
        numbered = "\n".join(f"{n:>5}  {line}" for n, line in enumerate(text.splitlines(), start=1))
        parts.append(f"=== {path} ===\n{numbered}")
    return "\n\n".join(parts)


def acceptance_text(repo: str) -> str:
    prds = sorted(Path(repo, "openspec", "changes").glob("*/prd.md"))
    return "\n\n".join(f"### {p.parent.name}\n{p.read_text(encoding='utf-8').split('---', 2)[1]}"
                       for p in prds if p.read_text(encoding="utf-8").startswith("---"))


def resolve_claude() -> str:
    """The real claude binary: the Windows .cmd/.ps1 shims mangle braces, quotes and newlines."""
    found = shutil.which("claude")
    if not found:
        return "claude"
    path = Path(found)
    real = path.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    return str(real) if path.suffix.lower() in (".cmd", ".bat", ".ps1") and real.exists() else found


def claude_command(model: str) -> list[str]:
    # --bare refuses OAuth logins; --restricted keeps an OAuth login working while
    # still dropping code-running tools and the user's settings files.
    auth = ["--bare"] if os.environ.get("ANTHROPIC_API_KEY") else ["--restricted"]
    return [resolve_claude(), "-p", "--output-format", "json",
            "--json-schema", json.dumps(VERDICT_SCHEMA),
            "--system-prompt", (HERE / "attest_prompt.md").read_text(encoding="utf-8"),
            "--tools", "", "--strict-mcp-config", "--no-session-persistence",
            "--model", model, "--max-budget-usd", "0.50", *auth]


def ask_claude(prompt: str, run: Runner = subprocess.run) -> dict[str, Any]:
    """One headless review. Runs in an empty temp dir so no CLAUDE.md or file is in reach."""
    model = os.environ.get("LEFTSHIFT_ATTEST_MODEL", "sonnet")
    with tempfile.TemporaryDirectory() as empty:
        proc = run(claude_command(model), input=prompt, cwd=empty, capture_output=True,
                   text=True, encoding="utf-8", errors="replace", check=False, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p exited {proc.returncode}: {(proc.stderr or proc.stdout)[-800:]}")
    envelope = json.loads(proc.stdout)
    review = envelope.get("structured_output") or json.loads(envelope.get("result") or "{}")
    return {"review": review, "model": next(iter(envelope.get("modelUsage") or {}), model),
            "sessionId": envelope.get("session_id", ""), "costUsd": envelope.get("total_cost_usd", 0)}


def evidence_holds(repo: str, rev: str, finding: dict[str, Any]) -> bool:
    """True when `quote` is on line `line` of `file` at rev ('' for the index)."""
    try:
        lines = git(repo, "show", f"{rev}:{finding['file']}").splitlines()
    except (subprocess.CalledProcessError, KeyError):
        return False
    line, quote = finding.get("line", 0), str(finding.get("quote", ""))
    return isinstance(line, int) and 0 < line <= len(lines) and bool(quote) and quote in lines[line - 1]


def build_task(subject: str, gates: list[dict[str, Any]], answer: dict[str, Any],
               verified: list[dict[str, Any]], unverified: list[dict[str, Any]],
               prompt: str) -> dict[str, Any]:
    review = answer["review"]
    passed = not any(f["severity"] == "HIGH" for f in verified)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return {
        "id": f"att-{uuid.uuid4()}",
        "contextId": "left-shift",
        "status": {
            "state": "TASK_STATE_COMPLETED" if passed else "TASK_STATE_FAILED",
            "timestamp": now,
            "message": {"messageId": f"msg-{uuid.uuid4()}", "role": "ROLE_AGENT", "parts": [
                {"text": f"verdict: {'pass' if passed else 'fail'} "
                         f"({sum(f['severity'] == 'HIGH' for f in verified)} verified HIGH findings)"}]},
        },
        "artifacts": [{"artifactId": "attestation", "name": "left-shift attestation", "parts": [{
            "mediaType": "application/json",
            "data": {
                "subject": {"algo": "sha256", "digest": subject, "excludes": [".attestations/"]},
                "gates": gates,
                "evaluator": {"model": answer["model"], "sessionId": answer["sessionId"],
                              "costUsd": answer["costUsd"]},
                "promptSha256": prompt_sha,
                "modelVerdict": review.get("verdict", ""),
                "summary": review.get("summary", ""),
                "verdict": "pass" if passed else "fail",
                "findings": {"verified": verified, "unverified": unverified},
            },
        }]}],
        "history": [{"messageId": f"msg-{uuid.uuid4()}", "role": "ROLE_USER", "parts": [
            {"text": f"review the staged change against its acceptance criteria; prompt sha256 {prompt_sha}"}]}],
        "metadata": {"a2aVersion": A2A_VERSION, "producer": "tools/leftshift/attest.py"},
    }


# implements: AC-5
def make(repo: str, run: Runner = subprocess.run, ask: Ask = ask_claude) -> int:
    """Gates first, then the review; write and stage the attestation only on a pass."""
    gates = run_gates(repo, run)
    if any(g["exit"] != 0 for g in gates):
        sys.stderr.write(f"attest: gate {gates[-1]['name']} is red; no review, no attestation\n")
        return 1
    prompt = build_prompt(review_files(repo), acceptance_text(repo), gates)
    if len(prompt) > PROMPT_LIMIT:
        sys.stderr.write(f"attest: change too large to review ({len(prompt)} chars > {PROMPT_LIMIT})\n")
        return 1
    answer = ask(prompt)
    findings = list(answer["review"].get("findings") or [])
    verified = [f for f in findings if evidence_holds(repo, "", f)]
    unverified = [f for f in findings if f not in verified]
    task = build_task(staged_digest(repo), gates, answer, verified, unverified, prompt)
    out = Path(repo, ATTESTATION)
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(task, handle, indent=2, sort_keys=True)
        handle.write("\n")
    data = task["artifacts"][0]["parts"][0]["data"]
    for f in unverified:
        sys.stderr.write(f"attest: ignored (evidence does not match): {f.get('file')}:{f.get('line')} "
                         f"{f.get('claim')}\n")
    if data["verdict"] != "pass":
        for f in verified:
            sys.stderr.write(f"attest: {f['severity']} {f['file']}:{f['line']} {f['claim']}\n")
        sys.stderr.write(f"attest: FAIL, see {ATTESTATION} (not staged)\n")
        return 1
    git(repo, "add", ATTESTATION)
    sys.stdout.write(f"attest: pass, {ATTESTATION} staged (digest {data['subject']['digest'][:12]}, "
                     f"{len(unverified)} unverified findings ignored)\n")
    return 0


def _shape_problems(task: Any) -> list[str]:
    if not isinstance(task, dict):
        return ["attestation is not a JSON object"]
    problems = []
    if not isinstance(task.get("id"), str) or not task["id"]:
        problems.append("Task.id missing")
    if (task.get("metadata") or {}).get("a2aVersion") != A2A_VERSION:
        problems.append(f"metadata.a2aVersion is not {A2A_VERSION}")
    if (task.get("status") or {}).get("state") not in TASK_STATES:
        problems.append("status.state is not an A2A v1.0 TaskState")
    try:
        if not isinstance(task["artifacts"][0]["parts"][0]["data"], dict):
            problems.append("artifacts[0].parts[0].data is not an object")
    except (KeyError, IndexError, TypeError):
        problems.append("no artifacts[0].parts[0].data")
    return problems


# implements: AC-7
def verify(repo: str, rev: str = "HEAD") -> list[str]:
    """Every reason the committed attestation does not vouch for rev; [] when it does."""
    try:
        task = json.loads(git(repo, "show", f"{rev}:{ATTESTATION}"))
    except subprocess.CalledProcessError:
        return [f"no {ATTESTATION} at {rev}: the local left-shift run (lefthook pre-commit) did not happen"]
    except json.JSONDecodeError as exc:
        return [f"{ATTESTATION} is not JSON: {exc}"]
    problems = _shape_problems(task)
    if problems:
        return problems
    data = task["artifacts"][0]["parts"][0]["data"]
    if task["status"]["state"] != "TASK_STATE_COMPLETED":
        problems.append(f"status.state is {task['status']['state']}")
    if data.get("verdict") != "pass":
        problems.append(f"verdict is {data.get('verdict')!r}")
    if (data.get("subject") or {}).get("digest") != commit_digest(repo, rev):
        problems.append("stale: the tree changed after the attestation was made; re-run the commit hook")
    gates = data.get("gates") or []
    if [g.get("name") for g in gates] != list(REQUIRED_GATES):
        problems.append(f"gates recorded {[g.get('name') for g in gates]}, required {list(REQUIRED_GATES)}")
    problems.extend(f"gate {g.get('name')} exited {g.get('exit')}" for g in gates if g.get("exit") != 0)
    verified = (data.get("findings") or {}).get("verified") or []
    problems.extend(f"HIGH finding: {f.get('file')}:{f.get('line')} {f.get('claim')}"
                    for f in verified if f.get("severity") == "HIGH")
    problems.extend(f"verified finding no longer matches: {f.get('file')}:{f.get('line')}"
                    for f in verified if not evidence_holds(repo, rev, f))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="attest.py")
    parser.add_argument("--repo", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("make")
    check = sub.add_parser("verify")
    check.add_argument("--rev", default="HEAD")
    args = parser.parse_args(argv)
    if args.command == "make":
        return make(args.repo)
    problems = verify(args.repo, args.rev)
    if problems:
        for p in problems:
            sys.stderr.write(f"attest verify: FAIL: {p}\n")
        return 1
    sys.stdout.write(f"attest verify: OK digest={commit_digest(args.repo, args.rev)[:12]} "
                     f"gates={','.join(REQUIRED_GATES)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
