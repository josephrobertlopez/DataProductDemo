## ADDED Requirements

### Requirement: Raw data is read-only
The system SHALL fail when a change adds, edits, deletes or moves any file under `data/raw/`, comparing the staged index with HEAD locally and the branch with its base in CI. (AC-1)

#### Scenario: Edited raw file is blocked
- **WHEN** a staged change edits `data/raw/Member.csv`
- **THEN** the raw check fails and names `data/raw/Member.csv`

#### Scenario: Moving a file out of raw is not a loophole
- **WHEN** a file is moved from `data/raw/` to another directory
- **THEN** the raw check reports it as a deletion from `data/raw/`

### Requirement: No PII column names outside raw
The system SHALL fail when a tracked CSV outside `data/raw/` has a header column that, ignoring case and punctuation, names first_name, last_name, postal_code or a common respelling, and SHALL report only the file and column, never a value. (AC-2)

#### Scenario: Respelled PII column is caught
- **WHEN** `data/product/x.csv` has a header column `First Name`
- **THEN** the pii check fails naming `data/product/x.csv` and `First Name`

#### Scenario: Raw extracts are not checked
- **WHEN** `data/raw/Member.csv` has a `first_name` column
- **THEN** the pii check passes

### Requirement: Every requirement has a scenario
The system SHALL fail when an OpenSpec requirement has no `#### Scenario:`, because `openspec validate --strict` accepts such a spec. (AC-3)

#### Scenario: Requirement with no scenario
- **WHEN** a spec has a `### Requirement:` followed by no `#### Scenario:` before the next heading
- **THEN** the scenarios check fails naming the spec, the line and the requirement

### Requirement: Pull requests leave a map
The system SHALL fail a pull request that adds, changes or renames no file under `docs/kt/`. (AC-4)

#### Scenario: Code-only pull request
- **WHEN** a pull request changes only files outside `docs/kt/`
- **THEN** the kt-docs check fails

### Requirement: The local run is recorded
The system SHALL, as the last pre-commit step, run the gates and stop at the first red one; SHALL refuse, with no attestation, a change too large to review rather than truncate it; when all pass, it SHALL run a headless Claude review and record gates, review and a digest of the staged tree as an A2A v1.0 Task, staging it only when no HIGH finding's evidence matches the code. (AC-5)

#### Scenario: Red gate stops the review
- **WHEN** a gate exits non-zero
- **THEN** no model is called and no attestation is written

#### Scenario: Verified HIGH finding fails the commit
- **WHEN** the review reports a HIGH finding whose file, line and quote match the staged code
- **THEN** the attestation is written with state TASK_STATE_FAILED and is not staged

### Requirement: The model never sees data
The system SHALL build the review prompt only from staged files outside `data/`, excluding every CSV in any letter case, `.attestations/` and the vendored gate. (AC-6)

#### Scenario: Staged data is withheld
- **WHEN** files under `data/` and CSV files are staged alongside code
- **THEN** the prompt contains the code and none of their content

### Requirement: CI rejects a missing or stale local run
The system SHALL fail the `attest` check, without calling a model, when the attestation is missing, does not match the commit's tree, is not a completed A2A v1.0 Task with verdict pass, lacks a required gate, records a red gate, or holds a verified HIGH or no-longer-matching finding. (AC-7)

#### Scenario: Hooks were skipped
- **WHEN** a commit is made with the hooks disabled and no attestation exists
- **THEN** the attest check fails saying the local run did not happen

#### Scenario: Code changed after attesting
- **WHEN** a file changes after the attestation was made
- **THEN** the attest check fails as stale
