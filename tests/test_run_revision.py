import copy
from collections import Counter
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location("run_revision", ROOT / "tools/run_revision.py")
revision = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(revision)
ab = revision.ab


def events(text="  Synthetic final.\r\n\n"):
    entries = [{"type": "thread.started"}, {"type": "turn.started"},
               {"type": "item.completed", "item": {"type": "agent_message", "text": text}},
               {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 3}}]
    return b"".join(json.dumps(entry).encode("utf-8") + b"\n" for entry in entries)


def review_for(ids):
    return {"reviews": [{"trio_id": key,
                         "X": {"status": "pass", "findings": []},
                         "Y": {"status": "pass", "findings": []},
                         "Z": {"status": "pass", "findings": []},
                         "editorial_ranking": [["Y", "Z"], ["X"]],
                         "editorial_reason": "Synthetic comparison.",
                         "practical_ranking": [["X", "Y", "Z"]],
                         "practical_reason": "Synthetic tie."} for key in ids],
            "limits": "Synthetic offline reviewer fixture."}


class RevisionRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dataset = self.root / "cases.json"
        self.cases = [{"id": "synthetic-" + str(index), "request": "Write a synthetic report.",
                       "sources": [{"id": "s1", "content": "Synthetic fact.", "use": "public"}],
                       "min_words": 2, "max_words": 10,
                       "reviewer_notes": ["SECRET RUBRIC MARKER"]} for index in range(6)]
        self.dataset.write_bytes(ab.json_bytes({"cases": self.cases}))
        self.protocol, self.rubric = self.root / "PROTOCOL.md", self.root / "JUDGE.md"
        self.protocol.write_bytes(b"SYNTHETIC PROTOCOL\r\n")
        self.rubric.write_bytes(b"SYNTHETIC JUDGE RUBRIC\n")
        files = {}
        for name in ab.SKILL_FILES:
            text = "PREVIOUS DOCUMENT " + name + "\r\n"
            files["snapshots/skill/" + name] = {"text": text, "sha256": ab.digest(text.encode("utf-8"))}
            path = self.root / "skills/source-and-voice" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("CANDIDATE DOCUMENT " + name + "\n").encode("utf-8"))
        self.previous = self.root / "previous.json"
        self.previous.write_bytes(ab.json_bytes({"files": files}))
        self.run_dir, self.judges_dir = self.root / "run", self.root / "judges"

    def prepare(self, dest=None):
        return revision.prepare(dest or self.run_dir, self.dataset, self.protocol,
                                self.rubric, self.previous, self.root)

    def run_fake(self, side_effect=None, reviewer=False):
        method = revision.judge_run if reviewer else revision.run
        dest = self.judges_dir if reviewer else self.run_dir
        with patch.object(revision.shutil, "which", return_value="synthetic-codex"), patch.object(
                revision.subprocess, "run", side_effect=side_effect,
                return_value=subprocess.CompletedProcess([], 0, events(), b"private raw stderr")) as call, redirect_stdout(io.StringIO()):
            result = method(dest, execute=True)
        return result, call

    def prepare_judges(self):
        revision.blind(self.run_dir, self.root / "blind")
        return revision.judge_prepare(self.root / "blind/packet.json", self.rubric, self.judges_dir)

    def fake_judge(self, command, **kwargs):
        self.assertEqual(kwargs["timeout"], 300)
        prompt = kwargs["input"].decode("utf-8")
        batch = json.loads(prompt.split("Тройки для оценки:\n", 1)[1])
        final = json.dumps(review_for([trio["trio_id"] for trio in batch["trios"]]))
        return subprocess.CompletedProcess(command, 0, events(final), b"")

    def test_frozen_inputs_full_injection_and_no_rubric_in_writer_prompts(self):
        manifest = self.prepare()
        self.assertEqual(len(manifest["jobs"]), 36)
        self.assertEqual((self.run_dir / "snapshots/cases.json").read_bytes(), self.dataset.read_bytes())
        self.assertEqual((self.run_dir / "snapshots/protocol.txt").read_bytes(), b"SYNTHETIC PROTOCOL\r\n")
        for job in manifest["jobs"]:
            prompt = (self.run_dir / job["prompt_path"]).read_bytes().decode("utf-8")
            self.assertIn(ab.COMMON, prompt)
            self.assertNotIn("SECRET RUBRIC MARKER", prompt)
            self.assertNotIn("SYNTHETIC PROTOCOL", prompt)
            self.assertNotIn("SYNTHETIC JUDGE RUBRIC", prompt)
            self.assertEqual(prompt.count("PREVIOUS DOCUMENT"), 5 if job["arm"] == "previous" else 0)
            self.assertEqual(prompt.count("CANDIDATE DOCUMENT"), 5 if job["arm"] == "candidate" else 0)
        with self.assertRaises(FileExistsError):
            self.prepare()

    def test_rotating_run_order_and_balanced_reproducible_blinding(self):
        manifest = self.prepare()
        second = self.prepare(self.root / "second")
        self.assertEqual(manifest["hashes"], second["hashes"])
        self.assertEqual(manifest["jobs"], second["jobs"])
        mapping = ab.read_json(self.run_dir / "mapping.json")["trios"]
        permutations = Counter(tuple(trio[label]["arm"] for label in revision.LABELS) for trio in mapping)
        self.assertEqual(sorted(permutations.values()), [2] * 6)
        orders = Counter(tuple(job["arm"] for job in manifest["jobs"][index:index + 3])
                         for index in range(0, 36, 3))
        self.assertEqual(sorted(orders.values()), [4, 4, 4])

    def test_previous_hash_and_frozen_input_tamper_rejected_before_calls(self):
        previous = ab.read_json(self.previous)
        previous["files"]["snapshots/skill/SKILL.md"]["text"] = "changed"
        self.previous.write_bytes(ab.json_bytes(previous))
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.run_dir.exists())

    def test_execute_gates_and_tampered_manifest_prevent_calls(self):
        self.prepare()
        with patch.object(revision.subprocess, "run") as call:
            with self.assertRaises(ValueError):
                revision.run(self.run_dir)
            with self.assertRaises(ValueError):
                revision.judge_run(self.judges_dir)
            (self.run_dir / "snapshots/candidate/SKILL.md").write_bytes(b"changed")
            with self.assertRaises(ValueError):
                revision.run(self.run_dir, execute=True)
        call.assert_not_called()

    def test_fresh_directories_exact_stdin_and_output_unknowns_and_resume(self):
        manifest = self.prepare()
        directories = []

        def fake(command, **kwargs):
            directory = Path(kwargs["cwd"])
            self.assertTrue(directory.is_dir())
            self.assertEqual(list(directory.iterdir()), [])
            directories.append(directory)
            self.assertEqual(kwargs["timeout"], 180)
            self.assertEqual(command, ["synthetic-codex"] + ab.CLI_ARGS)
            self.assertNotIn("env", kwargs)
            return subprocess.CompletedProcess(command, 0, events(), b"private raw stderr")

        result, call = self.run_fake(fake)
        self.assertEqual(result, {"completed": 36, "failed": 0, "executed_now": 36, "skipped": 0})
        self.assertEqual(call.call_count, 36)
        self.assertEqual(len(set(directories)), 36)
        self.assertFalse(any(path.exists() for path in directories))
        record = revision.read_record(self.run_dir, manifest["jobs"][0])
        self.assertEqual(record["final_text"], "  Synthetic final.\r\n\n")
        self.assertEqual(record["word_count"], 2)
        self.assertIsNone(record["resolved_model"])
        self.assertIsNone(record["usage"]["cached_input_tokens"])
        self.assertIsNone(record["backend_model_snapshot"])
        self.assertEqual(len(list((self.run_dir / "attempts").glob("*.json"))), 36)
        resumed, repeated = self.run_fake()
        repeated.assert_not_called()
        self.assertEqual(resumed["skipped"], 36)

    def test_unfinished_attempt_blocks_all_model_calls(self):
        manifest = self.prepare()
        job = manifest["jobs"][3]
        ab.save(self.run_dir / "attempts" / (job["output_id"] + ".json"), b"{}")
        with patch.object(revision.subprocess, "run") as call, self.assertRaises(ValueError):
            revision.run(self.run_dir, execute=True)
        call.assert_not_called()

    def test_failures_are_terminal_and_remain_in_trio_denominator(self):
        self.prepare()
        failure = subprocess.TimeoutExpired("synthetic-codex", 180, output=b"partial", stderr=b"private")
        sequence = [failure] + [subprocess.CompletedProcess([], 0, events(), b"")] * 35
        result, call = self.run_fake(sequence)
        self.assertEqual((result["failed"], call.call_count), (1, 36))
        resumed, repeated = self.run_fake()
        repeated.assert_not_called()
        self.assertEqual(resumed["failed"], 1)
        packet = revision.blind(self.run_dir, self.root / "blind")
        self.assertEqual((len(packet["trios"]), len(packet["not_comparable"])), (11, 1))
        self.assertIn("timeout", packet["not_comparable"][0]["reasons"])
        self.assertNotIn("candidate", json.dumps(packet))
        self.assertNotIn("output_id", json.dumps(packet))

    def test_raw_output_tamper_is_rejected(self):
        manifest = self.prepare()
        self.run_fake()
        (self.run_dir / "raw" / (manifest["jobs"][0]["output_id"] + ".jsonl")).write_bytes(b"changed")
        with self.assertRaises(ValueError):
            revision.blind(self.run_dir, self.root / "blind")

    def test_reviewer_rotates_labels_and_reverses_order_with_four_bounded_calls(self):
        self.prepare()
        self.run_fake()
        manifest = self.prepare_judges()
        self.assertEqual(len(manifest["jobs"]), 4)
        self.assertEqual(manifest["jobs"][0]["trio_ids"], ["t01", "t02", "t03", "t04", "t05", "t06"])
        self.assertEqual(manifest["jobs"][2]["trio_ids"], ["t12", "t11", "t10", "t09", "t08", "t07"])
        self.assertEqual(manifest["jobs"][2]["presented_to_canonical"], {"X": "Y", "Y": "Z", "Z": "X"})
        result, call = self.run_fake(self.fake_judge, reviewer=True)
        self.assertEqual((result["completed"], call.call_count), (4, 4))
        second, repeated = self.run_fake(reviewer=True)
        repeated.assert_not_called()
        self.assertEqual(second["skipped"], 4)

    def test_invalid_reviewer_json_is_terminal(self):
        self.prepare()
        self.run_fake()
        self.prepare_judges()
        result, call = self.run_fake(reviewer=True)
        self.assertEqual((result["failed"], call.call_count), (4, 4))
        _, repeated = self.run_fake(reviewer=True)
        repeated.assert_not_called()

    def test_ranking_parser_accepts_partial_ties_and_rejects_duplicate_labels(self):
        data = review_for(["t01"])
        self.assertEqual(revision.parse_review(json.dumps(data), ["t01"]), data)
        bad = copy.deepcopy(data)
        bad["reviews"][0]["editorial_ranking"] = [["X", "X"], ["Z"]]
        with self.assertRaises(ValueError):
            revision.parse_review(json.dumps(bad), ["t01"])
        with self.assertRaises(ValueError):
            revision.parse_review(json.dumps(data), ["t01", "t02"])

    def test_normalization_applies_cyclic_rotation_before_arm_lookup(self):
        mapping = {"X": {"arm": "candidate"}, "Y": {"arm": "baseline"}, "Z": {"arm": "previous"}}
        data = review_for(["t01"])["reviews"][0]
        normalized = revision.normalize_review(data, mapping, {"X": "Y", "Y": "Z", "Z": "X"})
        self.assertEqual(normalized["editorial_ranking"], [["previous", "candidate"], ["baseline"]])
        self.assertEqual(normalized["editorial_pairwise"], {
            "baseline_vs_previous": "previous", "baseline_vs_candidate": "candidate", "previous_vs_candidate": "tie"})
        self.assertEqual(normalized["practical_pairwise"], {
            "baseline_vs_previous": "tie", "baseline_vs_candidate": "tie", "previous_vs_candidate": "tie"})

    def test_public_export_preserves_exact_output_bytes_and_all_denominators(self):
        self.prepare()
        self.run_fake()
        self.prepare_judges()
        self.run_fake(self.fake_judge, reviewer=True)
        dest = self.root / "export"
        result = revision.export(self.run_dir, self.judges_dir, dest)
        self.assertEqual((len(result["writer_records"]), len(result["judge_records"]), len(result["trios"])), (36, 4, 12))
        self.assertEqual((dest / "outputs/synthetic-0-r1-baseline.txt").read_bytes(), b"  Synthetic final.\r\n\n")
        serialized = json.dumps(result)
        self.assertNotIn("private raw stderr", serialized)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn('"prompt":', serialized)
        self.assertEqual(result["statistics"]["writers"]["candidate"]["measurements"]["input_tokens"]["observed_total"], 120)
        self.assertIsNone(result["statistics"]["writers"]["candidate"]["measurements"]["cached_input_tokens"]["observed_total"])
        tallies = result["statistics"]["consensus_pairwise"]["practical"]["baseline_vs_candidate"]
        self.assertEqual(tallies["tie"], 12)
        self.assertEqual(sum(tallies.values()), 12)
        snapshot = ab.read_json(dest / "snapshots.json")
        self.assertEqual(snapshot["files"]["snapshots/previous/SKILL.md"]["text"], "PREVIOUS DOCUMENT SKILL.md\r\n")
        with self.assertRaises(FileExistsError):
            revision.export(self.run_dir, self.judges_dir, dest)

    def test_export_retains_failed_generation_and_failed_reviewer_separately(self):
        self.prepare()
        sequence = [subprocess.CompletedProcess([], 1, events(), b"")] + [subprocess.CompletedProcess([], 0, events(), b"")] * 35
        self.run_fake(sequence)
        self.prepare_judges()
        self.run_fake(reviewer=True)
        result = revision.export(self.run_dir, self.judges_dir, self.root / "export")
        votes = result["statistics"]["consensus_pairwise"]["editorial"]["previous_vs_candidate"]
        self.assertEqual(votes["non_comparable"], 1)
        self.assertEqual(votes["missing_review"], 11)
        self.assertEqual(votes["tie"], 0)
        self.assertEqual(sum(votes.values()), 12)
        self.assertEqual(result["statistics"]["writers"]["baseline"]["statuses"]["failed"], 1)

    def test_export_rejects_different_rubric_and_forged_final(self):
        manifest = self.prepare()
        self.run_fake()
        revision.blind(self.run_dir, self.root / "blind")
        self.rubric.write_bytes(b"DIFFERENT RUBRIC")
        revision.judge_prepare(self.root / "blind/packet.json", self.rubric, self.judges_dir)
        self.run_fake(self.fake_judge, reviewer=True)
        with self.assertRaises(ValueError):
            revision.export(self.run_dir, self.judges_dir, self.root / "export")
        record_path = self.run_dir / "records" / (manifest["jobs"][0]["output_id"] + ".json")
        record = ab.read_json(record_path)
        record["final_text"] = "forged"
        record["final_sha256"] = ab.digest(b"forged")
        record_path.write_bytes(ab.json_bytes(record))
        with self.assertRaises(ValueError):
            revision.read_record(self.run_dir, manifest["jobs"][0])

    def test_public_export_refuses_local_paths_without_rewriting_text(self):
        self.prepare()
        synthetic_path = str(Path("/") / "Users" / "synthetic" / "private")
        self.run_fake([subprocess.CompletedProcess([], 0, events(synthetic_path), b"")] * 36)
        self.prepare_judges()
        self.run_fake(self.fake_judge, reviewer=True)
        with self.assertRaises(ValueError):
            revision.export(self.run_dir, self.judges_dir, self.root / "export")
        self.assertFalse((self.root / "export").exists())


class EditingRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dataset, self.protocol = self.root / "cases.json", self.root / "PROTOCOL.md"
        self.rubric, self.editor = self.root / "JUDGE.md", self.root / "EDITOR.md"
        self.run_dir, self.judges_dir = self.root / "run", self.root / "judges"
        self.cases = [{"id": "synthetic-" + str(index), "request": "Edit a synthetic report.",
                       "sources": [{"id": "s1", "content": "Synthetic fact.", "use": "public",
                                    "origin": "Synthetic fixture", "kind": "record"}],
                       "min_words": 2, "max_words": 10, "reviewer_notes": "SECRET REVIEW NOTE",
                       "draft": "  Original текст {}.\r\n\n".format(index),
                       "provenance": {"marker": "SECRET PROVENANCE"},
                       "expectation": {"marker": "SECRET EXPECTATION"}} for index in range(12)]
        self.dataset.write_bytes(ab.json_bytes({"dataset_id": "synthetic-editing",
                                                "description": "Offline fixture", "cases": self.cases}))
        self.protocol.write_bytes(b"FROZEN PROTOCOL\r\n")
        self.rubric.write_bytes(b"FROZEN JUDGE RUBRIC\r\n")
        self.editor.write_bytes(b"FROZEN EDITOR\r\n")

    def prepare(self, dest=None):
        return revision.edit_prepare(dest or self.run_dir, self.dataset, self.protocol, self.rubric, self.editor)

    def run_fake(self, side_effect=None, reviewer=False):
        method = revision.pair_judges.run if reviewer else revision.edit_run
        dest = self.judges_dir if reviewer else self.run_dir
        with patch.object(revision.shutil, "which", return_value="synthetic-codex"), patch.object(
                revision.subprocess, "run", side_effect=side_effect,
                return_value=subprocess.CompletedProcess([], 0, events(), b"private raw stderr")) as call, redirect_stdout(io.StringIO()):
            result = method(dest, execute=True)
        return result, call

    def prepare_judges(self, packet=None, rubric=None):
        if packet is None:
            revision.edit_blind(self.run_dir, self.root / "blind")
            packet = self.root / "blind/packet.json"
        return revision.pair_judges.prepare(packet, rubric or self.rubric, self.judges_dir)

    def fake_judge(self, command, **kwargs):
        prompt = kwargs["input"].decode("utf-8")
        self.assertNotIn("SECRET EXPECTATION", prompt)
        self.assertNotIn("SECRET PROVENANCE", prompt)
        self.assertIn("SECRET REVIEW NOTE", prompt)
        pairs = json.loads(prompt.split("Пары для оценки:\n")[1])["pairs"]
        reviews = []
        for pair in pairs:
            original = "X" if "Original" in pair["X"]["text"] else "Y"
            reviews.append({"pair_id": pair["pair_id"],
                            "X": {"status": "revision" if original == "X" else "pass", "findings": []},
                            "Y": {"status": "revision" if original == "Y" else "pass", "findings": []},
                            "editorial_preference": "Y" if original == "X" else "X",
                            "editorial_reason": "Synthetic improvement.", "practical_preference": "tie",
                            "practical_reason": "Synthetic tie."})
        final = json.dumps({"reviews": reviews, "limits": "Offline fake reviewer only."})
        return subprocess.CompletedProcess(command, 0, events(final), b"")

    def test_prepare_freezes_twelve_drafts_without_skill_or_analysis_in_editor_prompts(self):
        manifest = self.prepare()
        second = self.prepare(self.root / "second")
        self.assertEqual(manifest["kind"], "editing-regression")
        self.assertEqual(manifest["jobs"], second["jobs"])
        self.assertEqual(manifest["hashes"], second["hashes"])
        self.assertEqual(len(manifest["jobs"]), 12)
        self.assertEqual((self.run_dir / "snapshots/cases.json").read_bytes(), self.dataset.read_bytes())
        mapping = ab.read_json(self.run_dir / "mapping.json")["pairs"]
        self.assertEqual(Counter(pair["X"]["arm"] for pair in mapping), {"original": 6, "edited": 6})
        for case, job in zip(self.cases, manifest["jobs"]):
            prompt = (self.run_dir / job["prompt_path"]).read_bytes().decode("utf-8")
            self.assertIn(ab.COMMON, prompt)
            self.assertIn("FROZEN EDITOR\r\n", prompt)
            self.assertTrue(prompt.endswith(case["draft"]))
            self.assertNotIn("SECRET", prompt)
            self.assertNotIn("FROZEN PROTOCOL", prompt)
            self.assertNotIn("FROZEN JUDGE", prompt)
            self.assertEqual((self.run_dir / job["original_path"]).read_bytes(), case["draft"].encode("utf-8"))
        self.assertEqual(manifest["injected_documents"], ["editor.txt"])

    def test_astra_selection_is_frozen_executed_and_exported_without_changing_prompts(self):
        default = self.prepare(self.root / "default")
        argv = ["edit-prepare", "--dest", str(self.run_dir), "--dataset", str(self.dataset),
                "--protocol", str(self.protocol), "--rubric", str(self.rubric),
                "--editor", str(self.editor), "--model", "gpt-6-astra"]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(revision.main(argv), 0)
        manifest = revision.load_manifest(self.run_dir, "editing-regression")
        self.assertEqual(manifest["requested_model"], "gpt-6-astra")
        self.assertEqual(manifest["hashes"], default["hashes"])
        expected_args = list(ab.CLI_ARGS)
        expected_args[expected_args.index("--model") + 1] = "gpt-6-astra"
        self.assertEqual(manifest["cli_args"], expected_args)
        result, calls = self.run_fake()
        self.assertEqual(result["completed"], 12)
        for call in calls.call_args_list:
            self.assertEqual(call.args[0], ["synthetic-codex"] + expected_args)
        self.prepare_judges()
        self.run_fake(self.fake_judge, reviewer=True)
        dest = self.root / "export"
        exported = revision.edit_export(self.run_dir, self.judges_dir, dest)
        self.assertEqual({record["requested_model"] for record in exported["editor_records"]}, {"gpt-6-astra"})
        snapshots = ab.read_json(dest / "snapshots.json")
        self.assertEqual(snapshots["cli_args"], expected_args)
        self.assertEqual(snapshots["editor_manifest"], manifest)
        self.assertEqual(ab.CLI_ARGS[ab.CLI_ARGS.index("--model") + 1], "gpt-5.6-sol")

    def test_unknown_editor_model_is_rejected_before_creating_run_directory(self):
        with self.assertRaisesRegex(ValueError, "unsupported editor model"):
            revision.edit_prepare(self.run_dir, self.dataset, self.protocol, self.rubric,
                                  self.editor, model="unsupported-model")
        self.assertFalse(self.run_dir.exists())

    def test_resealed_inconsistent_editor_model_is_rejected_before_run_blind_and_export(self):
        manifest = self.prepare()
        unsupported = copy.deepcopy(manifest)
        unsupported["requested_model"] = "unsupported-model"
        changed_model = copy.deepcopy(manifest)
        changed_model["requested_model"] = "gpt-6-astra"
        changed_job = copy.deepcopy(manifest)
        changed_job["jobs"][0]["requested_model"] = "gpt-6-astra"
        changed_args = copy.deepcopy(manifest)
        changed_args["cli_args"][changed_args["cli_args"].index("--model") + 1] = "gpt-6-astra"
        for variant in (unsupported, changed_model, changed_job, changed_args):
            with self.subTest(model=variant["requested_model"], jobs=variant["jobs"][0]["requested_model"]):
                body = ab.json_bytes(variant)
                (self.run_dir / "manifest.json").write_bytes(body)
                (self.run_dir / "manifest.sha256").write_bytes((ab.digest(body) + "\n").encode("ascii"))
                with patch.object(revision.subprocess, "run") as call:
                    with self.assertRaises(ValueError):
                        revision.edit_run(self.run_dir, execute=True)
                    with self.assertRaises(ValueError):
                        revision.edit_blind(self.run_dir, self.root / "blind")
                    with self.assertRaises(ValueError):
                        revision.edit_export(self.run_dir, self.judges_dir, self.root / "export")
                call.assert_not_called()
                self.assertFalse((self.root / "blind").exists())
                self.assertFalse((self.root / "export").exists())

    def test_astra_record_cannot_claim_a_different_editor_model(self):
        manifest = revision.edit_prepare(self.run_dir, self.dataset, self.protocol, self.rubric,
                                         self.editor, model="gpt-6-astra")
        self.run_fake()
        job = manifest["jobs"][0]
        record_path = self.run_dir / "records" / (job["output_id"] + ".json")
        record = ab.read_json(record_path)
        record["requested_model"] = ab.MODEL
        record_path.write_bytes(ab.json_bytes(record))
        with self.assertRaisesRegex(ValueError, "frozen job"):
            revision.edit_blind(self.run_dir, self.root / "blind")

    def test_default_model_reproduces_both_historical_editor_manifests_exactly(self):
        for name, results_dir in (("reliability-next", "regression-results"),
                                  ("reliability-source-first-2026-09-07", "results")):
            with self.subTest(experiment=name):
                snapshots = ab.read_json(ROOT / "evals" / name / results_dir / "snapshots.json")
                historical = snapshots["editor_manifest"]
                files = snapshots["files"]
                self.dataset.write_bytes(files["snapshots/cases.json"]["text"].encode("utf-8"))
                self.protocol.write_bytes(files["snapshots/protocol.txt"]["text"].encode("utf-8"))
                self.rubric.write_bytes(files["snapshots/judge.txt"]["text"].encode("utf-8"))
                self.editor.write_bytes(files["snapshots/editor.txt"]["text"].encode("utf-8"))
                dest = self.root / name
                with patch.object(ab, "utc_now", return_value=historical["prepared_at_utc"]):
                    generated = self.prepare(dest)
                self.assertEqual(generated, historical)
                self.assertEqual((dest / "manifest.json").read_bytes(), ab.json_bytes(historical))
                self.assertEqual(revision._edit_inputs(dest)[0], historical)

    def test_invalid_case_count_and_duplicate_ids_are_rejected_before_prepare(self):
        data = ab.read_json(self.dataset)
        data["cases"].pop()
        self.dataset.write_bytes(ab.json_bytes(data))
        with self.assertRaises(ValueError):
            self.prepare()
        data["cases"].append(copy.deepcopy(data["cases"][0]))
        self.dataset.write_bytes(ab.json_bytes(data))
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.run_dir.exists())

    def test_execute_gate_and_each_frozen_input_tamper_prevent_calls(self):
        manifest = self.prepare()
        targets = ["manifest.json", "snapshots/cases.json", "snapshots/protocol.txt", "snapshots/judge.txt",
                   "snapshots/editor.txt", "mapping.json", manifest["jobs"][0]["original_path"],
                   manifest["jobs"][0]["prompt_path"]]
        with patch.object(revision.subprocess, "run") as call:
            with self.assertRaises(ValueError):
                revision.edit_run(self.run_dir)
            for relative in targets:
                with self.subTest(relative=relative):
                    path = self.run_dir / relative
                    original = path.read_bytes()
                    path.write_bytes(original + b"changed")
                    with self.assertRaises(ValueError):
                        revision.edit_run(self.run_dir, execute=True)
                    path.write_bytes(original)
        call.assert_not_called()

    def test_empty_instructions_and_invalid_source_contract_are_rejected(self):
        data = ab.read_json(self.dataset)
        bad_sources = [[], data["cases"][0]["sources"] * 2,
                       [{**data["cases"][0]["sources"][0], "kind": "unknown"}],
                       [{**data["cases"][0]["sources"][0], "content": "  "}]]
        for sources in bad_sources:
            with self.subTest(sources=sources):
                changed = copy.deepcopy(data)
                changed["cases"][0]["sources"] = sources
                self.dataset.write_bytes(ab.json_bytes(changed))
                with self.assertRaises(ValueError):
                    self.prepare()
                self.assertFalse(self.run_dir.exists())
        self.dataset.write_bytes(ab.json_bytes(data))
        for path in (self.protocol, self.rubric, self.editor):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(b" \r\n")
                with self.assertRaises(ValueError):
                    self.prepare()
                self.assertFalse(self.run_dir.exists())
                path.write_bytes(original)
        data["cases"][0]["reviewer_notes"] = ["SECRET REVIEW NOTE"]
        self.dataset.write_bytes(ab.json_bytes(data))
        self.assertEqual(len(self.prepare()["jobs"]), 12)

    def test_exactly_twelve_fresh_calls_and_terminal_records_are_not_repeated(self):
        manifest = self.prepare()
        directories = []

        def fake(command, **kwargs):
            directory = Path(kwargs["cwd"])
            self.assertEqual(list(directory.iterdir()), [])
            self.assertEqual(command, ["synthetic-codex"] + ab.CLI_ARGS)
            self.assertEqual(kwargs["input"], (self.run_dir / manifest["jobs"][len(directories)]["prompt_path"]).read_bytes())
            directories.append(directory)
            return subprocess.CompletedProcess(command, 0, events(), b"")

        result, call = self.run_fake(fake)
        self.assertEqual(result, {"completed": 12, "failed": 0, "executed_now": 12, "skipped": 0})
        self.assertEqual((call.call_count, len(set(directories))), (12, 12))
        self.assertFalse(any(path.exists() for path in directories))
        result, repeated = self.run_fake()
        repeated.assert_not_called()
        self.assertEqual(result["skipped"], 12)

    def test_unfinished_attempt_blocks_every_pending_call(self):
        manifest = self.prepare()
        ab.save(self.run_dir / "attempts" / (manifest["jobs"][3]["output_id"] + ".json"), b"{}")
        with patch.object(revision.subprocess, "run") as call, self.assertRaises(ValueError):
            revision.edit_run(self.run_dir, execute=True)
        call.assert_not_called()

    def test_complete_pair_review_export_preserves_bytes_and_normalizes_both_judges(self):
        self.prepare()
        self.run_fake()
        manifest = self.prepare_judges()
        self.assertEqual(len(manifest["jobs"]), 4)
        self.assertEqual(manifest["jobs"][0]["pair_ids"], ["p01", "p02", "p03", "p04", "p05", "p06"])
        self.assertEqual(manifest["jobs"][2]["pair_ids"], ["p12", "p11", "p10", "p09", "p08", "p07"])
        self.assertTrue(manifest["jobs"][2]["swapped_xy"])
        result, call = self.run_fake(self.fake_judge, reviewer=True)
        self.assertEqual((result["completed"], call.call_count), (4, 4))
        dest = self.root / "export"
        exported = revision.edit_export(self.run_dir, self.judges_dir, dest)
        self.assertEqual((len(exported["original_records"]), len(exported["editor_records"])), (12, 12))
        self.assertEqual(exported["statistics"]["total_measurements"]["input_tokens"]["observed_total"], 160)
        self.assertEqual(exported["quality_gate"], "not-adjudicated")
        self.assertEqual(len(list((dest / "outputs").glob("*.txt"))), 24)
        for case in self.cases:
            self.assertEqual((dest / ("outputs/" + case["id"] + "-original.txt")).read_bytes(), case["draft"].encode("utf-8"))
            self.assertEqual((dest / ("outputs/" + case["id"] + "-edited.txt")).read_bytes(), b"  Synthetic final.\r\n\n")
        for pair in exported["pairs"]:
            self.assertEqual(pair["consensus"], {"editorial": "edited", "practical": "tie"})
            self.assertEqual(pair["judges"]["judge1"]["statuses"], {"original": "revision", "edited": "pass"})
            self.assertEqual(pair["judges"]["judge2"]["statuses"], {"original": "revision", "edited": "pass"})
        self.assertFalse(any(row["model_attempt"] for row in exported["original_records"]))
        serialized = json.dumps(exported)
        self.assertNotIn(str(self.root), serialized)
        self.assertNotIn("private raw stderr", serialized)
        self.assertNotIn('"prompt":', serialized)

    def test_failed_edit_and_failed_judges_remain_in_twelve_pair_denominator(self):
        self.prepare()
        failure = subprocess.TimeoutExpired("synthetic", 180, output=b"partial")
        result, call = self.run_fake([failure] + [subprocess.CompletedProcess([], 0, events(), b"")] * 11)
        self.assertEqual((result["failed"], call.call_count), (1, 12))
        _, repeated = self.run_fake()
        repeated.assert_not_called()
        self.prepare_judges()
        packet = ab.read_json(self.judges_dir / "packet.json")
        self.assertEqual((len(packet["pairs"]), len(packet["not_comparable"])), (11, 1))
        self.assertNotIn("original_path", json.dumps(packet))
        self.assertNotIn("output_id", json.dumps(packet))
        self.run_fake(reviewer=True)
        exported = revision.edit_export(self.run_dir, self.judges_dir, self.root / "export")
        votes = exported["statistics"]["consensus"]["editorial"]
        self.assertEqual((votes["non_comparable"], votes["missing_review"], votes["tie"]), (1, 11, 0))
        self.assertEqual(sum(votes.values()), 12)
        self.assertIsNone(exported["editor_records"][0]["text_path"])
        self.assertEqual(exported["editor_records"][0]["status"], "failed")
        self.assertEqual(len(exported["editor_records"]), 12)

    def test_raw_events_and_forged_final_are_checked_against_cli_events(self):
        manifest = self.prepare()
        self.run_fake()
        self.prepare_judges()
        self.run_fake(self.fake_judge, reviewer=True)
        record_path = self.judges_dir / "judge1-batch1.record.json"
        record = ab.read_json(record_path)
        changed = record["final_text"].replace("Synthetic improvement.", "Forged improvement.")
        record.update(final_text=changed, final_sha256=ab.digest(changed.encode("utf-8")),
                      review=json.loads(changed), agent_messages=[changed])
        record_path.write_bytes(ab.json_bytes(record))
        with self.assertRaisesRegex(ValueError, "actual CLI events"):
            revision.edit_export(self.run_dir, self.judges_dir, self.root / "export")
        raw = self.run_dir / "raw" / (manifest["jobs"][0]["output_id"] + ".jsonl")
        raw.write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            revision.edit_blind(self.run_dir, self.root / "second-blind")

    def test_export_rejects_changed_reviewer_materials_and_original_final_mapping(self):
        manifest = self.prepare()
        self.run_fake()
        packet = revision.edit_blind(self.run_dir, self.root / "blind")
        packet["pairs"][0]["sources"][0]["content"] = "Forged source."
        changed = self.root / "changed-packet.json"
        changed.write_bytes(ab.json_bytes(packet))
        self.prepare_judges(packet=changed)
        self.run_fake(self.fake_judge, reviewer=True)
        with self.assertRaisesRegex(ValueError, "packet or rubric"):
            revision.edit_export(self.run_dir, self.judges_dir, self.root / "export")
        mapping = ab.read_json(self.run_dir / "mapping.json")
        mapping["pairs"][0]["case_id"] = self.cases[1]["id"]
        body = ab.json_bytes(mapping)
        (self.run_dir / "mapping.json").write_bytes(body)
        manifest["hashes"]["mapping.json"] = ab.digest(body)
        body = ab.json_bytes(manifest)
        (self.run_dir / "manifest.json").write_bytes(body)
        (self.run_dir / "manifest.sha256").write_bytes((ab.digest(body) + "\n").encode("ascii"))
        with self.assertRaisesRegex(ValueError, "original-to-final mapping"):
            revision.edit_blind(self.run_dir, self.root / "second-blind")

    def test_export_rejects_changed_rubric_and_resealed_judge_prompt(self):
        self.prepare()
        self.run_fake()
        revision.edit_blind(self.run_dir, self.root / "blind")
        self.rubric.write_bytes(b"DIFFERENT RUBRIC")
        manifest = self.prepare_judges(packet=self.root / "blind/packet.json")
        self.run_fake(self.fake_judge, reviewer=True)
        with self.assertRaisesRegex(ValueError, "packet or rubric"):
            revision.edit_export(self.run_dir, self.judges_dir, self.root / "export")
        # A valid, re-sealed review manifest still must match reconstructed prompts.
        self.judges_dir = self.root / "second-judges"
        self.rubric.write_bytes(b"FROZEN JUDGE RUBRIC\r\n")
        manifest = self.prepare_judges(packet=self.root / "blind/packet.json")
        prompt_path = self.judges_dir / "judge1-batch1.prompt.txt"
        body = prompt_path.read_bytes() + b"Extra judge instruction."
        prompt_path.write_bytes(body)
        manifest["jobs"][0]["prompt_sha256"] = ab.digest(body)
        body = ab.json_bytes(manifest)
        (self.judges_dir / "manifest.json").write_bytes(body)
        (self.judges_dir / "manifest.sha256").write_bytes((ab.digest(body) + "\n").encode("ascii"))
        with self.assertRaisesRegex(ValueError, "fixed blinded order"):
            revision.edit_export(self.run_dir, self.judges_dir, self.root / "export")


class RevisionArtifactTests(unittest.TestCase):
    def test_recorded_checker_reports_match_exact_public_outputs_and_sources(self):
        directory = ROOT / "evals/revision-2026-09-07"
        results_path = directory / "results/results.json"
        if not results_path.is_file():
            self.skipTest("public revision results have not been exported")
        recorded = ab.read_json(directory / "CHECKER.json")
        results = ab.read_json(results_path)
        snapshots = ab.read_json(directory / "results/snapshots.json")
        cases_bytes = (directory / "cases.json").read_bytes()
        cases = {case["id"]: case for case in ab.validate_cases(json.loads(cases_bytes.decode("utf-8")))}
        frozen_cases = snapshots["files"]["snapshots/cases.json"]
        self.assertEqual(ab.digest(cases_bytes), frozen_cases["sha256"])
        self.assertEqual(ab.digest(frozen_cases["text"].encode("utf-8")), frozen_cases["sha256"])
        script = ROOT / "skills/source-and-voice/scripts/editorial_check.py"
        self.assertEqual(ab.digest(script.read_bytes()), recorded["checker_sha256"])
        self.assertEqual(recorded["source_hash_basis"], "canonical_json")
        spec = importlib.util.spec_from_file_location("revision_artifact_checker", script)
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        identities = {(case, repeat, arm) for case in cases for repeat in (1, 2) for arm in revision.ARMS}
        reports = {(row["case_id"], row["repeat"], row["arm"]): row["diagnostic"] for row in recorded["records"]}
        writers = {(row["case_id"], row["repeat"], row["arm"]): row for row in results["writer_records"]}
        self.assertEqual(len(recorded["records"]), 36)
        self.assertEqual(len(results["writer_records"]), 36)
        self.assertEqual(set(reports), identities)
        self.assertEqual(set(writers), identities)
        for identity in sorted(identities):
            with self.subTest(identity=identity):
                writer, saved, case = writers[identity], reports[identity], cases[identity[0]]
                self.assertIsNotNone(writer["text_path"])
                body_path = directory / "results" / writer["text_path"]
                self.assertTrue(body_path.resolve().is_relative_to((directory / "results").resolve()))
                body_bytes = body_path.read_bytes()
                body_hash = ab.digest(body_bytes)
                self.assertEqual(body_hash, writer["final_sha256"])
                self.assertEqual(body_hash, saved["body_sha256"])
                sources = {"version": 1, "sources": case["sources"]}
                source_bytes = json.dumps(sources, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":"), allow_nan=False).encode("utf-8")
                self.assertEqual(ab.digest(source_bytes), saved["source_packet_sha256"])
                self.assertEqual(saved["source_packet_hash_basis"], "canonical_json")
                actual = checker.analyze(body_bytes.decode("utf-8"), sources=sources,
                                         min_words=case["min_words"], max_words=case["max_words"])
                self.assertTrue(actual == saved, "saved checker diagnostic differs from exact rerun")


if __name__ == "__main__":
    unittest.main()
