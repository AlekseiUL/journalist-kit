#!/usr/bin/env python3
"""Opt-in blinded model review for the fixed A/B pilot; never called by the skill."""

import argparse
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import run_ab as ab


# Two independently blinded invocations use the same known-available model so
# reviewer differences are not confounded with an unverified backend.
JUDGES = ("gpt-5.6-sol", "gpt-5.6-sol")
TIMEOUT = 300


def prepare(packet_path, rubric_path, dest):
    packet_bytes = Path(packet_path).read_bytes()
    rubric_bytes = Path(rubric_path).read_bytes()
    packet = json.loads(packet_bytes.decode("utf-8"))
    pairs = packet["pairs"]
    if not isinstance(pairs, list) or len(pairs) > 12:
        raise ValueError("pilot is bounded to a list of at most 12 pairs")
    pair_ids = [pair.get("pair_id") for pair in pairs if isinstance(pair, dict)]
    if (len(pair_ids) != len(pairs) or any(not isinstance(key, str) or not key for key in pair_ids)
            or len(set(pair_ids)) != len(pair_ids)):
        raise ValueError("packet must contain unique nonempty pair IDs")
    for pair in pairs:
        for label in ("X", "Y"):
            if not isinstance(pair.get(label), dict) or not isinstance(pair[label].get("text"), str):
                raise ValueError("each pair must contain X/Y text")
    rubric = rubric_bytes.decode("utf-8")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=False, mode=0o700)
    ab.save(dest / "packet.json", packet_bytes)
    ab.save(dest / "rubric.txt", rubric_bytes)
    jobs = []
    for index, model in enumerate(JUDGES, 1):
        selected = copy.deepcopy(pairs)
        if index == 2:
            selected.reverse()
            for pair in selected:
                pair["X"], pair["Y"] = pair["Y"], pair["X"]
        for start in range(0, len(selected), 6):
            batch = selected[start:start + 6]
            job_id = "judge{}-batch{}".format(index, start // 6 + 1)
            prompt = (rubric + "\n\nПары для оценки:\n" +
                      json.dumps({"pairs": batch}, ensure_ascii=False, indent=2) + "\n")
            ab.save(dest / (job_id + ".prompt.txt"), prompt.encode("utf-8"))
            jobs.append({"job_id": job_id, "requested_model": model,
                         "swapped_xy": index == 2,
                         "pair_ids": [pair["pair_id"] for pair in batch],
                         "prompt_sha256": ab.digest(prompt.encode("utf-8"))})
    manifest = {"schema_version": 1, "jobs": jobs, "timeout_seconds": TIMEOUT,
                "cli_args": ab.CLI_ARGS,
                "rubric_sha256": ab.digest(rubric_bytes),
                "packet_sha256": ab.digest(packet_bytes),
                "prepared_at_utc": ab.utc_now()}
    manifest_bytes = ab.json_bytes(manifest)
    ab.save(dest / "manifest.json", manifest_bytes)
    ab.save(dest / "manifest.sha256", (ab.digest(manifest_bytes) + "\n").encode("ascii"))
    return manifest


def parse_review(text, pair_ids):
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) != {"reviews", "limits"}:
        raise ValueError("review must contain reviews and limits")
    if not isinstance(data["limits"], str) or not data["limits"].strip():
        raise ValueError("limits must be a nonempty string")
    reviews = data["reviews"]
    if not isinstance(reviews, list) or not all(isinstance(review, dict) for review in reviews):
        raise ValueError("reviews must be an array of objects")
    actual_ids = [review.get("pair_id") for review in reviews]
    if (len(reviews) != len(pair_ids) or not all(isinstance(key, str) for key in actual_ids)
            or set(actual_ids) != set(pair_ids)):
        raise ValueError("judge omitted or duplicated a pair")
    for review in reviews:
        if set(review) != {"pair_id", "X", "Y", "editorial_preference", "editorial_reason",
                           "practical_preference", "practical_reason"}:
            raise ValueError("invalid pair review fields")
        for label in ("X", "Y"):
            verdict = review[label]
            if not isinstance(verdict, dict) or set(verdict) != {"status", "findings"}:
                raise ValueError("invalid text review fields")
            if verdict["status"] not in ("pass", "revision", "critical"):
                raise ValueError("invalid judge status")
            if not isinstance(verdict["findings"], list):
                raise ValueError("findings must be an array")
            for finding in verdict["findings"]:
                if not isinstance(finding, dict) or set(finding) != {
                        "severity", "category", "quote", "source_id", "reason", "minimal_fix"}:
                    raise ValueError("invalid finding fields")
                if (finding["severity"] not in ("revision", "critical")
                        or finding["category"] not in ("facts", "permissions", "brief", "style")):
                    raise ValueError("invalid finding severity or category")
                for key in ("quote", "reason", "minimal_fix"):
                    if not isinstance(finding[key], str) or not finding[key].strip():
                        raise ValueError("finding text must be a nonempty string")
                source = finding["source_id"]
                if source is not None and (not isinstance(source, str) or not source.strip()):
                    raise ValueError("source_id must be a string or null")
        for key in ("editorial_preference", "practical_preference"):
            if review[key] not in ("X", "Y", "tie"):
                raise ValueError("invalid judge preference")
        for key in ("editorial_reason", "practical_reason"):
            if not isinstance(review[key], str) or not review[key].strip():
                raise ValueError("preference reason must be a nonempty string")
    return data


def load_manifest(dest):
    body = (dest / "manifest.json").read_bytes()
    if ab.digest(body) != (dest / "manifest.sha256").read_text(encoding="ascii").strip():
        raise ValueError("frozen judge manifest changed")
    manifest = json.loads(body.decode("utf-8"))
    if (manifest.get("schema_version") != 1 or manifest.get("timeout_seconds") != TIMEOUT
            or manifest.get("cli_args") != ab.CLI_ARGS or len(manifest.get("jobs", [])) > 4):
        raise ValueError("unsupported judge manifest")
    for name, key in (("packet.json", "packet_sha256"), ("rubric.txt", "rubric_sha256")):
        if ab.digest((dest / name).read_bytes()) != manifest[key]:
            raise ValueError("frozen judge input changed: " + name)
    for job in manifest["jobs"]:
        if job["job_id"] not in {"judge1-batch1", "judge1-batch2", "judge2-batch1", "judge2-batch2"}:
            raise ValueError("invalid judge job ID")
        prompt = (dest / (job["job_id"] + ".prompt.txt")).read_bytes()
        if ab.digest(prompt) != job["prompt_sha256"]:
            raise ValueError("frozen judge prompt changed")
    return manifest


def read_record(dest, job):
    record = ab.read_json(dest / (job["job_id"] + ".record.json"))
    if any(record.get(key) != value for key, value in job.items()):
        raise ValueError("judge record does not match frozen job")
    if (record.get("status") not in {"completed", "failed"}
            or not isinstance(record.get("flags"), list)
            or (record["status"] == "completed") != (not record["flags"])):
        raise ValueError("invalid terminal judge record")
    prompt = record.get("prompt")
    if not isinstance(prompt, str) or ab.digest(prompt.encode("utf-8")) != job["prompt_sha256"]:
        raise ValueError("judge record prompt hash mismatch")
    final = record.get("final_text")
    if record.get("final_sha256") != (ab.digest(final.encode("utf-8")) if isinstance(final, str) else None):
        raise ValueError("judge record final hash mismatch")
    for suffix, key in ((".raw.jsonl", "raw_stdout_sha256"), (".stderr.txt", "raw_stderr_sha256")):
        if ab.digest((dest / (job["job_id"] + suffix)).read_bytes()) != record.get(key):
            raise ValueError("judge raw artifact hash mismatch")
    if record["status"] == "completed" and parse_review(final, job["pair_ids"]) != record.get("review"):
        raise ValueError("parsed judge review does not match exact final")
    return record


def run(dest, execute=False):
    if not execute:
        raise ValueError("review model calls require --execute")
    dest = Path(dest).resolve()
    manifest = load_manifest(dest)
    pending = []
    for job in manifest["jobs"]:
        if (dest / (job["job_id"] + ".record.json")).exists():
            read_record(dest, job)
        elif (dest / (job["job_id"] + ".attempt.json")).exists():
            raise ValueError("unfinished judge attempt exists; refusing a possible duplicate model call")
        else:
            pending.append(job)
    executable = shutil.which("codex") if pending else None
    if pending and not executable:
        raise ValueError("codex not found")
    for job in pending:
        result_path = dest / (job["job_id"] + ".record.json")
        prompt = (dest / (job["job_id"] + ".prompt.txt")).read_bytes()
        if ab.digest(prompt) != job["prompt_sha256"]:
            raise ValueError("frozen judge prompt changed")
        args = list(ab.CLI_ARGS)
        args[args.index("--model") + 1] = job["requested_model"]
        raw = stderr = b""
        returncode = None
        started_at, started = ab.utc_now(), time.monotonic()
        ab.save(dest / (job["job_id"] + ".attempt.json"), ab.json_bytes({
            "job_id": job["job_id"], "started_at_utc": started_at,
            "prompt_sha256": job["prompt_sha256"]}))
        failure = None
        try:
            with tempfile.TemporaryDirectory(prefix="journalist-review-") as workdir:
                result = subprocess.run([executable] + args, input=prompt, cwd=workdir,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        timeout=TIMEOUT, check=False)
                raw, stderr, returncode = result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired as exc:
            raw, stderr = exc.stdout or b"", exc.stderr or b""
            failure = "timeout"
        except OSError:
            failure = "process_start_error"
        parsed = ab.parse_events(raw)
        if failure or returncode != 0:
            parsed["flags"].append(failure or "nonzero_exit")
        review = None
        if not parsed["flags"]:
            try:
                review = parse_review(parsed["final_text"], job["pair_ids"])
            except (ValueError, KeyError, TypeError):
                parsed["flags"].append("invalid_review_json")
        record = {**job, **parsed, "review": review,
                  "prompt": prompt.decode("utf-8"),
                  "final_sha256": ab.digest(parsed["final_text"].encode("utf-8"))
                  if parsed["final_text"] is not None else None,
                  "status": "failed" if parsed["flags"] else "completed",
                  "started_at_utc": started_at, "ended_at_utc": ab.utc_now(),
                  "wall_seconds": round(time.monotonic() - started, 6),
                  "requested_reasoning_effort": "medium", "returncode": returncode,
                  "raw_stdout_sha256": ab.digest(raw), "raw_stderr_sha256": ab.digest(stderr)}
        ab.save(dest / (job["job_id"] + ".raw.jsonl"), raw)
        ab.save(dest / (job["job_id"] + ".stderr.txt"), stderr)
        ab.save(result_path, ab.json_bytes(record))
        print(json.dumps({"job_id": job["job_id"], "status": record["status"],
                          "flags": parsed["flags"]}), flush=True)
    records = [read_record(dest, job) for job in manifest["jobs"]]
    return {"completed": sum(record["status"] == "completed" for record in records),
            "failed": sum(record["status"] == "failed" for record in records),
            "executed_now": len(pending), "skipped": len(records) - len(pending)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--packet", type=Path, required=True)
    prep.add_argument("--rubric", type=Path, required=True)
    prep.add_argument("--dest", type=Path, required=True)
    execute = commands.add_parser("run")
    execute.add_argument("--dest", type=Path, required=True)
    execute.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            manifest = prepare(args.packet, args.rubric, args.dest)
            summary = {"status": "prepared", "job_count": len(manifest["jobs"])}
        else:
            summary = run(args.dest, args.execute)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(summary))
    return 1 if summary.get("failed", 0) else 0


if __name__ == "__main__":
    sys.exit(main())
