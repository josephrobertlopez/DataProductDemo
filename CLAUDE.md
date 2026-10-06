# DP Member 360

**Member 360 Data Product** — a daily batch pipeline that conforms eight microservice extracts into `DP_Member_360`, a one-row-per-member table published to the client's Unity Catalog. A single analyst consumes it to build a member-activity-by-segment dashboard.

**Input:** source tables and API connectors from client microservices.
**Output:** a pull request containing the pipeline code and the resulting data product.

This is a demo/training repo. It simulates a developer building a data product for a client from a PRD written by a product owner.

PRD and decision log: `_bmad-output/planning-artifacts/prds/prd-Member-360-Data-Product-2026-10-01/`

## Rules

- **Ask clarifying questions before starting complex work.** Don't infer requirements that a single question would settle.
- **Show the plan before executing.** Get agreement on approach before writing code.
- **Never output PII.** `data/raw/Member.csv` contains `first_name`, `last_name`, and `postal_code`. These must not appear in any data product, generated code sample, log, commit message, or chat response. The published product uses `age_band` and `home_state` instead — this is a deliberate control, not a convenience.
- **`data/raw/` is read-only.** It stands in for upstream microservices this project does not own. Never edit, clean, or regenerate it — if source data looks wrong, that is a finding to surface, not a file to fix.
- Check `openspec/changes/` for the relevant change before coding

## Gates (enforced, not just written down)

`data/raw/` read-only and no PII columns are now checks, alongside the spec and docs rules. They run on commit (lefthook) and on every PR (required checks `ci`, `spec-gate`, `attest`, `kt-docs`). See `docs/kt/left-shift-gates/README.md`.

- Commit with the venv active: `git commit` runs `python tools/leftshift/attest.py make` (gates, then a headless Claude review). Never `--no-verify`: CI's `attest` check fails without the local run.
- `.attestations/` is written by the harness, never by hand.
- Tests that create git repos must strip `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`.

## Structure

```
data/raw/        Source extracts standing in for microservice feeds (read-only)
data/product/    Data product output
_bmad/           BMAD framework — scripts, templates, config
_bmad-output/    Deliverables (planning-artifacts/prds/, party-mode/)
.claude/skills/  Workflow skills (BMAD + openspec)
openspec/        Spec scaffolding — present but not yet used
wiki/            Wiki scaffolding — present but not yet used
```

## Conventions

- Workflows are invoked as skills (`/bmad-prd`, `/bmad-architecture`, `/bmad-party-mode`). They write to `_bmad-output/`, not to the repo root.
- Each BMAD workflow run binds a dated folder and keeps a `.memlog.md` — an append-only decision log. Read it before continuing prior work; write decisions to it as they are made, via `_bmad/scripts/memlog.py`, never by hand.
- Config lives in `_bmad/bmm/config.yaml`. Resolve it with `_bmad/scripts/resolve_config.py` rather than parsing it directly.
