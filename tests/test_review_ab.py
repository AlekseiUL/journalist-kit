import copy
import io
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import review_ab


def verdict(pair_id):
    return {"pair_id": pair_id, "X": {"status": "pass", "findings": []},
            "Y": {"status": "pass", "findings": []},
            "editorial_preference": "tie", "editorial_reason": "Comparable synthetic wording.",
            "practical_preference": "tie", "practical_reason": "Both satisfy the synthetic request."}


def review_data(pair_ids):
    return {"reviews": [verdict(pair_id) for pair_id in pair_ids],
            "limits": "Synthetic model review fixture."}


def cli_events(final):
    return b"".join((json.dumps(event) + "\n").encode("utf-8") for event in [
        {"type": "thread.started"}, {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": final}},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 10}}])


class BlindReviewTests(unittest.TestCase):
    def test_second_judge_gets_reversed_order_and_swapped_texts_without_mutation(self):
        pairs = [{"pair_id": "p{:02d}".format(i), "X": {"text": "first"},
                  "Y": {"text": "second"}} for i in range(1, 13)]
        original = copy.deepcopy(pairs)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            packet = root / "packet.json"
            rubric = root / "rubric.txt"
            packet.write_text(json.dumps({"pairs": pairs}), encoding="utf-8")
            rubric.write_text("Evaluate both texts.", encoding="utf-8")
            manifest = review_ab.prepare(packet, rubric, root / "judges")
            self.assertEqual(len(manifest["jobs"]), 4)
            self.assertEqual(manifest["jobs"][2]["pair_ids"],
                             ["p12", "p11", "p10", "p09", "p08", "p07"])
            prompt = (root / "judges/judge2-batch1.prompt.txt").read_text(encoding="utf-8")
            shown = json.loads(prompt.split("Пары для оценки:\n", 1)[1])["pairs"]
            self.assertEqual(shown[0]["X"]["text"], "second")
            self.assertEqual(shown[0]["Y"]["text"], "first")
            self.assertNotIn("gpt-", prompt)
        self.assertEqual(pairs, original)

    def test_model_execution_is_opt_in(self):
        with patch.object(review_ab.subprocess, "run") as call:
            with self.assertRaises(ValueError):
                review_ab.run(Path("unused"))
            call.assert_not_called()

    def test_missing_duplicate_or_bad_judge_verdict_rejected(self):
        review = verdict("p01")
        valid = {"reviews": [review], "limits": "Synthetic fixture."}
        self.assertEqual(review_ab.parse_review(json.dumps(valid), ["p01"]), valid)
        with self.assertRaises(ValueError):
            review_ab.parse_review(json.dumps(valid), ["p01", "p02"])
        with self.assertRaises(ValueError):
            review_ab.parse_review(json.dumps({"reviews": [review, review], "limits": "Fixture."}), ["p01", "p02"])
        review["X"]["status"] = "guaranteed"
        with self.assertRaises(ValueError):
            review_ab.parse_review(json.dumps(valid), ["p01"])

    def test_findings_and_reasons_must_match_declared_schema(self):
        valid = review_data(["p01"])
        finding = {"severity": "revision", "category": "style", "quote": "first",
                   "source_id": None, "reason": "Synthetic reason.", "minimal_fix": "Synthetic fix."}
        valid["reviews"][0]["X"]["findings"].append(finding)
        self.assertEqual(review_ab.parse_review(json.dumps(valid), ["p01"]), valid)
        for key, invalid in (("severity", "warning"), ("category", "unknown"), ("quote", ""),
                             ("reason", []), ("source_id", 5), ("minimal_fix", None)):
            with self.subTest(key=key):
                changed = copy.deepcopy(valid)
                changed["reviews"][0]["X"]["findings"][0][key] = invalid
                with self.assertRaises(ValueError):
                    review_ab.parse_review(json.dumps(changed), ["p01"])
        missing_reason = review_data(["p01"])
        del missing_reason["reviews"][0]["editorial_reason"]
        with self.assertRaises(ValueError):
            review_ab.parse_review(json.dumps(missing_reason), ["p01"])


class JudgeExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dest = self.root / "judges"
        self.packet = self.root / "packet.json"
        self.rubric = self.root / "rubric.txt"
        self.packet.write_bytes(review_ab.ab.json_bytes({"pairs": [
            {"pair_id": "p01", "X": {"text": "First text."}, "Y": {"text": "Second text."}},
            {"pair_id": "p02", "X": {"text": "Third text."}, "Y": {"text": "Fourth text."}}]}))
        self.rubric.write_bytes(b"Synthetic reviewer instructions.\r\n")
        self.manifest = review_ab.prepare(self.packet, self.rubric, self.dest)

    def fake_success(self, command, **kwargs):
        prompt = kwargs["input"].decode("utf-8")
        pairs = json.loads(prompt.split("Пары для оценки:\n", 1)[1])["pairs"]
        final = " \n" + json.dumps(review_data([pair["pair_id"] for pair in pairs])) + "\n\n"
        return subprocess.CompletedProcess(command, 0, cli_events(final), b"synthetic raw stderr")

    def fake_run(self, side_effect=None):
        with (patch.object(review_ab.shutil, "which", return_value="synthetic-codex"),
              patch.object(review_ab.subprocess, "run", side_effect=side_effect or self.fake_success) as call,
              redirect_stdout(io.StringIO())):
            result = review_ab.run(self.dest, execute=True)
        return result, call

    def test_run_preserves_exact_output_and_uses_fresh_bounded_processes(self):
        directories = []

        def fake(command, **kwargs):
            directory = Path(kwargs["cwd"])
            self.assertTrue(directory.is_dir())
            self.assertEqual(list(directory.iterdir()), [])
            self.assertEqual(kwargs["timeout"], 300)
            self.assertNotIn("env", kwargs)
            directories.append(directory)
            return self.fake_success(command, **kwargs)

        result, call = self.fake_run(fake)
        self.assertEqual(result, {"completed": 2, "failed": 0, "executed_now": 2, "skipped": 0})
        self.assertEqual(call.call_count, 2)
        self.assertEqual(len(set(directories)), 2)
        self.assertFalse(any(path.exists() for path in directories))
        record = review_ab.read_record(self.dest, self.manifest["jobs"][0])
        self.assertTrue(record["final_text"].startswith(" \n"))
        self.assertTrue(record["final_text"].endswith("\n\n"))
        self.assertEqual(record["prompt"].encode("utf-8"), (self.dest / "judge1-batch1.prompt.txt").read_bytes())
        self.assertEqual((self.dest / "rubric.txt").read_bytes(), self.rubric.read_bytes())
        self.assertEqual((self.dest / "packet.json").read_bytes(), self.packet.read_bytes())

    def test_timeouts_keep_partial_raw_and_failed_records_and_resume_without_calls(self):
        failure = subprocess.TimeoutExpired("synthetic-codex", 300, output=b"partial raw", stderr=b"raw error")
        result, call = self.fake_run(failure)
        self.assertEqual(result["failed"], 2)
        self.assertEqual(call.call_count, 2)
        self.assertEqual((self.dest / "judge1-batch1.raw.jsonl").read_bytes(), b"partial raw")
        record = review_ab.read_record(self.dest, self.manifest["jobs"][0])
        self.assertIn("timeout", record["flags"])
        self.assertIsNone(record["review"])
        with patch.object(review_ab.shutil, "which", return_value=None) as which, patch.object(
                review_ab.subprocess, "run") as repeated, redirect_stdout(io.StringIO()):
            resumed = review_ab.run(self.dest, execute=True)
        which.assert_not_called()
        repeated.assert_not_called()
        self.assertEqual(resumed, {"completed": 0, "failed": 2, "executed_now": 0, "skipped": 2})

    def test_all_prompts_are_checked_before_any_new_call(self):
        (self.dest / "judge2-batch1.prompt.txt").write_text("tampered", encoding="utf-8")
        with patch.object(review_ab.subprocess, "run") as call, self.assertRaises(ValueError):
            review_ab.run(self.dest, execute=True)
        call.assert_not_called()

    def test_interrupted_attempt_is_never_automatically_retried(self):
        (self.dest / "judge1-batch1.attempt.json").write_bytes(b"{}\n")
        with patch.object(review_ab.subprocess, "run") as call, self.assertRaises(ValueError):
            review_ab.run(self.dest, execute=True)
        call.assert_not_called()

    def test_resumed_completed_prompt_tampering_is_rejected(self):
        self.fake_run()
        (self.dest / "judge1-batch1.prompt.txt").write_text("tampered", encoding="utf-8")
        with patch.object(review_ab.subprocess, "run") as call, self.assertRaises(ValueError):
            review_ab.run(self.dest, execute=True)
        call.assert_not_called()

    def test_frozen_manifest_tampering_is_rejected_before_calls(self):
        path = self.dest / "manifest.json"
        manifest = review_ab.ab.read_json(path)
        manifest["jobs"][0]["requested_model"] = "another-model"
        path.write_bytes(review_ab.ab.json_bytes(manifest))
        with patch.object(review_ab.subprocess, "run") as call, self.assertRaises(ValueError):
            review_ab.run(self.dest, execute=True)
        call.assert_not_called()

    def test_resumed_final_tampering_is_rejected_without_rerun(self):
        self.fake_run()
        path = self.dest / "judge1-batch1.record.json"
        record = review_ab.ab.read_json(path)
        record["final_text"] = "changed"
        path.write_bytes(review_ab.ab.json_bytes(record))
        with patch.object(review_ab.subprocess, "run") as call, self.assertRaises(ValueError):
            review_ab.run(self.dest, execute=True)
        call.assert_not_called()

    def test_invalid_judge_json_retained_and_main_returns_failure_on_resume(self):
        invalid = subprocess.CompletedProcess([], 0, cli_events('{"reviews": []}'), b"")
        result, _ = self.fake_run([invalid, invalid])
        self.assertEqual(result["failed"], 2)
        record = review_ab.read_record(self.dest, self.manifest["jobs"][0])
        self.assertIn("invalid_review_json", record["flags"])
        self.assertEqual(record["final_text"], '{"reviews": []}')
        with patch.object(review_ab.subprocess, "run") as call, redirect_stdout(io.StringIO()):
            code = review_ab.main(["run", "--dest", str(self.dest), "--execute"])
        call.assert_not_called()
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
