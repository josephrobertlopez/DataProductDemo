"""L6 debate layer for specgate.

L6 runs a prover and refuter to argue about a change, then feeds their outputs
to 3 judges who vote on findings. PASS unless majority veto whose file:line+claim
is confirmed by deterministic recheck; unconfirmed veto ignored+logged.

Rule IDs:
  - SG601: judge output invalid (non-JSON, or stop_reason != end_turn / truncated)
  - SG602: cache miss and no token available (env CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY)
  - SG603: confirmed majority veto (>=2 of 3 judges veto AND the veto's cited file:line+claim passes recheck)
"""

import hashlib
import json
import os
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any


def _command(claude_bin: str | Sequence[str]) -> list[str]:
    return [claude_bin] if isinstance(claude_bin, str) else list(claude_bin)


# implements: AC-7
def run_debate(
    change_dir: Path,
    inputs: dict[str, Any],
    recheck: Callable[[str, str, str], bool],
    claude_bin: str | Sequence[str] = "claude",
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run the L6 debate layer.

    Args:
        change_dir: Directory for debate cache and outputs
        inputs: Inputs to the debate (prover/refuter context)
        recheck: Function to verify a veto's file:line:claim (returns True if confirmed)
        claude_bin: Path to claude CLI binary, or a command prefix such as
            [sys.executable, "stub.py"] (an extensionless script cannot be
            executed directly on Windows)
        env: Optional environment dict (defaults to os.environ)

    Returns:
        {
            "findings": [{"rule": "SG6XX", "file": "...", "line": "...", "message": "..."}, ...],
            "ignored_vetoes": [{"file": "...", "line": "...", "claim": "..."}, ...],
            "calls": N  # total subprocess calls made
        }
    """
    if env is None:
        env = os.environ.copy()

    change_dir = Path(change_dir)
    change_dir.mkdir(parents=True, exist_ok=True)

    cache_file = change_dir / "debate.cache.json"
    cache_data = {}
    if cache_file.exists():
        try:
            cache_data = json.loads(cache_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cache_data = {}

    findings = []
    ignored_vetoes: list[dict[str, Any]] = []
    calls = 0

    # Build prompts for prover, refuter, and judges
    prover_prompt = _build_prover_prompt(inputs)
    refuter_prompt = _build_refuter_prompt(inputs)

    # Run prover
    prover_output, calls_prover, prover_error = _run_judge(
        claude_bin=claude_bin,
        prompt=prover_prompt,
        model="claude-sonnet-5-5",
        cache_data=cache_data,
        cache_file=cache_file,
        env=env,
    )
    calls += calls_prover

    if prover_error:
        findings.append({
            "rule": prover_error,
            "file": "",
            "line": 0,
            "message": f"Prover failed: {prover_error}"
        })
        return {"findings": findings, "ignored_vetoes": ignored_vetoes, "calls": calls}

    if not prover_output:
        findings.append({
            "rule": "SG601",
            "file": "",
            "line": 0,
            "message": "Prover output invalid"
        })
        return {"findings": findings, "ignored_vetoes": ignored_vetoes, "calls": calls}

    # Run refuter
    refuter_output, calls_refuter, refuter_error = _run_judge(
        claude_bin=claude_bin,
        prompt=refuter_prompt,
        model="claude-sonnet-5-5",
        cache_data=cache_data,
        cache_file=cache_file,
        env=env,
    )
    calls += calls_refuter

    if refuter_error:
        findings.append({
            "rule": refuter_error,
            "file": "",
            "line": 0,
            "message": f"Refuter failed: {refuter_error}"
        })
        return {"findings": findings, "ignored_vetoes": ignored_vetoes, "calls": calls}

    if not refuter_output:
        findings.append({
            "rule": "SG601",
            "file": "",
            "line": 0,
            "message": "Refuter output invalid"
        })
        return {"findings": findings, "ignored_vetoes": ignored_vetoes, "calls": calls}

    # Run 3 judges
    judge_results = []
    for i in range(3):
        judge_prompt = _build_judge_prompt(i, prover_output, refuter_output)
        judge_output, calls_judge, judge_error = _run_judge(
            claude_bin=claude_bin,
            prompt=judge_prompt,
            model="claude-sonnet-5-5",
            cache_data=cache_data,
            cache_file=cache_file,
            env=env,
        )
        calls += calls_judge

        if judge_error:
            findings.append({
                "rule": judge_error,
                "file": "",
                "line": 0,
                "message": f"Judge {i} failed: {judge_error}"
            })
            return {"findings": findings, "ignored_vetoes": ignored_vetoes, "calls": calls}

        judge_results.append(judge_output)

    # Process judge results: count vetoes, check if majority and confirmed
    veto_count = 0
    veto_details = None

    for judge_result in judge_results:
        if judge_result and judge_result.get("verdict") == "veto":
            veto_count += 1
            if veto_details is None:
                veto_details = judge_result

    # Check for majority veto (>=2 of 3)
    if veto_count >= 2 and veto_details:
        file = veto_details.get("file", "")
        line = veto_details.get("line", "")
        claim = veto_details.get("claim", "")

        if recheck(file, line, claim):
            # Confirmed veto
            findings.append({
                "rule": "SG603",
                "file": file,
                "line": line,
                "message": f"Confirmed majority veto: {claim}"
            })
        else:
            # Unconfirmed veto
            ignored_vetoes.append({
                "file": file,
                "line": line,
                "claim": claim,
                "reason": "Unconfirmed (veto claim did not pass recheck)"
            })
    elif veto_count == 1 and veto_details is not None:
        # Single judge veto - unconfirmed by definition
        ignored_vetoes.append({
            "file": veto_details.get("file", ""),
            "line": veto_details.get("line", ""),
            "claim": veto_details.get("claim", ""),
            "reason": "Single judge veto (not majority)"
        })

    return {
        "findings": findings,
        "ignored_vetoes": ignored_vetoes,
        "calls": calls,
    }


def _run_judge(
    claude_bin: str | Sequence[str],
    prompt: str,
    model: str,
    cache_data: dict[str, Any],
    cache_file: Path,
    env: dict[str, str],
) -> tuple[dict[str, Any] | None, int, str | None]:
    """Run a judge (or prover/refuter) and return parsed verdict, calls count, error rule.

    Returns:
        (parsed_verdict_dict or None, number_of_subprocess_calls, error_rule_or_None)
        where error_rule is None for success, "SG602" for cache miss with no token, "SG601" for other errors
    """
    # Compute cache key
    cache_key = hashlib.sha256(
        (prompt + model).encode()
    ).hexdigest()

    # Check cache
    if cache_key in cache_data:
        cached = cache_data[cache_key]
        return cached.get("verdict"), 0, None

    # Cache miss - check if we have required token
    has_token = "CLAUDE_CODE_OAUTH_TOKEN" in env or "ANTHROPIC_API_KEY" in env
    if not has_token:
        # SG602: cache miss and no token available
        return None, 0, "SG602"

    # Cache miss and we have token - run subprocess
    try:
        result = subprocess.run(
            [*_command(claude_bin), "-p", "--model", model, "--output-format", "json"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None, 1, "SG601"

    # Parse JSON envelope
    try:
        envelope = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return None, 1, "SG601"

    # Check stop_reason
    if envelope.get("stop_reason") != "end_turn":
        # SG601: truncation or other non-end_turn stop
        return None, 1, "SG601"

    # Parse result field (which is itself JSON)
    result_str = envelope.get("result", "")
    try:
        verdict = json.loads(result_str)
    except (json.JSONDecodeError, ValueError):
        # SG601: bad JSON in result field
        return None, 1, "SG601"

    # Cache the result
    cache_data[cache_key] = {"verdict": verdict}
    try:
        cache_file.write_text(json.dumps(cache_data, sort_keys=True), encoding="utf-8")
    except OSError:
        pass  # Ignore cache write failures

    return verdict, 1, None


def _build_prover_prompt(inputs: dict[str, Any]) -> str:
    """Build the prover prompt."""
    return f"""You are a prover arguing that a change is good.

Change: {inputs.get('change', '')}

Argue in favor of this change. Return JSON only:
{{"verdict": "pass", "file": "", "line": "", "claim": ""}}
"""


def _build_refuter_prompt(inputs: dict[str, Any]) -> str:
    """Build the refuter prompt."""
    return f"""You are a refuter arguing that a change is problematic.

Change: {inputs.get('change', '')}

Argue against this change if you can. Return JSON only:
{{"verdict": "pass|veto", "file": "...", "line": "...", "claim": "..."}}
"""


def _build_judge_prompt(judge_id: int, prover: dict[str, Any], refuter: dict[str, Any]) -> str:
    """Build a judge prompt."""
    return f"""You are judge {judge_id} evaluating a debate.

Prover argues: {json.dumps(prover)}
Refuter argues: {json.dumps(refuter)}

Decide: pass (change is good) or veto (change is bad).
Return JSON only:
{{"verdict": "pass|veto", "file": "...", "line": "...", "claim": "..."}}
"""
