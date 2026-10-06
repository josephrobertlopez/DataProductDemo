"""Tests for the left-shift checks and the attestation (ACs in openspec/changes/left-shift-gates)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Appended, not inserted: specgate's L5 puts a mutated copy of these modules
# first on PYTHONPATH, and an insert here would shadow it with the original.
sys.path.append(str(Path(__file__).resolve().parents[1] / "tools" / "leftshift"))

import attest  # noqa: E402
import checks  # noqa: E402

GIT_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


class GitRepoCase(unittest.TestCase):
    """A throwaway repo, with the variables a git hook sets removed.

    Inside a hook those point at the real repository, so a test's
    "throwaway" commit would land on the real branch instead.
    """

    def setUp(self):
        clean = {k: v for k, v in os.environ.items() if k not in GIT_VARS}
        env = patch.dict(os.environ, clean, clear=True)
        env.start()
        self.addCleanup(env.stop)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = tmp.name
        self.git("init", "-q", "-b", "main")
        for key, value in (("user.email", "t@example.com"), ("user.name", "t"),
                           ("commit.gpgsign", "false"), ("core.autocrlf", "false")):
            self.git("config", key, value)

    def git(self, *args):
        return subprocess.run(["git", "-C", self.repo, *args], capture_output=True,
                              text=True, check=True).stdout

    def write(self, rel, text):
        path = Path(self.repo, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    def commit(self, message="c"):
        self.git("add", "-A")
        self.git("commit", "-q", "--no-verify", "-m", message)


class TestRawIsReadOnly(GitRepoCase):
    def setUp(self):
        super().setUp()
        self.write("data/raw/Member.csv", "member_id,first_name\nM1,x\n")
        self.write("src/app.py", "x = 1\n")
        self.commit()

    # covers: AC-1
    def test_unchanged_raw_is_clean(self):
        self.write("src/app.py", "x = 2\n")
        self.git("add", "-A")
        self.assertEqual(checks.raw_changes(self.repo, None), [])

    # covers: AC-1
    def test_staged_edit_is_reported(self):
        self.write("data/raw/Member.csv", "member_id,first_name\nM1,y\n")
        self.git("add", "-A")
        self.assertEqual(checks.raw_changes(self.repo, None), ["M\tdata/raw/Member.csv"])

    # covers: AC-1
    def test_moving_a_file_out_of_raw_is_a_deletion_not_a_rename(self):
        self.git("mv", "data/raw/Member.csv", "data/Member.csv")
        self.assertEqual(checks.raw_changes(self.repo, None), ["D\tdata/raw/Member.csv"])

    # covers: AC-1
    def test_branch_against_base(self):
        self.git("switch", "-q", "-c", "feat")
        self.write("data/raw/New.csv", "a\n1\n")
        self.commit()
        self.assertEqual(checks.raw_changes(self.repo, "main"), ["A\tdata/raw/New.csv"])
        self.git("switch", "-q", "main")
        self.git("switch", "-q", "-c", "clean")
        self.write("src/app.py", "x = 3\n")
        self.commit()
        self.assertEqual(checks.raw_changes(self.repo, "main"), [])


class TestNoPiiColumns(GitRepoCase):
    def setUp(self):
        super().setUp()
        self.write("data/raw/Member.csv", "member_id,first_name,postal_code\nM1,x,1\n")
        self.write("data/product/ok.csv", "member_id,age_band,home_state\nM1,30-39,CA\n")
        self.write("notes.md", "first_name is never published\n")

    # covers: AC-2
    def test_raw_and_non_csv_files_are_not_checked(self):
        self.git("add", "-A")
        self.assertEqual(checks.pii_columns(self.repo, "index"), [])

    # covers: AC-2
    def test_respelled_pii_columns_are_caught_in_the_index(self):
        self.write("data/product/bad.csv", "﻿member_id,First Name,ZIP\nM1,x,1\n")
        self.git("add", "-A")
        self.assertEqual(checks.pii_columns(self.repo, "index"),
                         [("data/product/bad.csv", "First Name"), ("data/product/bad.csv", "ZIP")])

    # covers: AC-2
    def test_committed_csv_is_checked_at_a_revision(self):
        self.write("out/x.CSV", "member_id,lastName\nM1,y\n")
        self.commit()
        self.assertEqual(checks.pii_columns(self.repo, "HEAD"), [("out/x.CSV", "lastName")])

    # covers: AC-2
    def test_unstaged_edit_does_not_count_for_the_index(self):
        self.git("add", "-A")
        self.write("data/product/ok.csv", "member_id,postal_code\nM1,1\n")
        self.assertEqual(checks.pii_columns(self.repo, "index"), [])


class TestEveryRequirementHasAScenario(GitRepoCase):
    # covers: AC-3
    def test_requirements_without_scenarios_are_listed(self):
        self.write("openspec/changes/c/specs/c/spec.md", "\n".join([
            "## ADDED Requirements",
            "### Requirement: Has one",
            "The system SHALL x.",
            "#### Scenario: ok",
            "### Requirement: Has none",
            "The system SHALL y.",
            "## Another section",
            "### Requirement: Last and empty",
            "The system SHALL z.",
        ]) + "\n")
        self.assertEqual(checks.missing_scenarios(self.repo), [
            ("openspec/changes/c/specs/c/spec.md", 5, "Has none"),
            ("openspec/changes/c/specs/c/spec.md", 8, "Last and empty"),
        ])

    # covers: AC-3
    def test_a_complete_spec_is_clean(self):
        self.write("openspec/specs/s/spec.md",
                   "### Requirement: A\nThe system SHALL a.\n#### Scenario: a\n- **WHEN** x\n")
        self.assertEqual(checks.missing_scenarios(self.repo), [])


class TestKtDocs(unittest.TestCase):
    # covers: AC-4
    def test_only_files_under_docs_kt_count(self):
        self.assertEqual(checks.kt_docs(["src/a.py", "docs/kt/x/README.md", "docs/kt-old/a.md"]),
                         ["docs/kt/x/README.md"])
        self.assertEqual(checks.kt_docs(["src/a.py", "docs/README.md"]), [])

    def test_act_event_payload_lists_the_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = Path(tmp, "e.json")
            event.write_text(json.dumps({"act_changed_files": ["docs/kt/a.md"]}))
            with patch.dict(os.environ, {"ACT": "true", "GITHUB_EVENT_PATH": str(event)}):
                self.assertEqual(checks.changed_files(tmp, "main"), ["docs/kt/a.md"])

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            event = Path(tmp, "e.json")
            for files, code in ((["docs/kt/a.md"], 0), (["src/a.py"], 1)):
                event.write_text(json.dumps({"act_changed_files": files}))
                with patch.dict(os.environ, {"ACT": "true", "GITHUB_EVENT_PATH": str(event)}), \
                        patch("sys.stdout"), patch("sys.stderr"):
                    self.assertEqual(checks.main(["--repo", tmp, "kt-docs", "--base", "main"]), code)


def gates_with(failing=None):
    """A gate runner that records the commands and fails only `failing`."""
    calls = []

    def run(cmd, **_kwargs):
        calls.append(cmd)
        name = attest.GATES[len(calls) - 1][0]
        return subprocess.CompletedProcess(cmd, 1 if name == failing else 0, stdout="", stderr="")
    run.calls = calls
    return run


def answer(findings=()):
    return lambda prompt: {"review": {"verdict": "pass", "summary": "s", "findings": list(findings)},
                           "model": "test-model", "sessionId": "sess", "costUsd": 0.0}


def high(file, line, quote):
    return {"severity": "HIGH", "ac": "AC-1", "claim": "broken", "file": file, "line": line, "quote": quote}


class AttestCase(GitRepoCase):
    def setUp(self):
        super().setUp()
        self.write("src/app.py", "def f():\n    return 1\n")
        self.write("data/raw/Member.csv", "member_id,first_name\nM1,SECRET_NAME\n")
        self.write("openspec/changes/c/prd.md", "---\nfeature: c\nacs: []\n---\n# c\n")
        self.commit("base")
        self.write("src/app.py", "def f():\n    return 2\n")
        self.git("add", "-A")

    def make(self, run=None, ask=None):
        with patch("sys.stdout"), patch("sys.stderr"):
            return attest.make(self.repo, run or gates_with(), ask or answer())

    def task(self):
        return json.loads(Path(self.repo, attest.ATTESTATION).read_text(encoding="utf-8"))


class TestMake(AttestCase):
    # covers: AC-5
    def test_green_gates_and_clean_review_write_a_staged_a2a_task(self):
        digest = attest.staged_digest(self.repo)
        self.assertEqual(self.make(), 0)
        task = self.task()
        data = task["artifacts"][0]["parts"][0]["data"]
        self.assertEqual(task["status"]["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(task["metadata"]["a2aVersion"], "1.0.0")
        self.assertEqual(data["subject"]["digest"], digest)
        self.assertEqual([g["name"] for g in data["gates"]], list(attest.REQUIRED_GATES))
        self.assertEqual(data["verdict"], "pass")
        self.assertIn(attest.ATTESTATION, self.git("diff", "--cached", "--name-only"))
        # Staging the attestation does not move the digest it carries.
        self.assertEqual(attest.staged_digest(self.repo), digest)

    # covers: AC-5
    def test_a_red_gate_stops_before_the_review(self):
        asked = []
        run = gates_with(failing="openspec")
        self.assertEqual(self.make(run, lambda p: asked.append(p) or {}), 1)
        self.assertEqual(asked, [])
        self.assertEqual(len(run.calls), 3)
        self.assertFalse(Path(self.repo, attest.ATTESTATION).exists())

    # covers: AC-5
    def test_a_verified_high_finding_fails_and_is_not_staged(self):
        self.assertEqual(self.make(ask=answer([high("src/app.py", 2, "return 2")])), 1)
        self.assertEqual(self.task()["status"]["state"], "TASK_STATE_FAILED")
        self.assertNotIn(attest.ATTESTATION, self.git("diff", "--cached", "--name-only"))

    # covers: AC-5
    def test_a_high_finding_that_points_at_nothing_is_ignored(self):
        self.assertEqual(self.make(ask=answer([high("src/app.py", 2, "return 99")])), 0)
        findings = self.task()["artifacts"][0]["parts"][0]["data"]["findings"]
        self.assertEqual((len(findings["verified"]), len(findings["unverified"])), (0, 1))

    # covers: AC-5
    def test_an_oversized_change_is_refused_not_truncated(self):
        with patch.object(attest, "PROMPT_LIMIT", 10):
            self.assertEqual(self.make(), 1)
        self.assertFalse(Path(self.repo, attest.ATTESTATION).exists())


class TestPromptNeverSeesData(AttestCase):
    # covers: AC-6
    def test_data_csv_attestations_and_vendored_gate_are_withheld(self):
        self.write("data/product/p.csv", "member_id\nPRODUCT_VALUE\n")
        self.write("export.csv", "member_id\nROOT_CSV_VALUE\n")
        self.write("out/Export.CSV", "member_id\nUPPER_CSV_VALUE\n")
        self.write("data/notes.txt", "DATA_DIR_VALUE\n")
        self.write("tools/specgate/src/x.py", "VENDORED = 1\n")
        self.write(".attestations/old.json", "{}\n")
        self.write("data/raw/Member.csv", "member_id,first_name\nM1,SECRET_NAME_2\n")
        self.git("add", "-A")
        files = attest.review_files(self.repo)
        self.assertEqual(sorted(files), ["src/app.py"])
        prompt = attest.build_prompt(files, "acs", [])
        for leaked in ("SECRET_NAME", "PRODUCT_VALUE", "ROOT_CSV_VALUE", "UPPER_CSV_VALUE",
                       "DATA_DIR_VALUE", "VENDORED"):
            self.assertNotIn(leaked, prompt)
        self.assertIn("    2      return 2", prompt)


class TestVerify(AttestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.make(), 0)
        self.commit("attested")

    def rewrite(self, change):
        task = self.task()
        change(task, task["artifacts"][0]["parts"][0]["data"])
        self.write(attest.ATTESTATION, json.dumps(task))
        self.commit("edited")

    # covers: AC-7
    def test_a_fresh_attestation_verifies(self):
        self.assertEqual(attest.verify(self.repo), [])

    # covers: AC-7
    def test_no_attestation_means_the_local_run_did_not_happen(self):
        self.git("rm", "-q", attest.ATTESTATION)
        self.commit("skipped hooks")
        self.assertEqual(len(attest.verify(self.repo)), 1)
        self.assertIn("did not happen", attest.verify(self.repo)[0])

    # covers: AC-7
    def test_code_changed_after_attesting_is_stale(self):
        self.write("src/app.py", "def f():\n    return 3\n")
        self.commit("sneaky")
        self.assertEqual([p[:6] for p in attest.verify(self.repo)], ["stale:"])

    # covers: AC-7
    def test_failed_state_and_verdict_are_rejected(self):
        def fail(task, data):
            task["status"]["state"] = "TASK_STATE_FAILED"
            data["verdict"] = "fail"
        self.rewrite(fail)
        self.assertEqual(attest.verify(self.repo),
                         ["status.state is TASK_STATE_FAILED", "verdict is 'fail'"])

    # covers: AC-7
    def test_a_missing_or_red_gate_is_rejected(self):
        def drop(task, data):
            data["gates"] = [g for g in data["gates"] if g["name"] != "specgate"]
            data["gates"][0]["exit"] = 2
        self.rewrite(drop)
        problems = attest.verify(self.repo)
        self.assertEqual(len(problems), 2)
        self.assertTrue(problems[0].startswith("gates recorded"))
        self.assertEqual(problems[1], "gate raw exited 2")

    # covers: AC-7
    def test_verified_findings_are_rechecked(self):
        def add(task, data):
            data["findings"]["verified"] = [
                high("src/app.py", 2, "return 2"),
                dict(high("src/app.py", 2, "return 7"), severity="LOW")]
        self.rewrite(add)
        self.assertEqual(attest.verify(self.repo), [
            "HIGH finding: src/app.py:2 broken",
            "verified finding no longer matches: src/app.py:2"])

    # covers: AC-7
    def test_not_an_a2a_task(self):
        self.rewrite(lambda task, data: task["metadata"].update(a2aVersion="0.3.0"))
        self.assertEqual(attest.verify(self.repo), ["metadata.a2aVersion is not 1.0.0"])
        self.write(attest.ATTESTATION, "[1, 2]")
        self.commit("junk")
        self.assertEqual(attest.verify(self.repo), ["attestation is not a JSON object"])

    # covers: AC-7
    def test_not_json(self):
        self.write(attest.ATTESTATION, "{nope")
        self.commit("junk")
        self.assertTrue(attest.verify(self.repo)[0].startswith(f"{attest.ATTESTATION} is not JSON"))


class TestClaudeCommand(unittest.TestCase):
    def test_no_tools_no_mcp_no_session_and_auth_by_environment(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            cmd = attest.claude_command("sonnet")
        self.assertIn("--bare", cmd)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        for flag in ("-p", "--strict-mcp-config", "--no-session-persistence", "--json-schema"):
            self.assertIn(flag, cmd)
        with patch.dict(os.environ, {}, clear=True):
            self.assertIn("--restricted", attest.claude_command("sonnet"))

    def test_structured_output_is_read_from_the_envelope(self):
        envelope = {"structured_output": {"verdict": "pass", "summary": "s", "findings": []},
                    "session_id": "abc", "total_cost_usd": 0.01, "modelUsage": {"claude-x": {}}}
        done = subprocess.CompletedProcess([], 0, stdout=json.dumps(envelope), stderr="")
        got = attest.ask_claude("p", run=lambda *_a, **_k: done)
        self.assertEqual((got["model"], got["sessionId"], got["review"]["verdict"]),
                         ("claude-x", "abc", "pass"))


if __name__ == "__main__":
    unittest.main()
