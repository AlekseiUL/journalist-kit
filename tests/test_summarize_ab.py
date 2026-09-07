"""Synthetic, offline integrity and aggregation checks; no model calls."""

import json
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import run_ab as ab
import review_ab as judges
import summarize_ab as summary


SYNTHETIC_LOCAL = "/" + "Users/example"


class SummaryTests(unittest.TestCase):
    def fixture(self, base, failed_writer=False, failed_judge=False, failed_pairs=0):
        root, run, review = base / "fixture", base / "run", base / "judges"
        dataset = {"cases": [{"id": "case_" + str(index), "request": "Короткий вымышленный текст.",
                              "sources": [{"id": "s", "content": "Вымышленная запись.",
                                           "origin": "Тест", "kind": "record", "use": "public"}],
                              "min_words": 2, "max_words": 8, "reviewer_notes": {}}
                             for index in range(6)]}
        data_path = root / "evals/ab-2026-09-07/cases.json"
        ab.save(data_path, ab.json_bytes(dataset))
        for name in ab.SKILL_FILES:
            ab.save(root / "skills/source-and-voice" / name, ("Тестовый документ " + name + "\n").encode("utf-8"))
        manifest = ab.prepare(run, dataset=data_path, root=root)
        exact = "  Ёж и ёлка.\r\nВторая строка!  \n"
        mapping = ab.read_json(run / "mapping.json")["pairs"]
        failed_ids = {pair["X"]["output_id"] for pair in mapping[:failed_pairs]}
        if failed_writer:
            failed_ids.add(manifest["jobs"][0]["output_id"])
        for job in manifest["jobs"]:
            final = None if job["output_id"] in failed_ids else exact
            record = {"output_id": job["output_id"], "case_id": job["case_id"], "repeat": job["repeat"],
                      "status": "failed" if final is None else "completed", "flags": ["timeout"] if final is None else [],
                      "prompt": (run / job["prompt_path"]).read_bytes().decode("utf-8"),
                      "prompt_sha256": job["prompt_sha256"], "final_text": final,
                      "final_sha256": ab.digest(final.encode("utf-8")) if final is not None else None,
                      "word_count": len(final.split()) if final is not None else None,
                      "requested_model": ab.MODEL, "resolved_model": None,
                      "usage": {"input_tokens": 100, "cached_input_tokens": 25, "output_tokens": 10},
                      "wall_seconds": 1.25, "agent_messages": ["PRIVATE AGENT MESSAGE"],
                      "raw_error": "PRIVATE ERROR " + SYNTHETIC_LOCAL + "/private-file", "thread_id": "PRIVATE THREAD"}
            ab.save(run / "records" / (job["output_id"] + ".json"), ab.json_bytes(record))
        packet_dir = base / "packet"
        ab.blind(run, packet_dir)
        rubric = base / "rubric.txt"
        ab.save(rubric, "Синтетическая рубрика.\n".encode("utf-8"))
        judge_manifest = judges.prepare(packet_dir / "packet.json", rubric, review)
        for job in judge_manifest["jobs"]:
            data = {"reviews": [], "limits": "Синтетическая проверка экспортёра."}
            for pair_id in job["pair_ids"]:
                preferred = "Y" if job["swapped_xy"] else "X"
                statuses = {"X": "revision", "Y": "pass"} if job["swapped_xy"] else {"X": "pass", "Y": "revision"}
                data["reviews"].append({"pair_id": pair_id,
                                        "X": {"status": statuses["X"], "findings": []},
                                        "Y": {"status": statuses["Y"], "findings": []},
                                        "editorial_preference": preferred, "editorial_reason": "Точнее мысль.",
                                        "practical_preference": "tie", "practical_reason": "Оба пригодны."})
            final = " \n" + json.dumps(data, ensure_ascii=False) + "\n\n"
            failed = failed_judge and job["job_id"] == "judge2-batch1"
            raw, stderr = b"PRIVATE RAW THREAD EVENT", ("PRIVATE STDERR " + SYNTHETIC_LOCAL + "/key").encode("utf-8")
            record = {**job, "status": "failed" if failed else "completed",
                      "flags": ["timeout"] if failed else [], "review": None if failed else data,
                      "prompt": (review / (job["job_id"] + ".prompt.txt")).read_bytes().decode("utf-8"),
                      "final_text": final, "final_sha256": ab.digest(final.encode("utf-8")),
                      "raw_stdout_sha256": ab.digest(raw), "raw_stderr_sha256": ab.digest(stderr),
                      "usage": {"input_tokens": 200, "cached_input_tokens": 0, "output_tokens": 20},
                      "wall_seconds": 2.0, "agent_messages": ["PRIVATE JUDGE MESSAGE"],
                      "thread_id": "PRIVATE JUDGE THREAD", "resolved_model": None}
            ab.save(review / (job["job_id"] + ".raw.jsonl"), raw)
            ab.save(review / (job["job_id"] + ".stderr.txt"), stderr)
            ab.save(review / (job["job_id"] + ".record.json"), ab.json_bytes(record))
        return run, review, exact

    def test_reversal_maps_both_statuses_and_preferences(self):
        mapping = {"X": {"arm": "baseline"}, "Y": {"arm": "treatment"}}
        review = {"X": {"status": "critical"}, "Y": {"status": "pass"},
                  "editorial_preference": "Y", "editorial_reason": "Reason.",
                  "practical_preference": "X", "practical_reason": "Reason."}
        normal = summary.normalize_review(review, mapping, swapped_xy=True)
        self.assertEqual(normal["statuses"], {"treatment": "critical", "baseline": "pass"})
        self.assertEqual(normal["editorial_preference"], "baseline")
        self.assertEqual(normal["practical_preference"], "treatment")

    def test_consensus_keeps_tie_disagreement_and_missing_distinct(self):
        self.assertEqual(summary.consensus("tie", "tie"), "tie")
        self.assertEqual(summary.consensus("baseline", "treatment"), "disagreement")
        self.assertEqual(summary.consensus("tie", "baseline"), "disagreement")
        self.assertEqual(summary.consensus(None, "baseline"), "missing_review")
        self.assertEqual(summary.consensus(None, None), "missing_review")
        self.assertEqual(summary.consensus("baseline", "baseline", comparable=False), "non_comparable")

    def test_export_preserves_exact_utf8_and_excludes_private_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            run, review, exact = self.fixture(base)
            dest = base / "export"
            result = summary.summarize(run, review, dest)
            self.assertEqual(len(result["pairs"]), 12)
            self.assertEqual(result["statistics"]["consensus"]["practical"]["tie"], 12)
            for pair in result["pairs"]:
                self.assertEqual(pair["judges"]["judge1"]["statuses"], pair["judges"]["judge2"]["statuses"])
                self.assertIn(pair["consensus"]["editorial"], summary.ARMS)
            self.assertEqual(len(list((dest / "outputs").glob("*.txt"))), 24)
            for path in (dest / "outputs").glob("*.txt"):
                self.assertEqual(path.read_bytes(), exact.encode("utf-8"))
            for record in result["judge_records"]:
                original = ab.read_json(review / (record["job_id"] + ".record.json"))
                self.assertEqual(record["final_text"], original["final_text"])
                self.assertEqual(record["review"], original["review"])
            public = (dest / "results.json").read_text(encoding="utf-8")
            self.assertNotIn("PRIVATE", public)
            self.assertNotIn(SYNTHETIC_LOCAL, public)
            self.assertNotIn('"prompt":', public)
            self.assertTrue(all(record["resolved_model"] == "unknown" for record in result["writer_records"]))
            self.assertEqual(result["statistics"]["writers"]["baseline"]["measurements"]["input_tokens"]["observed_total"], 1200)
            self.assertIsNone(result["statistics"]["writers"]["baseline"]["measurements"]["reasoning_output_tokens"]["observed_total"])
            snapshots = ab.read_json(dest / "snapshots.json")
            self.assertEqual(snapshots["common_wrapper"], ab.COMMON)
            self.assertEqual(snapshots["cli_args"], ab.CLI_ARGS)

    def test_failed_pair_and_missing_judge_remain_in_denominators(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            run, review, _ = self.fixture(base, failed_writer=True, failed_judge=True)
            result = summary.summarize(run, review, base / "export")
            self.assertEqual(len(result["pairs"]), 12)
            self.assertEqual(result["statistics"]["denominators"]["writer_attempts"], 24)
            for dimension in ("editorial", "practical"):
                values = result["statistics"]["consensus"][dimension]
                self.assertEqual(values["non_comparable"], 1)
                self.assertEqual(values["missing_review"], 6)
                self.assertEqual(sum(values.values()), 12)
            self.assertEqual(sum(not pair["comparable"] for pair in result["pairs"]), 1)
            self.assertEqual(sum(record["status"] == "failed" for record in result["writer_records"]), 1)

    def test_existing_destination_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory) / "export"
            dest.mkdir()
            with self.assertRaises(FileExistsError):
                summary.summarize(Path(directory) / "absent", Path(directory) / "absent2", dest)
            self.assertEqual(list(dest.iterdir()), [])

    def test_six_comparable_pairs_require_only_two_judge_records(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            run, review, _ = self.fixture(base, failed_pairs=6)
            result = summary.summarize(run, review, base / "export")
            self.assertEqual(len(result["judge_records"]), 2)
            self.assertEqual(len(result["pairs"]), 12)
            self.assertEqual(result["statistics"]["denominators"]["writer_attempts"], 24)
            self.assertEqual(sum(pair["comparable"] for pair in result["pairs"]), 6)
            for dimension in ("editorial", "practical"):
                values = result["statistics"]["consensus"][dimension]
                self.assertEqual(values["non_comparable"], 6)
                self.assertEqual(values["missing_review"], 0)
                self.assertEqual(sum(values.values()), 12)
            self.assertEqual(result["statistics"]["consensus"]["practical"]["tie"], 6)

    def test_zero_comparable_pairs_need_no_judge_records(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            run, review, _ = self.fixture(base, failed_pairs=12)
            result = summary.summarize(run, review, base / "export")
            self.assertEqual(result["judge_records"], [])
            self.assertEqual(len(result["pairs"]), 12)
            self.assertEqual(len(result["writer_records"]), 24)
            self.assertFalse(any(pair["comparable"] for pair in result["pairs"]))
            for dimension in ("editorial", "practical"):
                values = result["statistics"]["consensus"][dimension]
                self.assertEqual(values["non_comparable"], 12)
                self.assertEqual(values["missing_review"], 0)
                self.assertEqual(sum(values.values()), 12)
            for judge in result["statistics"]["judges"].values():
                self.assertEqual(judge["pair_denominator"], 12)
                self.assertIsNone(judge["measurements"]["input_tokens"]["observed_total"])

    def test_tampered_final_or_missing_terminal_record_refuses_export(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            run, review, _ = self.fixture(base)
            record_path = next((run / "records").glob("*.json"))
            record = ab.read_json(record_path)
            record["final_text"] += "Подмена."
            record_path.write_bytes(ab.json_bytes(record))
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                summary.summarize(run, review, base / "export")
            self.assertFalse((base / "export").exists())
            record_path.unlink()
            with self.assertRaisesRegex(ValueError, "24 terminal"):
                summary.summarize(run, review, base / "export")


if __name__ == "__main__":
    unittest.main()
