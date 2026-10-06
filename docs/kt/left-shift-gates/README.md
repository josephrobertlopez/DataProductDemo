# Left-shift gates: knowledge transfer

## What this is

The repo's rules used to live only in `CLAUDE.md`: `data/raw/` is read-only, PII never leaves it, and every requirement is specified and tested. Now each rule is a check. It runs on your machine before the commit (lefthook), and again on GitHub before the merge, with the same command in both places.

| Required check | Question | Local | GitHub |
|---|---|---|---|
| `ci` | Does it still work, and is the data safe? | pre-commit | Linux + Windows |
| `spec-gate` | Did you build what the spec promised? | pre-commit L0-L2, pre-push L3-L5 | L0-L5 |
| `attest` | Did the local run, including the Claude review, happen on this exact tree? | pre-commit (writes it) | verifies it, never calls a model |
| `kt-docs` | Did you leave the next person a map? | pre-push | every PR |

## Setup, once

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python -m pip install "./tools/specgate[dev]"
```

On macOS or Linux the interpreter is `.venv/bin/python`. Then install the hooks; this needs lefthook (`winget install evilmartians.lefthook`, or `brew install lefthook`):

```bash
lefthook install
```

You also need Node 22 (for `npx`) and Claude Code logged in (`claude auth login`). The attestation calls `claude -p`.

## What happens on commit

With the venv active, `git commit` runs `python tools/leftshift/attest.py make`, which:

1. runs the gates in order and stops at the first red one: `raw`, `pii`, `openspec`, `scenarios`, `specgate` (L0-L2; also regenerates and stages each `trace.json`), `unittest`;
2. if all pass, sends the staged files, minus `data/`, CSVs, `.attestations/` and the vendored `tools/specgate/`, to a headless Claude review with no tools;
3. re-checks every finding's file, line and quote against the code. A finding that points at nothing is ignored;
4. fails if any HIGH finding checks out. Otherwise it writes `.attestations/attestation.a2a.json` (an A2A v1.0 Task) and stages it into the same commit.

## What CI checks

`attest verify` fails when the attestation is:

- missing (the hooks were skipped);
- stale (code changed after the review);
- not `TASK_STATE_COMPLETED` with verdict `pass`;
- missing a gate, or recording a red one;
- holding a HIGH finding that still matches the code.

It never calls a model.

## Gotchas

- **Rebasing makes the attestation stale.** The digest covers the whole tree, so commit again (or `git commit --amend`) to re-attest.
- **`attest` proves the run happened; it does not prove honesty.** The digest is a public function of the tree, so a determined person could hand-write the JSON. That is why `.attestations/` is in `CODEOWNERS`.
- **`openspec validate --strict` accepts a requirement with no scenario.** We checked against a real change, so `checks.py scenarios` enforces it instead.
- **Vendored specgate.** `tools/specgate/` is a vendored copy (see `VENDORED.md`). Its `.specgate-skip` keeps its own `# implements:` markers out of this repo's trace. Fix specgate upstream, then re-copy it.
- **Tests that create git repos must strip `GIT_DIR`, `GIT_WORK_TREE` and `GIT_INDEX_FILE`.** Inside a hook those point at this repo.

## Run CI locally

`act` runs the workflows in Docker; with colima, run it inside WSL. The kt-docs check reads the changed files from the event payload:

```bash
act pull_request -W .github/workflows/kt-docs.yml -e .github/act-events/pr-docs-kt.json -P ubuntu-24.04=catthehacker/ubuntu:act-latest
```

```bash
act pull_request -W .github/workflows/kt-docs.yml -e .github/act-events/pr-code-only.json -P ubuntu-24.04=catthehacker/ubuntu:act-latest
```

The first should pass and the second should fail.
