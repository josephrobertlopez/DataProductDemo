---
feature: left-shift-gates
acs:
  - id: AC-1
    given: "a change that adds, edits, deletes or moves a file under data/raw"
    when: "the raw check runs on the staged index, or on the branch against its base"
    then: "it fails naming each changed path, and passes when data/raw is untouched"
    tests:
      - "test_leftshift.TestRawIsReadOnly.test_unchanged_raw_is_clean"
      - "test_leftshift.TestRawIsReadOnly.test_staged_edit_is_reported"
      - "test_leftshift.TestRawIsReadOnly.test_moving_a_file_out_of_raw_is_a_deletion_not_a_rename"
      - "test_leftshift.TestRawIsReadOnly.test_branch_against_base"
  - id: AC-2
    given: "a tracked CSV outside data/raw whose header names first_name, last_name, postal_code or a respelling of them"
    when: "the pii check runs on the index or on a commit"
    then: "it fails naming the file and the column, and never prints a value from the file"
    tests:
      - "test_leftshift.TestNoPiiColumns.test_raw_and_non_csv_files_are_not_checked"
      - "test_leftshift.TestNoPiiColumns.test_respelled_pii_columns_are_caught_in_the_index"
      - "test_leftshift.TestNoPiiColumns.test_committed_csv_is_checked_at_a_revision"
      - "test_leftshift.TestNoPiiColumns.test_unstaged_edit_does_not_count_for_the_index"
  - id: AC-3
    given: "an OpenSpec spec with a Requirement that has no Scenario"
    when: "the scenarios check runs"
    then: "it fails naming the spec, line and requirement, because openspec validate --strict accepts it"
    tests:
      - "test_leftshift.TestEveryRequirementHasAScenario.test_requirements_without_scenarios_are_listed"
      - "test_leftshift.TestEveryRequirementHasAScenario.test_a_complete_spec_is_clean"
  - id: AC-4
    given: "a pull request"
    when: "the kt-docs check runs"
    then: "it fails unless the pull request adds, changes or renames a file under docs/kt/"
    tests:
      - "test_leftshift.TestKtDocs.test_only_files_under_docs_kt_count"
  - id: AC-5
    given: "a commit being made with the lefthook pre-commit hook"
    when: "attest make runs after the other gates"
    then: "a red gate stops it before any model call; a change too large to review is refused with no attestation, never truncated; otherwise a headless Claude review is recorded as an A2A v1.0 Task bound to the staged tree, staged only when no verified HIGH finding exists"
    tests:
      - "test_leftshift.TestMake.test_green_gates_and_clean_review_write_a_staged_a2a_task"
      - "test_leftshift.TestMake.test_a_red_gate_stops_before_the_review"
      - "test_leftshift.TestMake.test_a_verified_high_finding_fails_and_is_not_staged"
      - "test_leftshift.TestMake.test_a_high_finding_that_points_at_nothing_is_ignored"
      - "test_leftshift.TestMake.test_an_oversized_change_is_refused_not_truncated"
  - id: AC-6
    given: "staged files under data/, CSVs anywhere in any letter case, .attestations/ and the vendored gate"
    when: "the review prompt is built"
    then: "none of their content reaches the model"
    tests:
      - "test_leftshift.TestPromptNeverSeesData.test_data_csv_attestations_and_vendored_gate_are_withheld"
  - id: AC-7
    given: "a pull request head commit"
    when: "attest verify runs in CI, which never calls a model"
    then: "it fails when the attestation is missing, stale, failed, not an A2A v1.0 Task, missing a required gate, records a red gate, or holds a verified HIGH or no-longer-matching finding"
    tests:
      - "test_leftshift.TestVerify.test_a_fresh_attestation_verifies"
      - "test_leftshift.TestVerify.test_no_attestation_means_the_local_run_did_not_happen"
      - "test_leftshift.TestVerify.test_code_changed_after_attesting_is_stale"
      - "test_leftshift.TestVerify.test_failed_state_and_verdict_are_rejected"
      - "test_leftshift.TestVerify.test_a_missing_or_red_gate_is_rejected"
      - "test_leftshift.TestVerify.test_verified_findings_are_rechecked"
      - "test_leftshift.TestVerify.test_not_an_a2a_task"
---

# PRD: left-shift gates

## Problem

The repo's rules live in prose. `CLAUDE.md` says `data/raw/` is read-only and PII never leaves it, and the BMAD and OpenSpec flow says every requirement is specified and tested. Nothing checks any of it, so a hurried commit, human or agent, can break a rule and nobody finds out until after the push.

## Goal

Every rule above becomes a check that runs on the developer's machine before the commit and again on GitHub before the merge, with the same command in both places. A commit made without the local run, including the headless Claude review, cannot go green.

## Users

- Developers and agents building the Member 360 pipeline in this repo.
- Reviewers who need evidence, not a claim, that the rules held.

## Out of scope

- The Member 360 pipeline itself and its FR-12 data-quality checks.
- Any model call from CI. CI verifies the attestation; it does not repeat the review.
