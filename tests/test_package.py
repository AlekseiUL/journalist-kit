import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
try:
    SPEC = importlib.util.spec_from_file_location("package_validation", ROOT / "tools" / "validate_package.py")
    validator = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(validator)
finally:
    sys.path.pop(0)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for path in validator.public_files(ROOT):
            if not path.is_symlink():
                target = self.root / path.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())

    def test_real_package_is_complete(self):
        self.assertEqual(validator.validate(self.root), [])

    def test_source_first_candidate_contract_is_discoverable(self):
        skill = (self.root / "skills/source-and-voice/SKILL.md").read_text(encoding="utf-8")
        self.assertIn('version: "0.2.0-alpha.2"', skill)
        self.assertIn("templates/source-fidelity-review.md", skill)
        self.assertIn("редактируй его, а не переписывай заново", skill)
        self.assertIn("Каждая новая конкретная деталь", skill)
        self.assertIn("Не загружай справочник", skill)
        self.assertIn("соблюди заданный объём", skill)
        self.assertLessEqual(len(skill.encode("utf-8")), 4200)

    def test_oversized_entrypoint_is_rejected(self):
        path = self.root / "skills/source-and-voice/SKILL.md"
        with path.open("a", encoding="utf-8") as stream:
            stream.write("x" * validator.MAX_ENTRYPOINT_BYTES)
        self.assertTrue(any("byte context budget" in error for error in validator.validate(self.root)))

    def test_next_holdout_has_locked_value_and_cost_gates(self):
        protocol = (self.root / "evals/next-source-first-holdout/PROTOCOL.md").read_text(encoding="utf-8")
        self.assertIn("0 confirmed substantive meaning distortions", protocol)
        self.assertIn("at least 10 of all 12 pairs", protocol)
        self.assertIn("exactly 28 model calls with no retry", protocol)
        self.assertIn("no more than 15%", protocol)
        self.assertIn("Do not inject all references by default", protocol)

    def test_release_surfaces_are_required(self):
        (self.root / "SECURITY.md").unlink()
        self.assertIn("missing: SECURITY.md", validator.validate(self.root))

    def test_broken_relative_link_is_detected(self):
        with (self.root / "README.md").open("a", encoding="utf-8") as handle:
            handle.write("\n[missing](docs/missing.md)\n")
        self.assertTrue(any("broken/nonportable link" in error for error in validator.validate(self.root)))

    def test_invalid_public_jpeg_is_detected(self):
        image = self.root / "assets" / "journalist-kit-cover.jpg"
        image.write_bytes(b"not a jpeg")
        self.assertIn("invalid or oversized public JPEG: assets/journalist-kit-cover.jpg",
                      validator.validate(self.root))

    def test_nonportable_path_is_detected_without_scanning_local_notes(self):
        (self.root / "unsafe.md").write_text("/" + "Users/example/private.txt", encoding="utf-8")
        errors = validator.validate(self.root)
        self.assertIn("personal absolute path: unsafe.md", errors)
        (self.root / "unsafe.md").unlink()
        (self.root / ".local").mkdir()
        (self.root / ".local" / "note.md").write_text("/" + "Users/example/private.txt", encoding="utf-8")
        self.assertEqual(validator.validate(self.root), [])

    def test_unsupported_evaluation_source_schema_is_detected(self):
        path = self.root / "evals" / "cases.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["cases"][0]["sources"][0]["unexpected"] = True
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assertTrue(any("source schema" in error for error in validator.validate(self.root)))

    def test_license_mismatch_is_detected(self):
        (self.root / "LICENSE").write_text("different license", encoding="utf-8")
        self.assertIn("root/installed license mismatch", validator.validate(self.root))

    def test_release_rejects_pending_license(self):
        for path in (self.root / "LICENSE", self.root / "skills/source-and-voice/LICENSE"):
            path.write_text("Publication and licensing decision pending.\n", encoding="utf-8")
        self.assertEqual(validator.validate(self.root), [])
        self.assertIn("public release blocked: license decision is pending",
                      validator.validate(self.root, release=True))

    def test_release_requires_mit_and_bilingual_resources(self):
        (self.root / "LICENSE").write_text("Custom license\n", encoding="utf-8")
        (self.root / "skills/source-and-voice/LICENSE").write_text("Custom license\n", encoding="utf-8")
        self.assertIn("public release blocked: standard MIT license missing",
                      validator.validate(self.root, release=True))

        for relative in ("LICENSE", "skills/source-and-voice/LICENSE"):
            (self.root / relative).write_bytes((ROOT / relative).read_bytes())
        readme = self.root / "README.en.md"
        readme.write_text(readme.read_text(encoding="utf-8").replace(
            "- GitHub: https://github.com/AlekseiUL", "- GitHub: missing"), encoding="utf-8")
        self.assertIn("public release blocked: canonical resources missing or not final: README.en.md",
                      validator.validate(self.root, release=True))

    def test_api_does_not_call_itself_a_quality_evaluation(self):
        run = subprocess.run([sys.executable, str(self.root / "tools/validate_package.py")],
                             cwd=self.root, capture_output=True, text=True, check=False)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result["status"], "PASS")
        self.assertIn("source_truth", result["not_proven"])
        self.assertIn("editorial_quality", result["not_proven"])

    def test_public_demo_runs_with_supplied_source_schema(self):
        script = self.root / "skills/source-and-voice/scripts/editorial_check.py"
        example = self.root / "examples/library-hours"
        run = subprocess.run([sys.executable, str(script), str(example / "after.txt"),
                              "--sources", str(example / "sources.json")],
                             cwd=self.root, capture_output=True, text=True, check=False)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(json.loads(run.stdout)["source_review"]["status"], "NOT_REVIEWED")

    def test_all_evaluation_packets_are_accepted_by_the_shipped_checker(self):
        checker_path = self.root / "skills/source-and-voice/scripts/editorial_check.py"
        spec = importlib.util.spec_from_file_location("fixture_checker", checker_path)
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        data = json.loads((self.root / "evals/cases.json").read_text(encoding="utf-8"))
        for case in data["cases"]:
            with self.subTest(case=case["id"]):
                result = checker.analyze("Fixture schema check.", {"version": 1, "sources": case["sources"]})
                self.assertEqual(result["exit_code"], 0)
                self.assertEqual(result["source_review"]["status"], "NOT_REVIEWED")

    def test_recorded_smoke_lengths_are_measured_from_exact_public_files(self):
        for name, expected in (("article", 71), ("neutral", 48),
                               ("conversational", 42), ("solutions", 340)):
            with self.subTest(case=name):
                path = self.root / "evals/smoke/outputs" / (name + ".txt")
                self.assertEqual(len(path.read_bytes().decode("utf-8").split()), expected)


if __name__ == "__main__":
    unittest.main()
