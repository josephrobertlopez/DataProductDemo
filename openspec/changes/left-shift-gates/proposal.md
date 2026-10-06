## Why

The rules that protect this repo are written down but not enforced: `data/raw/` is read-only, PII never leaves it, and every requirement is specified and tested. A rule that costs nothing to break gets broken. Left-shift makes breaking one cost something at the moment it happens, on the author's machine, and again at merge time where it cannot be skipped.

## What Changes

- Add `tools/leftshift/checks.py`: the raw, pii, scenarios and kt-docs checks, one stdlib script run by both lefthook and CI.
- Add `tools/leftshift/attest.py`: runs the gates, then a headless `claude -p` review, and records both as an A2A v1.0 Task in `.attestations/attestation.a2a.json`; `verify` checks it in CI without calling a model.
- Vendor specgate under `tools/specgate/` (with `.specgate-skip`) so the spec gate runs with no fetch of the gate itself.
- Add four required checks: `ci`, `spec-gate`, `attest`, `kt-docs`; add `lefthook.yml` running the same commands locally.

## Capabilities

### New Capabilities

- `left-shift-gates`: local and CI enforcement of raw-data immutability, PII column names, OpenSpec completeness, knowledge-transfer docs, and a recorded agent review bound to the committed tree.

## Impact

- New: `tools/leftshift/`, `tools/specgate/`, `tests/`, `.github/workflows/`, `lefthook.yml`, `docs/kt/left-shift-gates/`.
- Contributors need Python 3.12+, Node 22 and a logged-in Claude Code CLI to commit with the hooks; CI needs none of the model access.
- Out of scope: the Member 360 pipeline and its runtime data-quality checks.
