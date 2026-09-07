import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("skill_install", ROOT / "tools" / "install.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / "source"
        self.source.mkdir()
        for relative in installer.FILES:
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("test fixture: " + relative + "\n", encoding="utf-8")
        self.destination = self.base / "skills"

    def test_preview_does_not_create_destination(self):
        result = installer.install(self.destination, source=self.source)
        self.assertEqual(result["status"], "DRY_RUN")
        self.assertFalse(self.destination.exists())

    def test_install_copies_all_bytes_and_excludes_unlisted_files(self):
        (self.source / "private.txt").write_text("not installed", encoding="utf-8")
        result = installer.install(self.destination, source=self.source, apply=True)
        self.assertEqual(result["status"], "INSTALLED")
        target = self.destination / installer.NAME
        actual = {str(p.relative_to(target)) for p in target.rglob("*") if p.is_file()}
        self.assertEqual(actual, set(installer.FILES))
        self.assertEqual((target / "SKILL.md").read_bytes(), (self.source / "SKILL.md").read_bytes())

    def test_existing_skill_is_never_overwritten(self):
        installer.install(self.destination, source=self.source, apply=True)
        target = self.destination / installer.NAME / "SKILL.md"
        target.write_text("user change", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            installer.install(self.destination, source=self.source, apply=True)
        self.assertEqual(target.read_text(encoding="utf-8"), "user change")

    def test_unrelated_skill_is_preserved(self):
        other = self.destination / "other"
        other.mkdir(parents=True)
        (other / "SKILL.md").write_text("other skill", encoding="utf-8")
        installer.install(self.destination, source=self.source, apply=True)
        self.assertEqual((other / "SKILL.md").read_text(encoding="utf-8"), "other skill")

    def test_missing_file_fails_before_destination_creation(self):
        (self.source / "SKILL.md").unlink()
        with self.assertRaises(ValueError):
            installer.install(self.destination, source=self.source, apply=True)
        self.assertFalse(self.destination.exists())

    def test_symlinked_file_is_rejected(self):
        path = self.source / "SKILL.md"
        path.unlink()
        path.symlink_to(self.source / "LICENSE")
        with self.assertRaises(ValueError):
            installer.install(self.destination, source=self.source, apply=True)
        self.assertFalse(self.destination.exists())

    def test_symlinked_directory_is_rejected(self):
        original = self.source / "references"
        moved = self.base / "references"
        original.rename(moved)
        original.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(ValueError):
            installer.install(self.destination, source=self.source, apply=True)

    def test_dangling_destination_symlink_is_not_followed(self):
        self.destination.mkdir()
        (self.destination / installer.NAME).symlink_to(self.base / "missing")
        with self.assertRaises(FileExistsError):
            installer.install(self.destination, source=self.source, apply=True)
        self.assertFalse((self.base / "missing").exists())

    def test_install_inside_source_is_rejected(self):
        with self.assertRaises(ValueError):
            installer.install(self.source / "nested", source=self.source, apply=True)

    def test_case_alias_inside_source_is_rejected(self):
        alias = self.source.parent / self.source.name.upper()
        if not alias.exists() or not alias.samefile(self.source):
            self.skipTest("case-sensitive filesystem")
        with self.assertRaises(ValueError):
            installer.install(alias / "nested", source=self.source, apply=True)
        self.assertFalse((self.source / "nested").exists())

    def test_cli_handles_unicode_destination_with_ascii_stdout(self):
        environment = dict(os.environ, PYTHONIOENCODING="ascii")
        run = subprocess.run([sys.executable, str(ROOT / "tools" / "install.py"),
                              "--dest", str(self.base / "навыки")],
                             env=environment, capture_output=True, check=False)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)["status"], "DRY_RUN")

    def test_file_as_destination_is_rejected(self):
        self.destination.write_text("user data", encoding="utf-8")
        with self.assertRaises(ValueError):
            installer.install(self.destination, source=self.source, apply=True)

    def test_self_contained_real_install_runs_from_another_working_directory(self):
        destination = self.base / "real skills"
        result = installer.install(destination, apply=True)
        script = Path(result["destination"]) / "scripts" / "editorial_check.py"
        text = self.base / "article.txt"
        text.write_bytes("Библиотека открыта до 20:00.\r\n".encode("utf-8"))
        run = subprocess.run([sys.executable, str(script), str(text)], cwd=self.base,
                             capture_output=True, text=True, check=False)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("body_sha256", run.stdout)


if __name__ == "__main__":
    unittest.main()
