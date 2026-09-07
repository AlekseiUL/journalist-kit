#!/usr/bin/env python3
"""Copy one self-contained skill to an explicit destination; never overwrite."""

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
NAME = "source-and-voice"
FILES = (
    "SKILL.md",
    "LICENSE",
    "references/journalism.md",
    "references/editing.md",
    "references/voice.md",
    "references/review.md",
    "scripts/editorial_check.py",
    "assets/voice.example.json",
    "agents/openai.yaml",
)


def plan_install(destination, source=None):
    source = Path(source) if source is not None else ROOT / "skills" / NAME
    if source.is_symlink() or not source.is_dir():
        raise ValueError("Skill source must be a real directory")
    source = source.resolve()
    destination = Path(destination).expanduser().resolve()
    if destination.exists() and not destination.is_dir():
        raise ValueError("Destination must be a skills directory")
    if destination == source or source in destination.parents:
        raise ValueError("Cannot install inside the source skill")
    # resolve() does not normalize case on case-insensitive filesystems.
    # Check existing ancestors by identity before creating missing descendants.
    if any(parent.exists() and parent.samefile(source)
           for parent in (destination, *destination.parents)):
        raise ValueError("Cannot install inside the source skill")
    target = destination / NAME
    if target.exists() or target.is_symlink():
        raise FileExistsError("Skill already exists; no files were overwritten")
    inventory = []
    for relative in FILES:
        path = source / relative
        if any(part.is_symlink() for part in (path, *path.parents) if part != source.parent):
            raise ValueError("Symlinks are not allowed in the installable package")
        if not path.is_file() or not path.resolve().is_relative_to(source):
            raise ValueError("Incomplete skill package: " + relative)
        inventory.append({"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return source, target, inventory


def install(destination, *, apply=False, source=None):
    source, target, inventory = plan_install(destination, source)
    result = {"status": "DRY_RUN", "destination": str(target), "files": inventory}
    if not apply:
        return result
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive reservation is the overwrite guard, including a concurrent install.
    target.mkdir(exist_ok=False)
    try:
        for entry in inventory:
            relative = entry["path"]
            data = (source / relative).read_bytes()
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ValueError("Source changed during installation: " + relative)
            output = target / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("xb") as stream:
                stream.write(data)
        for entry in inventory:
            digest = hashlib.sha256((target / entry["path"]).read_bytes()).hexdigest()
            if digest != entry["sha256"]:
                raise ValueError("Installed file verification failed: " + entry["path"])
    except (OSError, ValueError) as exc:
        # This new folder belongs to this install, but keep it for inspection.
        # No recursive deletion or overwrite is used for recovery.
        raise ValueError("Installation incomplete; inspect the new destination before retrying") from exc
    result["status"] = "INSTALLED"
    result["restart_performed"] = False
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", required=True, help="Parent skills directory; no profile is selected automatically")
    parser.add_argument("--apply", action="store_true", help="Copy files (default: read-only preview)")
    args = parser.parse_args(argv)
    try:
        result = install(args.dest, apply=args.apply)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=True))
        return 2
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
