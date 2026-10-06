## 1. Checks

- [x] 1.1 raw: `data/raw` unchanged against the index or the base branch (AC-1)
- [x] 1.2 pii: no PII column names in CSV headers outside `data/raw` (AC-2)
- [x] 1.3 scenarios: every OpenSpec requirement has a scenario (AC-3)
- [x] 1.4 kt-docs: the pull request changes a file under `docs/kt/` (AC-4)

## 2. Attestation

- [x] 2.1 `attest make`: gates, then headless review, then an A2A v1.0 Task (AC-5)
- [x] 2.2 The review prompt withholds data, CSVs, attestations and the vendored gate (AC-6)
- [x] 2.3 `attest verify`: model-free check in CI (AC-7)

## 3. Wiring

- [x] 3.1 `lefthook.yml` pre-commit and pre-push
- [x] 3.2 Workflows `ci`, `spec-gate` (with job `attest`), `kt-docs`
- [x] 3.3 Knowledge-transfer doc `docs/kt/left-shift-gates/README.md`
