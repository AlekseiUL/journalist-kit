import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("run_ab", ROOT / "tools/run_ab.py")
ab = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ab)


def events(*middle):
    data = [{"type": "thread.started", "thread_id": "synthetic"}, {"type": "turn.started"}]
    data.extend(middle or [{"type": "item.completed", "item": {
        "type": "agent_message", "text": "  Synthetic final.\n\n"}}])
    data.append({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 3}})
    return b"".join(ab.json_bytes(item).replace(b"\n", b"") + b"\n" for item in data)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dataset = self.root / "cases.json"
        self.cases = [{"id": "case-" + str(index), "request": "Write a short synthetic report.",
                       "sources": [{"id": "s1", "content": "A synthetic fact.", "use": "public"}],
                       "min_words": 2, "max_words": 10, "reviewer_notes": ["SECRET RUBRIC MARKER"]}
                      for index in range(6)]
        self.dataset.write_bytes(ab.json_bytes({"cases": self.cases}))
        for name in ab.SKILL_FILES:
            path = self.root / "skills/source-and-voice" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("SYNTHETIC DOCUMENT " + name + "\n", encoding="utf-8")
        self.run_dir = self.root / "run"

    def prepare(self):
        return ab.prepare(self.run_dir, self.dataset, self.root)

    def run_fake(self, side_effect=None):
        with patch.object(ab.shutil, "which", return_value="synthetic-codex"), patch.object(
                ab.subprocess, "run", side_effect=side_effect,
                return_value=subprocess.CompletedProcess([], 0, events(), b"")) as call, redirect_stdout(io.StringIO()):
            result = ab.run(self.run_dir, execute=True)
        return result, call

    def test_prepare_freezes_exact_inputs_and_keeps_rubric_out_of_both_arms(self):
        manifest = self.prepare()
        self.assertEqual((self.run_dir / "snapshots/cases.json").read_bytes(), self.dataset.read_bytes())
        self.assertEqual(len(manifest["jobs"]), 24)
        self.assertEqual(manifest["injection_mode"], "full-injection-not-native-loading")
        for job in manifest["jobs"]:
            prompt = (self.run_dir / job["prompt_path"]).read_text(encoding="utf-8")
            self.assertNotIn("SECRET RUBRIC MARKER", prompt)
            self.assertEqual("SYNTHETIC DOCUMENT" in prompt, job["arm"] == "treatment")
            self.assertIn(ab.COMMON, prompt)
        self.assertEqual([job["arm"] for job in manifest["jobs"][:4]],
                         ["baseline", "treatment", "treatment", "baseline"])

    def test_prepare_mapping_and_prompts_are_reproducible(self):
        first = self.prepare()
        second_dir = self.root / "second"
        second = ab.prepare(second_dir, self.dataset, self.root)
        self.assertEqual(first["hashes"], second["hashes"])
        self.assertEqual(first["jobs"], second["jobs"])
        self.assertEqual((self.run_dir / "mapping.json").read_bytes(), (second_dir / "mapping.json").read_bytes())
        with self.assertRaises(FileExistsError):
            self.prepare()

    def test_protocol_and_judge_are_frozen_without_entering_model_prompts(self):
        directory = self.root / "evals/ab-2026-09-07"
        directory.mkdir(parents=True)
        (directory / "PROTOCOL.md").write_bytes(b"SYNTHETIC PROTOCOL\r\n")
        (directory / "JUDGE.md").write_bytes(b"SYNTHETIC JUDGE RUBRIC\n")
        manifest = self.prepare()
        self.assertEqual((self.run_dir / "snapshots/protocol.txt").read_bytes(), b"SYNTHETIC PROTOCOL\r\n")
        self.assertEqual((self.run_dir / "snapshots/judge.txt").read_bytes(), b"SYNTHETIC JUDGE RUBRIC\n")
        for job in manifest["jobs"]:
            prompt = (self.run_dir / job["prompt_path"]).read_text(encoding="utf-8")
            self.assertNotIn("SYNTHETIC PROTOCOL", prompt)
            self.assertNotIn("SYNTHETIC JUDGE RUBRIC", prompt)

    def test_tampered_frozen_input_fails_before_execution(self):
        manifest = self.prepare()
        (self.run_dir / manifest["jobs"][0]["prompt_path"]).write_text("changed", encoding="utf-8")
        with patch.object(ab.subprocess, "run") as call, self.assertRaises(ValueError):
            ab.run(self.run_dir, execute=True)
        call.assert_not_called()

    def test_execute_gate_prevents_model_calls(self):
        with patch.object(ab.subprocess, "run") as call, self.assertRaises(ValueError):
            ab.run(self.run_dir)
        call.assert_not_called()

    def test_changed_manifest_is_rejected_before_resuming(self):
        self.prepare()
        path = self.run_dir / "manifest.json"
        manifest = ab.read_json(path)
        manifest["requested_model"] = "different-model"
        path.write_bytes(ab.json_bytes(manifest))
        with patch.object(ab.subprocess, "run") as call, self.assertRaises(ValueError):
            ab.run(self.run_dir, execute=True)
        call.assert_not_called()

    def test_run_uses_fresh_directories_exact_stdin_bounded_flags_and_resumes(self):
        manifest = self.prepare()
        seen_directories = []

        def fake(command, **kwargs):
            directory = Path(kwargs["cwd"])
            self.assertTrue(directory.is_dir())
            self.assertEqual(list(directory.iterdir()), [])
            seen_directories.append(directory)
            self.assertEqual(kwargs["timeout"], 180)
            self.assertIsInstance(kwargs["input"], bytes)
            self.assertEqual(command, ["synthetic-codex"] + ab.CLI_ARGS)
            self.assertNotIn("env", kwargs)
            return subprocess.CompletedProcess(command, 0, events(), b"private raw stderr")

        result, call = self.run_fake(fake)
        self.assertEqual(result, {"completed": 24, "failed": 0, "executed_now": 24, "skipped": 0})
        self.assertEqual(call.call_count, 24)
        self.assertEqual(len(set(seen_directories)), 24)
        self.assertFalse(any(directory.exists() for directory in seen_directories))
        record = ab.read_record(self.run_dir, manifest["jobs"][0])
        self.assertEqual(record["final_text"], "  Synthetic final.\n\n")
        self.assertEqual(record["word_count"], 2)
        self.assertIsNone(record["resolved_model"])
        self.assertIsNone(record["usage"]["cached_input_tokens"])
        self.assertIsNone(record["temperature"])
        resumed, repeated = self.run_fake()
        repeated.assert_not_called()
        self.assertEqual(resumed["skipped"], 24)

    def test_timeout_and_process_failures_are_retained_and_never_retried(self):
        self.prepare()
        failure = subprocess.TimeoutExpired("synthetic-codex", 180, output=b"partial", stderr=b"local only")
        result, call = self.run_fake(failure)
        self.assertEqual(result["failed"], 24)
        self.assertEqual(call.call_count, 24)
        resumed, repeated = self.run_fake()
        repeated.assert_not_called()
        self.assertEqual(resumed["failed"], 24)
        packet = ab.blind(self.run_dir, self.root / "judge")
        self.assertEqual(packet["pairs"], [])
        self.assertEqual(len(packet["not_comparable"]), 12)
        self.assertIn("timeout", packet["not_comparable"][0]["reasons"])

    def test_blind_retains_successful_pairs_when_one_attempt_fails(self):
        self.prepare()
        sequence = [subprocess.CompletedProcess([], 1, events(), b"synthetic failure")]
        sequence.extend([subprocess.CompletedProcess([], 0, events(), b"")] * 23)
        self.run_fake(sequence)
        packet = ab.blind(self.run_dir, self.root / "judge")
        self.assertEqual(len(packet["pairs"]), 11)
        self.assertEqual(packet["not_comparable"], [{"pair_id": "p01", "case_id": "case-0",
                                                     "repeat": 1, "reasons": ["nonzero_exit"]}])
        self.assertNotIn("baseline", json.dumps(packet))
        self.assertNotIn("treatment", json.dumps(packet))

    def test_blind_packet_preserves_text_sources_and_notes_without_arm_metadata(self):
        self.prepare()
        self.run_fake()
        packet = ab.blind(self.run_dir, self.root / "judge")
        self.assertEqual(len(packet["pairs"]), 12)
        pair = packet["pairs"][0]
        self.assertEqual(pair["sources"], self.cases[0]["sources"])
        self.assertEqual(pair["reviewer_notes"], self.cases[0]["reviewer_notes"])
        self.assertEqual(set(pair), {"pair_id", "case_id", "repeat", "request", "sources", "reviewer_notes", "X", "Y"})
        self.assertEqual(pair["X"], {"text": "  Synthetic final.\n\n", "word_count": 2, "min_words": 2, "max_words": 10})
        serialized = json.dumps(packet)
        self.assertNotIn("baseline", serialized)
        self.assertNotIn("treatment", serialized)
        self.assertNotIn("prompt_sha256", serialized)


class EventTests(unittest.TestCase):
    def test_warning_is_not_failure_and_all_messages_are_preserved(self):
        result = ab.parse_events(events(
            {"type": "item.completed", "item": {"type": "error", "message": "synthetic warning"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "First message"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "Final text\n"}}))
        self.assertEqual(result["flags"], [])
        self.assertEqual(result["cli_warning_count"], 1)
        self.assertEqual(result["agent_messages"], ["First message", "Final text\n"])
        self.assertEqual(result["final_text"], "Final text\n")

    def test_malformed_missing_turn_and_tool_events_fail_closed(self):
        for raw, expected in (
            (b"broken\n", "malformed_jsonl"),
            (b"{}\n", "malformed_event"),
            (b"", "missing_final"),
            (events({"type": "item.started", "item": {"type": "command_execution"}}), "tool_contamination:command_execution"),
            (events({"type": "turn.failed", "error": {"message": "failure"}}), "cli_failure_event"),
            (events({"type": "turn.started"}), "expected_one_completed_turn"),
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, ab.parse_events(raw)["flags"])

    def test_missing_completion_rejects_even_an_existing_final(self):
        raw = events().rsplit(b"\n", 2)[0] + b"\n"
        self.assertIn("expected_one_completed_turn", ab.parse_events(raw)["flags"])


if __name__ == "__main__":
    unittest.main()
