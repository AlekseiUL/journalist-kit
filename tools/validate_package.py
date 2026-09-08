#!/usr/bin/env python3
"""Offline package, link, fixture, and accidental-disclosure checks."""

import ast
import argparse
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote

from install import FILES, NAME


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {".git", ".local", ".venv", "__pycache__", "dist"}
MODES = {"angle_only", "interview_plan", "fact_gap_map", "sourced_explainer",
         "full_article", "investigation_brief"}
FORMS = {"straight_news", "explainer_service", "interview_profile",
         "reported_narrative", "analysis", "solutions"}
MAX_ENTRYPOINT_BYTES = 4_200
MAX_PUBLIC_IMAGE_BYTES = 5_000_000
JPEG_MAGIC = b"\xff\xd8\xff"
MIT_HEADER = "MIT License\n\nCopyright (c) 2026 Aleksei Ulianov / Sprut_AI\n"
PUBLIC_RESOURCES = "\n".join((
    "- YouTube: https://youtube.com/@alekseiulianov",
    "- Telegram SPRUT_AI: https://t.me/Sprut_AI",
    "- Telegram chat: https://t.me/+eH-qNIDmud8zNDZi",
    "- AI Операционка: https://t.me/tribute/app?startapp=sJyg",
    "- GitHub: https://github.com/AlekseiUL",
))
SENSITIVE = (
    ("personal absolute path", re.compile(r"[/]Users[/][\w.-]+[/]|[/]home[/][\w.-]+[/]")),
    ("private production identifier", re.compile(r"MIKE[_]CENTER|mike[-](?:smm|hank|tuco)|alexey[-]voice[-]pack")),
    ("GitHub credential", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,}")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("Telegram credential", re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b")),
)


def public_files(root):
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in EXCLUDED for part in relative.parts):
            continue
        if path.is_symlink():
            yield path
        elif path.is_file() and path.suffix not in {".pyc", ".pyo"}:
            yield path


def validate(root=ROOT, *, release=False):
    root = Path(root).resolve()
    failures = []
    skill = root / "skills" / NAME
    required = ["README.md", "README.en.md", "LICENSE", "SECURITY.md", "THIRD_PARTY_NOTICES.md",
                "docs/integrations.md", "docs/evaluation.md", "docs/golden-path.md",
                "evals/cases.json", "evals/next-source-first-holdout/PROTOCOL.md",
                ".github/workflows/check.yml"]
    required.extend("skills/" + NAME + "/" + relative for relative in FILES)
    for relative in required:
        if not (root / relative).is_file():
            failures.append("missing: " + relative)

    for path in public_files(root):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            failures.append("symlink: " + relative)
            continue
        if path.name.startswith(".env") or path.suffix in {".db", ".sqlite", ".sqlite3", ".log", ".pem"}:
            failures.append("private artifact type: " + relative)
        if path.suffix.lower() in {".jpg", ".jpeg"}:
            try:
                image = path.read_bytes()
            except OSError:
                failures.append("unreadable public JPEG: " + relative)
                continue
            if (len(image) > MAX_PUBLIC_IMAGE_BYTES or
                    not image.startswith(JPEG_MAGIC) or not image.endswith(b"\xff\xd9")):
                failures.append("invalid or oversized public JPEG: " + relative)
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            failures.append("non-UTF-8 or unreadable public file: " + relative)
            continue
        for label, pattern in SENSITIVE:
            if pattern.search(text):
                failures.append(label + ": " + relative)
        if path.suffix == ".py":
            try:
                ast.parse(text, filename=relative, feature_version=(3, 10))
            except SyntaxError:
                failures.append("Python 3.10 syntax: " + relative)
        if path.suffix == ".json":
            try:
                json.loads(text)
            except ValueError:
                failures.append("invalid JSON: " + relative)
        if path.suffix == ".md":
            for match in re.finditer(r"\[[^\]\n]*\]\(([^\s)]+)\)", text):
                target = match.group(1).strip("<>")
                if target.startswith(("https://", "http://", "mailto:", "#")):
                    continue
                target = unquote(target.split("#", 1)[0])
                resolved = (path.parent / target).resolve()
                if not resolved.is_relative_to(root) or not resolved.exists():
                    failures.append("broken/nonportable link: " + relative + " -> " + target)

    entry = skill / "SKILL.md"
    if entry.is_file():
        body = entry.read_text(encoding="utf-8")
        parts = body.split("---", 2)
        if not body.startswith("---\n") or len(parts) != 3:
            failures.append("missing skill YAML frontmatter")
        else:
            if not re.search(r"(?m)^name:\s*source-and-voice\s*$", parts[1]):
                failures.append("skill name/folder mismatch")
            if not re.search(r"(?m)^description:\s*\S", parts[1]):
                failures.append("missing discovery description")
            if len(body.splitlines()) > 220:
                failures.append("entrypoint exceeds the package's 220-line context budget")
            if len(body.encode("utf-8")) > MAX_ENTRYPOINT_BYTES:
                failures.append(f"entrypoint exceeds the package's {MAX_ENTRYPOINT_BYTES}-byte context budget")
        for reference in ("journalism.md", "editing.md", "voice.md", "review.md"):
            if "references/" + reference not in body:
                failures.append("undiscoverable reference: " + reference)
        if "templates/source-fidelity-review.md" not in body:
            failures.append("undiscoverable template: source-fidelity-review.md")

    cases_path = root / "evals" / "cases.json"
    if cases_path.is_file():
        try:
            data = json.loads(cases_path.read_text(encoding="utf-8"))
            cases = data["cases"]
            if data["version"] != 1 or not isinstance(cases, list) or not cases:
                raise ValueError("empty/unsupported cases")
            ids = [case["id"] for case in cases]
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate case ID")
            if not MODES.issubset({case["mode"] for case in cases}):
                raise ValueError("missing journalism mode")
            if not FORMS.issubset({case.get("form") for case in cases}):
                raise ValueError("missing journalism form")
            for case in cases:
                if not case["request"] or not case["rubric"] or not case["failure_conditions"]:
                    raise ValueError("incomplete case: " + case["id"])
                for source in case["sources"]:
                    if set(source) != {"id", "content", "origin", "kind", "use"}:
                        raise ValueError("source schema: " + case["id"])
                    if source["kind"] not in {"record", "statement", "author_note"}:
                        raise ValueError("source kind: " + case["id"])
                    if source["use"] not in {"public", "paraphrase_only", "private"}:
                        raise ValueError("source permission: " + case["id"])
        except (KeyError, TypeError, ValueError) as exc:
            failures.append("evaluation suite: " + str(exc))

    root_license = root / "LICENSE"
    skill_license = skill / "LICENSE"
    if root_license.is_file() and skill_license.is_file():
        if root_license.read_bytes() != skill_license.read_bytes():
            failures.append("root/installed license mismatch")
        if release:
            license_text = root_license.read_text(encoding="utf-8")
            if "decision pending" in license_text.lower():
                failures.append("public release blocked: license decision is pending")
            elif not license_text.startswith(MIT_HEADER) or "Permission is hereby granted" not in license_text:
                failures.append("public release blocked: standard MIT license missing")
    if release:
        for relative in ("README.md", "README.en.md"):
            readme = root / relative
            if readme.is_file():
                text = readme.read_text(encoding="utf-8")
                if not text.rstrip().endswith(PUBLIC_RESOURCES):
                    failures.append("public release blocked: canonical resources missing or not final: " + relative)
                if "MIT License" not in text:
                    failures.append("public release blocked: MIT claim missing: " + relative)
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", action="store_true", help="Also reject an undecided distribution license")
    args = parser.parse_args()
    failures = validate(release=args.release)
    print(json.dumps({"status": "FAIL" if failures else "PASS", "failures": failures,
                      "scope": "package_structure_links_syntax_fixtures_and_disclosure_patterns",
                      "not_proven": ["editorial_quality", "source_truth", "complete_secret_detection"]},
                     ensure_ascii=True, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
