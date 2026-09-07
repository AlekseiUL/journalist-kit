#!/usr/bin/env python3
"""Opt-in three-arm revision pilot. Preparation, blinding and tests are offline."""

import argparse
import copy
from itertools import permutations
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import time

import run_ab as ab
import review_ab as pair_judges
import summarize_ab as reporting


ARMS = ("baseline", "previous", "candidate")
LABELS = ("X", "Y", "Z")
SEED = 20260908
JUDGES = ("gpt-5.6-sol", "gpt-6-astra")
JUDGE_TIMEOUT = 300
PREVIOUS = ab.ROOT / "evals/ab-2026-09-07/results/snapshots.json"


def _seal(dest, manifest):
    body = ab.json_bytes(manifest)
    ab.save(dest / "manifest.json", body)
    ab.save(dest / "manifest.sha256", (ab.digest(body) + "\n").encode("ascii"))
    return manifest


def previous_documents(path):
    body = Path(path).read_bytes()
    snapshot = json.loads(body.decode("utf-8"))
    documents = {}
    for name in ab.SKILL_FILES:
        entry = snapshot["files"]["snapshots/skill/" + name]
        text = entry["text"]
        if not isinstance(text, str) or ab.digest(text.encode("utf-8")) != entry["sha256"]:
            raise ValueError("previous document hash mismatch: " + name)
        documents[name] = text
    return documents, ab.digest(body)


def prepare(dest, dataset, protocol, rubric, previous_snapshots=PREVIOUS, root=ab.ROOT):
    """Freeze the candidate only when explicitly called; never access live profiles."""
    dest, root = Path(dest), Path(root)
    dataset_bytes = Path(dataset).read_bytes()
    cases = ab.validate_cases(json.loads(dataset_bytes.decode("utf-8")))
    previous, previous_hash = previous_documents(previous_snapshots)
    candidate = {name: (root / "skills/source-and-voice" / name).read_bytes().decode("utf-8")
                 for name in ab.SKILL_FILES}
    documents = {"previous": previous, "candidate": candidate}
    supporting = {"cases.json": dataset_bytes, "protocol.txt": Path(protocol).read_bytes(),
                  "judge.txt": Path(rubric).read_bytes()}
    dest.mkdir(parents=True, exist_ok=False, mode=0o700)
    hashes = {}

    def freeze(relative, body):
        ab.save(dest / relative, body)
        hashes[relative] = ab.digest(body)

    for name, body in supporting.items():
        freeze("snapshots/" + name, body)
    for arm, docs in documents.items():
        for name, text in docs.items():
            freeze("snapshots/" + arm + "/" + name, text.encode("utf-8"))
    rng = random.Random(SEED)
    assignments = list(permutations(ARMS)) * 2
    rng.shuffle(assignments)
    jobs, trios = [], []
    for case in cases:
        for repeat in (1, 2):
            index = len(trios)
            ids = {arm: "o_" + format(rng.getrandbits(128), "032x") for arm in ARMS}
            trio = {"trio_id": "t{:02d}".format(index + 1), "case_id": case["id"],
                    "repeat": repeat}
            for label, arm in zip(LABELS, assignments[index]):
                trio[label] = {"output_id": ids[arm], "arm": arm}
            trios.append(trio)
            order = ARMS[index % 3:] + ARMS[:index % 3]
            for arm in order:
                relative = "prompts/" + ids[arm] + ".txt"
                prompt = ab.prompt_for(case, documents.get(arm)).encode("utf-8")
                freeze(relative, prompt)
                jobs.append({"output_id": ids[arm], "case_id": case["id"], "repeat": repeat,
                             "arm": arm, "requested_model": ab.MODEL, "prompt_path": relative,
                             "prompt_sha256": hashes[relative]})
    freeze("mapping.json", ab.json_bytes({"seed": SEED, "trios": trios}))
    return _seal(dest, {"schema_version": 2, "kind": "revision-generation",
                       "prepared_at_utc": ab.utc_now(), "seed": SEED, "case_count": 6,
                       "repeats": 2, "arms": list(ARMS), "output_count": 36,
                       "requested_model": ab.MODEL, "requested_reasoning_effort": "medium",
                       "timeout_seconds": ab.TIMEOUT, "execution": "sequential-no-retries",
                       "injection_mode": "full-injection-not-native-loading",
                       "injected_documents": list(ab.SKILL_FILES),
                       "previous_export_sha256": previous_hash, "cli_args": ab.CLI_ARGS,
                       "limitations": ["host system prompt is not fully captured",
                                       "backend model snapshot is unknown unless explicitly emitted",
                                       "full injection does not test native skill loading",
                                       "provider seed, temperature and monetary cost are unknown",
                                       "unfinished process attempts are never automatically repeated"],
                       "publication": "local-only; no automatic publication",
                       "hashes": hashes, "jobs": jobs})


def load_manifest(dest, kind):
    dest = Path(dest)
    body = (dest / "manifest.json").read_bytes()
    if ab.digest(body) != (dest / "manifest.sha256").read_text(encoding="ascii").strip():
        raise ValueError("frozen manifest changed")
    manifest = json.loads(body.decode("utf-8"))
    expected_timeout = ab.TIMEOUT if kind in ("revision-generation", "editing-regression") else JUDGE_TIMEOUT
    jobs = manifest.get("jobs", [])
    if (manifest.get("schema_version") != 2 or manifest.get("kind") != kind
            or manifest.get("cli_args") != ab.CLI_ARGS
            or manifest.get("timeout_seconds") != expected_timeout):
        raise ValueError("unsupported or changed run manifest")
    if kind == "revision-generation":
        if manifest.get("output_count") != 36 or len(jobs) != 36:
            raise ValueError("revision run must contain 36 attempts")
        logical = {(job["case_id"], job["repeat"], job["arm"]) for job in jobs}
        cases = {job["case_id"] for job in jobs}
        expected = {(case, repeat, arm) for case in cases for repeat in (1, 2) for arm in ARMS}
        if len(cases) != 6 or logical != expected:
            raise ValueError("invalid revision job coverage")
    elif kind == "editing-regression":
        if (manifest.get("output_count") != 12 or len(jobs) != 12
                or manifest.get("case_count") != 12
                or any(job["requested_model"] != ab.MODEL for job in jobs)):
            raise ValueError("editing regression requires exactly 12 editor attempts")
    elif len(jobs) > 4 or any(job["requested_model"] not in JUDGES for job in jobs):
        raise ValueError("invalid reviewer jobs")
    ids = [job["output_id"] for job in jobs]
    if len(set(ids)) != len(ids) or any(not key or Path(key).name != key for key in ids):
        raise ValueError("invalid or duplicate output IDs")
    for relative, expected in manifest["hashes"].items():
        path = dest / relative
        if not path.resolve().is_relative_to(dest.resolve()):
            raise ValueError("snapshot path escapes run directory")
        if ab.digest(path.read_bytes()) != expected:
            raise ValueError("frozen input changed: " + relative)
    for job in jobs:
        if manifest["hashes"].get(job["prompt_path"]) != job["prompt_sha256"]:
            raise ValueError("job does not identify a frozen prompt")
    return manifest


def read_record(dest, job, reviewer=False):
    dest = Path(dest)
    record = ab.read_record(dest, job)
    if any(record.get(key) != value for key, value in job.items()):
        raise ValueError("record differs from frozen job")
    if (not isinstance(record.get("flags"), list)
            or (record["status"] == "completed") != (not record["flags"])):
        raise ValueError("invalid terminal record")
    for suffix, key in ((".jsonl", "raw_stdout_sha256"), (".stderr.txt", "raw_stderr_sha256")):
        if ab.digest((dest / "raw" / (job["output_id"] + suffix)).read_bytes()) != record.get(key):
            raise ValueError("raw artifact hash mismatch")
    parsed = ab.parse_events((dest / "raw" / (job["output_id"] + ".jsonl")).read_bytes())
    if (any(record.get(key) != parsed[key] for key in ("final_text", "agent_messages", "usage", "resolved_model"))
            or not set(parsed["flags"]).issubset(record["flags"])):
        raise ValueError("record differs from actual CLI events")
    if reviewer and record["status"] == "completed":
        if parse_review(record["final_text"], job["trio_ids"]) != record.get("review"):
            raise ValueError("review differs from exact final")
    return record


def _run_jobs(dest, manifest, reviewer=False):
    pending = []
    for job in manifest["jobs"]:
        if (dest / "records" / (job["output_id"] + ".json")).exists():
            read_record(dest, job, reviewer)
        elif (dest / "attempts" / (job["output_id"] + ".json")).exists():
            raise ValueError("unfinished attempt exists; refusing a possible duplicate model call")
        else:
            pending.append(job)
    executable = shutil.which("codex") if pending else None
    if pending and not executable:
        raise ValueError("codex executable not found")
    for job in pending:
        prompt = (dest / job["prompt_path"]).read_bytes()
        if ab.digest(prompt) != job["prompt_sha256"]:
            raise ValueError("frozen prompt changed")
        started_at, started = ab.utc_now(), time.monotonic()
        ab.save(dest / "attempts" / (job["output_id"] + ".json"), ab.json_bytes({
            "output_id": job["output_id"], "started_at_utc": started_at,
            "prompt_sha256": job["prompt_sha256"]}))
        args = list(ab.CLI_ARGS)
        args[args.index("--model") + 1] = job["requested_model"]
        raw = stderr = b""
        returncode = failure = None
        try:
            with tempfile.TemporaryDirectory(prefix="source-voice-revision-") as workdir:
                result = subprocess.run([executable] + args, input=prompt, cwd=workdir,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        timeout=manifest["timeout_seconds"], check=False)
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
        if reviewer and not parsed["flags"]:
            try:
                review = parse_review(parsed["final_text"], job["trio_ids"])
            except (ValueError, KeyError, TypeError):
                parsed["flags"].append("invalid_review_json")
        parsed["flags"] = sorted(set(parsed["flags"]))
        final = parsed["final_text"]
        record = {"schema_version": 2, **job, **parsed,
                  "status": "failed" if parsed["flags"] else "completed",
                  "started_at_utc": started_at, "ended_at_utc": ab.utc_now(),
                  "wall_seconds": round(time.monotonic() - started, 6),
                  "returncode": returncode, "requested_reasoning_effort": "medium",
                  "temperature": None, "provider_seed": None, "cli_version": None,
                  "backend_model_snapshot": None, "host_system_prompt": None,
                  "monetary_cost": None, "prompt": prompt.decode("utf-8"),
                  "final_sha256": ab.digest(final.encode("utf-8")) if final is not None else None,
                  "word_count": len(final.split()) if final is not None else None,
                  "raw_stdout_sha256": ab.digest(raw), "raw_stderr_sha256": ab.digest(stderr),
                  "final_selection": "last_completed_agent_message"}
        if reviewer:
            record["review"] = review
        else:
            record["dataset_sha256"] = manifest["hashes"]["snapshots/cases.json"]
        ab.save(dest / "raw" / (job["output_id"] + ".jsonl"), raw)
        ab.save(dest / "raw" / (job["output_id"] + ".stderr.txt"), stderr)
        ab.save(dest / "records" / (job["output_id"] + ".json"), ab.json_bytes(record))
        print(json.dumps({"output_id": job["output_id"], "status": record["status"],
                          "flags": record["flags"]}), flush=True)
    records = [read_record(dest, job, reviewer) for job in manifest["jobs"]]
    return {"completed": sum(r["status"] == "completed" for r in records),
            "failed": sum(r["status"] == "failed" for r in records),
            "executed_now": len(pending), "skipped": len(records) - len(pending)}


def run(run_dir, execute=False):
    if not execute:
        raise ValueError("model calls require explicit --execute")
    dest = Path(run_dir).resolve()
    return _run_jobs(dest, load_manifest(dest, "revision-generation"))


def blind(run_dir, dest):
    run_dir, dest = Path(run_dir), Path(dest)
    manifest = load_manifest(run_dir, "revision-generation")
    cases = {case["id"]: case for case in ab.validate_cases(ab.read_json(run_dir / "snapshots/cases.json"))}
    records = {job["output_id"]: read_record(run_dir, job) for job in manifest["jobs"]}
    trios, not_comparable = [], []
    for mapping in ab.read_json(run_dir / "mapping.json")["trios"]:
        case = cases[mapping["case_id"]]
        selected = {label: records[mapping[label]["output_id"]] for label in LABELS}
        if any(record["status"] != "completed" for record in selected.values()):
            reasons = sorted({flag for record in selected.values() for flag in record["flags"]})
            not_comparable.append({"trio_id": mapping["trio_id"], "case_id": case["id"],
                                   "repeat": mapping["repeat"], "reasons": reasons or ["failed_attempt"]})
            continue
        trio = {"trio_id": mapping["trio_id"], "case_id": case["id"], "repeat": mapping["repeat"],
                "request": case["request"], "sources": case["sources"],
                "reviewer_notes": case["reviewer_notes"]}
        for label, record in selected.items():
            trio[label] = {"text": record["final_text"], "word_count": record["word_count"],
                           "min_words": case["min_words"], "max_words": case["max_words"]}
        trios.append(trio)
    packet = {"schema_version": 2, "trios": trios, "not_comparable": not_comparable}
    dest.mkdir(parents=True, exist_ok=False, mode=0o700)
    ab.save(dest / "packet.json", ab.json_bytes(packet))
    return packet


def parse_review(text, trio_ids):
    data = json.loads(text)
    if (not isinstance(data, dict) or set(data) != {"reviews", "limits"}
            or not isinstance(data["limits"], str) or not data["limits"].strip()):
        raise ValueError("review must contain reviews and nonempty limits")
    reviews = data["reviews"]
    if not isinstance(reviews, list) or not all(isinstance(r, dict) for r in reviews):
        raise ValueError("reviews must be an array of objects")
    ids = [review.get("trio_id") for review in reviews]
    if len(ids) != len(trio_ids) or not all(isinstance(key, str) for key in ids) or set(ids) != set(trio_ids):
        raise ValueError("judge omitted or duplicated a trio")
    for review in reviews:
        if set(review) != {"trio_id", "X", "Y", "Z", "editorial_ranking", "editorial_reason",
                           "practical_ranking", "practical_reason"}:
            raise ValueError("invalid trio review fields")
        for label in LABELS:
            verdict = review[label]
            if (not isinstance(verdict, dict) or set(verdict) != {"status", "findings"}
                    or verdict["status"] not in ("pass", "revision", "critical")
                    or not isinstance(verdict["findings"], list)):
                raise ValueError("invalid text review")
            for finding in verdict["findings"]:
                if not isinstance(finding, dict) or set(finding) != {
                        "severity", "category", "quote", "source_id", "reason", "minimal_fix"}:
                    raise ValueError("invalid finding fields")
                if (finding["severity"] not in ("revision", "critical")
                        or finding["category"] not in ("facts", "permissions", "brief", "style")):
                    raise ValueError("invalid finding severity or category")
                if any(not isinstance(finding[key], str) or not finding[key].strip()
                       for key in ("quote", "reason", "minimal_fix")):
                    raise ValueError("finding text must be nonempty")
                source = finding["source_id"]
                if source is not None and (not isinstance(source, str) or not source.strip()):
                    raise ValueError("source_id must be nonempty string or null")
        for dimension in ("editorial", "practical"):
            ranking = review[dimension + "_ranking"]
            if (not isinstance(ranking, list) or not ranking
                    or not all(isinstance(tier, list) and tier for tier in ranking)):
                raise ValueError("ranking must contain nonempty tied tiers")
            flat = [label for tier in ranking for label in tier]
            if len(flat) != 3 or any(not isinstance(label, str) for label in flat) or set(flat) != set(LABELS):
                raise ValueError("ranking must contain each label exactly once")
            reason = review[dimension + "_reason"]
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError("ranking reason must be nonempty")
    return data


def judge_prepare(packet_path, rubric_path, dest):
    packet_bytes, rubric_bytes = Path(packet_path).read_bytes(), Path(rubric_path).read_bytes()
    packet = json.loads(packet_bytes.decode("utf-8"))
    trios = packet["trios"]
    if not isinstance(trios, list) or len(trios) > 12:
        raise ValueError("pilot requires at most 12 trios")
    ids = [trio.get("trio_id") for trio in trios if isinstance(trio, dict)]
    if (len(ids) != len(trios) or any(not isinstance(key, str) or not key for key in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError("packet needs unique nonempty trio IDs")
    for trio in trios:
        if any(not isinstance(trio.get(label), dict) or not isinstance(trio[label].get("text"), str)
               for label in LABELS):
            raise ValueError("each trio requires X/Y/Z text")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=False, mode=0o700)
    hashes = {}

    def freeze(relative, body):
        ab.save(dest / relative, body)
        hashes[relative] = ab.digest(body)

    freeze("packet.json", packet_bytes)
    freeze("rubric.txt", rubric_bytes)
    jobs = []
    for index, model in enumerate(JUDGES):
        selected = copy.deepcopy(trios if index == 0 else list(reversed(trios)))
        label_map = dict(zip(LABELS, LABELS if index == 0 else ("Y", "Z", "X")))
        for trio in selected:
            original = {label: trio[label] for label in LABELS}
            for label, canonical in label_map.items():
                trio[label] = original[canonical]
        for start in range(0, len(selected), 6):
            batch = selected[start:start + 6]
            output_id = "judge{}-batch{}".format(index + 1, start // 6 + 1)
            path = "prompts/" + output_id + ".txt"
            prompt = (rubric_bytes.decode("utf-8") + "\n\nТройки для оценки:\n" +
                      json.dumps({"trios": batch}, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            freeze(path, prompt)
            jobs.append({"output_id": output_id, "requested_model": model,
                         "presented_to_canonical": label_map,
                         "trio_ids": [trio["trio_id"] for trio in batch],
                         "prompt_path": path, "prompt_sha256": hashes[path]})
    return _seal(dest, {"schema_version": 2, "kind": "revision-review",
                       "prepared_at_utc": ab.utc_now(), "timeout_seconds": JUDGE_TIMEOUT,
                       "execution": "sequential-no-retries", "cli_args": ab.CLI_ARGS,
                       "hashes": hashes, "jobs": jobs})


def judge_run(dest, execute=False):
    if not execute:
        raise ValueError("review model calls require explicit --execute")
    dest = Path(dest).resolve()
    return _run_jobs(dest, load_manifest(dest, "revision-review"), reviewer=True)


def normalize_review(review, mapping, presented_to_canonical):
    labels = {label: mapping[presented_to_canonical[label]]["arm"] for label in LABELS}
    normalized = {"statuses": {labels[label]: review[label]["status"] for label in LABELS},
                  "findings": {labels[label]: review[label]["findings"] for label in LABELS}}
    for dimension in ("editorial", "practical"):
        ranking = [[labels[label] for label in tier] for tier in review[dimension + "_ranking"]]
        normalized[dimension + "_ranking"] = ranking
        normalized[dimension + "_reason"] = review[dimension + "_reason"]
        positions = {arm: index for index, tier in enumerate(ranking) for arm in tier}
        normalized[dimension + "_pairwise"] = {
            left + "_vs_" + right: ("tie" if positions[left] == positions[right]
                                    else left if positions[left] < positions[right] else right)
            for index, left in enumerate(ARMS) for right in ARMS[index + 1:]}
    return normalized


def export(run_dir, judges_dir, dest):
    """Export exact finals and public metadata; retain failures and partial ties."""
    run_dir, judges_dir, dest = Path(run_dir), Path(judges_dir), Path(dest)
    if dest.exists():
        raise FileExistsError("export destination must be new")
    manifest = load_manifest(run_dir, "revision-generation")
    cases = {case["id"]: case for case in ab.validate_cases(ab.read_json(run_dir / "snapshots/cases.json"))}
    mapping = ab.read_json(run_dir / "mapping.json")["trios"]
    jobs = {job["output_id"]: job for job in manifest["jobs"]}
    if len(mapping) != 12 or len({trio["trio_id"] for trio in mapping}) != 12:
        raise ValueError("expected twelve unique trios")
    used, identities = set(), set()
    for trio in mapping:
        if (not reporting.SAFE_ID.fullmatch(trio["trio_id"])
                or not reporting.SAFE_ID.fullmatch(trio["case_id"])
                or trio["case_id"] not in cases or trio["repeat"] not in (1, 2)
                or {trio[label]["arm"] for label in LABELS} != set(ARMS)):
            raise ValueError("invalid trio mapping")
        identities.add((trio["case_id"], trio["repeat"]))
        for label in LABELS:
            entry = trio[label]
            job = jobs[entry["output_id"]]
            if (entry["output_id"] in used or job["case_id"] != trio["case_id"]
                    or job["repeat"] != trio["repeat"] or job["arm"] != entry["arm"]):
                raise ValueError("mapping does not match generation jobs")
            used.add(entry["output_id"])
    if used != set(jobs) or identities != {(key, repeat) for key in cases for repeat in (1, 2)}:
        raise ValueError("mapping omits generation outputs")
    if {path.name for path in (run_dir / "records").glob("*.json")} != {key + ".json" for key in jobs}:
        raise ValueError("exactly 36 terminal writer records are required")
    records = {key: read_record(run_dir, job) for key, job in jobs.items()}
    frozen = {relative: {"sha256": expected, "text": (run_dir / relative).read_bytes().decode("utf-8")}
              for relative, expected in manifest["hashes"].items() if relative.startswith("snapshots/")}
    documents = {arm: {name: frozen["snapshots/" + arm + "/" + name]["text"]
                       for name in ab.SKILL_FILES} for arm in ("previous", "candidate")}
    writers, outputs = [], {}
    for key, job in jobs.items():
        record, case = records[key], cases[job["case_id"]]
        if record["prompt"] != ab.prompt_for(case, documents.get(job["arm"])):
            raise ValueError("writer prompt cannot be reconstructed from frozen inputs")
        final = record["final_text"]
        count = len(final.split()) if final is not None else None
        if record["word_count"] != count:
            raise ValueError("word count differs from exact final")
        path = "outputs/{}-r{}-{}.txt".format(job["case_id"], job["repeat"], job["arm"])
        if final is not None:
            outputs[path] = final.encode("utf-8")
        writers.append({**reporting.metadata(record), "output_id": key, "case_id": case["id"],
                        "repeat": job["repeat"], "arm": job["arm"],
                        "text_path": path if final is not None else None, "word_count": count,
                        "min_words": case["min_words"], "max_words": case["max_words"],
                        "within_word_range": case["min_words"] <= count <= case["max_words"]
                        if count is not None else None})
    judge_manifest = load_manifest(judges_dir, "revision-review")
    packet = ab.read_json(judges_dir / "packet.json")
    if (judges_dir / "rubric.txt").read_bytes() != (run_dir / "snapshots/judge.txt").read_bytes():
        raise ValueError("reviewer rubric differs from pre-generation frozen rubric")
    comparable = [trio for trio in mapping if all(records[trio[label]["output_id"]]["status"] == "completed"
                                                for label in LABELS)]
    comparable_ids = [trio["trio_id"] for trio in comparable]
    if (len(packet["trios"]) != len(comparable)
            or [trio["trio_id"] for trio in packet["trios"]] != comparable_ids
            or {trio["trio_id"] for trio in packet["not_comparable"]} !=
            {trio["trio_id"] for trio in mapping if trio["trio_id"] not in comparable_ids}):
        raise ValueError("judge packet does not cover writer trios")
    for shown, original in zip(packet["trios"], comparable):
        case = cases[original["case_id"]]
        if (any(shown[key] != original[key] for key in ("case_id", "repeat"))
                or any(shown[key] != case[key] for key in ("request", "sources", "reviewer_notes"))):
            raise ValueError("judge packet source materials changed")
        for label in LABELS:
            record = records[original[label]["output_id"]]
            if shown[label] != {"text": record["final_text"], "word_count": record["word_count"],
                                "min_words": case["min_words"], "max_words": case["max_words"]}:
                raise ValueError("judge packet does not contain exact writer text")
    expected_jobs = {"judge{}-batch{}".format(judge, batch)
                     for judge in (1, 2) for batch in range(1, (len(comparable) + 5) // 6 + 1)}
    if ({job["output_id"] for job in judge_manifest["jobs"]} != expected_jobs
            or {path.name for path in (judges_dir / "records").glob("*.json")} !=
            {key + ".json" for key in expected_jobs}):
        raise ValueError("terminal judge records must match all scheduled batches")
    by_judge = {"judge1": {}, "judge2": {}}
    reviews = []
    rubric = (judges_dir / "rubric.txt").read_bytes().decode("utf-8")
    for job in judge_manifest["jobs"]:
        judge_id, batch = job["output_id"].split("-batch")
        index, start = int(judge_id[-1]) - 1, (int(batch) - 1) * 6
        selected = copy.deepcopy(packet["trios"] if index == 0 else list(reversed(packet["trios"])))
        label_map = dict(zip(LABELS, LABELS if index == 0 else ("Y", "Z", "X")))
        for trio in selected:
            originals = {label: trio[label] for label in LABELS}
            for label, original in label_map.items():
                trio[label] = originals[original]
        batch_trios = selected[start:start + 6]
        expected_prompt = (rubric + "\n\nТройки для оценки:\n" +
                           json.dumps({"trios": batch_trios}, ensure_ascii=False, indent=2) + "\n")
        record = read_record(judges_dir, job, reviewer=True)
        if (job["requested_model"] != JUDGES[index] or job["presented_to_canonical"] != label_map
                or job["trio_ids"] != [trio["trio_id"] for trio in batch_trios]
                or record["prompt"] != expected_prompt):
            raise ValueError("review assignment or prompt differs from frozen design")
        reviews.append({**reporting.metadata(record), "output_id": job["output_id"],
                        "judge_id": judge_id, "trio_ids": job["trio_ids"],
                        "presented_to_canonical": label_map,
                        "review": record.get("review"), "final_text": record.get("final_text")})
        if record["status"] == "completed":
            by_judge[judge_id].update({review["trio_id"]: (review, label_map)
                                      for review in record["review"]["reviews"]})
    comparisons = [left + "_vs_" + right for index, left in enumerate(ARMS) for right in ARMS[index + 1:]]
    trios = []
    for trio in mapping:
        is_comparable = trio["trio_id"] in comparable_ids
        opinions = {}
        for judge_id, available in by_judge.items():
            found = available.get(trio["trio_id"])
            opinions[judge_id] = normalize_review(found[0], trio, found[1]) if found and is_comparable else None
        consensus = {}
        for dimension in ("editorial", "practical"):
            consensus[dimension] = {}
            for comparison in comparisons:
                votes = [opinion[dimension + "_pairwise"][comparison] if opinion else None
                         for opinion in opinions.values()]
                value = ("non_comparable" if not is_comparable else "missing_review" if None in votes
                         else votes[0] if votes[0] == votes[1] else "disagreement")
                consensus[dimension][comparison] = value
        trios.append({"trio_id": trio["trio_id"], "case_id": trio["case_id"], "repeat": trio["repeat"],
                      "comparable": is_comparable, "judges": opinions, "consensus_pairwise": consensus})
    stats = {"denominators": {"writer_attempts": 36, "trios": 12, "cases": 6,
                              "repeats_per_case": 2, "judges": 2}, "writers": {}, "judges": {},
             "consensus_pairwise": {}}
    for arm in ARMS:
        selected = [record for record in writers if record["arm"] == arm]
        stats["writers"][arm] = {"attempts": len(selected),
                                 "statuses": reporting.counts((r["status"] for r in selected), ("completed", "failed")),
                                 "length_range": {"within": sum(r["within_word_range"] is True for r in selected),
                                                  "outside": sum(r["within_word_range"] is False for r in selected),
                                                  "missing_text": sum(r["within_word_range"] is None for r in selected)},
                                 "measurements": reporting.measured(selected)}
    for dimension in ("editorial", "practical"):
        stats["consensus_pairwise"][dimension] = {
            comparison: reporting.counts((trio["consensus_pairwise"][dimension][comparison] for trio in trios),
                                          (*comparison.split("_vs_"), "tie", "disagreement", "missing_review", "non_comparable"))
            for comparison in comparisons}
    for judge_id in by_judge:
        selected = [record for record in reviews if record["judge_id"] == judge_id]
        stats["judges"][judge_id] = {
            "record_statuses": reporting.counts((r["status"] for r in selected), ("completed", "failed")),
            "measurements": reporting.measured(selected), "trio_denominator": 12,
            "statuses_by_arm": {arm: reporting.counts(
                (trio["judges"][judge_id]["statuses"][arm] if trio["judges"][judge_id] else
                 "missing_review" if trio["comparable"] else "non_comparable" for trio in trios),
                reporting.STATUSES) for arm in ARMS},
            "pairwise_preferences": {dimension: {comparison: reporting.counts(
                (trio["judges"][judge_id][dimension + "_pairwise"][comparison] if trio["judges"][judge_id] else
                 "missing_review" if trio["comparable"] else "non_comparable" for trio in trios),
                (*comparison.split("_vs_"), "tie", "missing_review", "non_comparable"))
                for comparison in comparisons} for dimension in ("editorial", "practical")}}
    results = {"schema_version": 2, "exported_at_utc": ab.utc_now(),
               "caveats": ["Six synthetic cases with two repeats are not twelve independent topics.",
                           "Model reviews and full document injection do not establish human preference or general accuracy.",
                           "Failed attempts and missing reviews remain in the denominator; missing reviews are not ties.",
                           "Unknown backend snapshots, generation parameters and costs remain unknown.",
                           "Cached input is included in input, not added to it; missing usage is not zero."],
               "writer_manifest_sha256": ab.digest((run_dir / "manifest.json").read_bytes()),
               "judge_manifest_sha256": ab.digest((judges_dir / "manifest.json").read_bytes()),
               "writer_records": writers, "judge_records": reviews, "mapping": mapping,
               "trios": trios, "statistics": stats}
    snapshots = {"schema_version": 2, "files": frozen, "common_wrapper": ab.COMMON,
                 "cli_args": manifest["cli_args"], "writer_timeout_seconds": ab.TIMEOUT,
                 "judge_timeout_seconds": JUDGE_TIMEOUT, "judge_models_in_order": list(JUDGES),
                 "injection_mode": manifest["injection_mode"],
                 "previous_export_sha256": manifest["previous_export_sha256"],
                 "writer_input_hashes": manifest["hashes"], "judge_input_hashes": judge_manifest["hashes"]}
    reporting.ensure_publishable(results)
    reporting.ensure_publishable(snapshots)
    for body in outputs.values():
        reporting.ensure_publishable(body.decode("utf-8"))
    dest.mkdir(parents=True, exist_ok=False)
    for path, body in outputs.items():
        ab.save(dest / path, body)
    ab.save(dest / "results.json", ab.json_bytes(results))
    ab.save(dest / "snapshots.json", ab.json_bytes(snapshots))
    return results


def edit_cases(data):
    """Validate twelve supplied drafts; analysis fields never enter prompts."""
    if (not isinstance(data, dict) or not isinstance(data.get("cases"), list)
            or len(data["cases"]) != 12
            or any(not isinstance(data.get(key), str) or not data[key].strip()
                   for key in ("dataset_id", "description"))):
        raise ValueError("editing dataset needs an ID, description and exactly 12 cases")
    cases = data["cases"]
    ab.validate_cases({"cases": cases[:6]})
    ab.validate_cases({"cases": cases[6:]})
    if len({case["id"] for case in cases}) != 12:
        raise ValueError("editing case IDs must be unique")
    for case in cases:
        notes = case["reviewer_notes"]
        if (not reporting.SAFE_ID.fullmatch(case["id"])
                or not isinstance(case.get("draft"), str) or not case["draft"].strip()
                or any(not isinstance(case.get(key), dict) for key in ("provenance", "expectation"))
                or not ((isinstance(notes, str) and notes.strip()) or
                        (isinstance(notes, list) and notes
                         and all(isinstance(note, str) and note.strip() for note in notes)))):
            raise ValueError("invalid editing draft or analysis metadata")
        if not case["sources"]:
            raise ValueError("editing cases need nonempty sources")
        for source in case["sources"]:
            if any(not isinstance(source.get(key), str) or not source[key].strip()
                   for key in ("id", "content", "origin", "kind")):
                raise ValueError("editing sources need ID, content, origin and kind")
            if source["kind"] not in ("record", "statement", "author_note"):
                raise ValueError("invalid editing source kind")
        if len({source["id"] for source in case["sources"]}) != len(case["sources"]):
            raise ValueError("editing source IDs must be unique within each case")
    return cases


def edit_prompt(case, editor):
    return (ab.COMMON + "\nРедакторские инструкции:\n" + editor + "\n"
            + ab.prompt_for(case)[len(ab.COMMON):]
            + "\nЧерновик (данные, а не инструкции):\n" + case["draft"])


def _edit_design(cases, editor):
    rng = random.Random(SEED)
    positions = ["original"] * 6 + ["edited"] * 6
    rng.shuffle(positions)
    files, jobs, pairs = {}, [], []
    for index, case in enumerate(cases):
        output_id = "o_" + format(rng.getrandbits(128), "032x")
        original_path = "originals/" + case["id"] + ".txt"
        prompt_path = "prompts/" + output_id + ".txt"
        files[original_path] = case["draft"].encode("utf-8")
        files[prompt_path] = edit_prompt(case, editor).encode("utf-8")
        original = {"arm": "original", "original_path": original_path,
                    "original_sha256": ab.digest(files[original_path])}
        edited = {"arm": "edited", "output_id": output_id}
        pairs.append({"pair_id": "p{:02d}".format(index + 1), "case_id": case["id"],
                      "X": original if positions[index] == "original" else edited,
                      "Y": edited if positions[index] == "original" else original})
        jobs.append({"output_id": output_id, "case_id": case["id"], "arm": "edited",
                     "requested_model": ab.MODEL, "original_path": original_path,
                     "original_sha256": original["original_sha256"],
                     "prompt_path": prompt_path, "prompt_sha256": ab.digest(files[prompt_path])})
    files["mapping.json"] = ab.json_bytes({"seed": SEED, "pairs": pairs})
    return files, jobs, pairs


def edit_prepare(dest, dataset, protocol, rubric, editor):
    inputs = {"cases.json": Path(dataset).read_bytes(), "protocol.txt": Path(protocol).read_bytes(),
              "judge.txt": Path(rubric).read_bytes(), "editor.txt": Path(editor).read_bytes()}
    if any(not body.decode("utf-8").strip() for body in inputs.values()):
        raise ValueError("editing inputs and instructions must be nonempty UTF-8 text")
    cases = edit_cases(json.loads(inputs["cases.json"].decode("utf-8")))
    files, jobs, _ = _edit_design(cases, inputs["editor.txt"].decode("utf-8"))
    files.update({"snapshots/" + name: body for name, body in inputs.items()})
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=False, mode=0o700)
    for relative, body in files.items():
        ab.save(dest / relative, body)
    return _seal(dest, {"schema_version": 2, "kind": "editing-regression", "seed": SEED,
                       "prepared_at_utc": ab.utc_now(), "case_count": 12, "output_count": 12,
                       "original_count": 12, "execution": "sequential-no-retries",
                       "requested_model": ab.MODEL, "requested_reasoning_effort": "medium",
                       "timeout_seconds": ab.TIMEOUT, "cli_args": ab.CLI_ARGS,
                       "injection_mode": "frozen-editor-only-not-native-skill-loading",
                       "injected_documents": ["editor.txt"],
                       "hashes": {path: ab.digest(body) for path, body in files.items()}, "jobs": jobs})


def _edit_inputs(run_dir):
    manifest = load_manifest(run_dir, "editing-regression")
    cases = edit_cases(ab.read_json(run_dir / "snapshots/cases.json"))
    editor = (run_dir / "snapshots/editor.txt").read_bytes().decode("utf-8")
    files, jobs, pairs = _edit_design(cases, editor)
    frozen_paths = set(files) | {"snapshots/" + name for name in ("cases.json", "protocol.txt", "judge.txt", "editor.txt")}
    if (manifest["jobs"] != jobs or manifest.get("seed") != SEED
            or set(manifest["hashes"]) != frozen_paths
            or any((run_dir / path).read_bytes() != body for path, body in files.items())):
        raise ValueError("editor prompts or original-to-final mapping differ from frozen design")
    return manifest, {case["id"]: case for case in cases}, pairs


def edit_run(run_dir, execute=False):
    if not execute:
        raise ValueError("model calls require explicit --execute")
    run_dir = Path(run_dir).resolve()
    manifest, _, _ = _edit_inputs(run_dir)
    return _run_jobs(run_dir, manifest)


def _edit_packet(cases, mapping, records):
    packet = {"schema_version": 1, "pairs": [], "not_comparable": []}
    by_case = {record["case_id"]: record for record in records.values()}
    for entry in mapping:
        case, record = cases[entry["case_id"]], by_case[entry["case_id"]]
        if record["status"] != "completed":
            packet["not_comparable"].append({"pair_id": entry["pair_id"], "case_id": case["id"],
                                              "reasons": record["flags"]})
            continue
        pair = {"pair_id": entry["pair_id"], "case_id": case["id"],
                **{key: case[key] for key in ("request", "sources", "reviewer_notes")}}
        for label in ("X", "Y"):
            text = case["draft"] if entry[label]["arm"] == "original" else record["final_text"]
            pair[label] = {"text": text, "word_count": len(text.split()),
                           "min_words": case["min_words"], "max_words": case["max_words"]}
        packet["pairs"].append(pair)
    return packet


def edit_blind(run_dir, dest):
    run_dir, dest = Path(run_dir), Path(dest)
    manifest, cases, mapping = _edit_inputs(run_dir)
    records = {job["output_id"]: read_record(run_dir, job) for job in manifest["jobs"]}
    packet = _edit_packet(cases, mapping, records)
    dest.mkdir(parents=True, exist_ok=False, mode=0o700)
    ab.save(dest / "packet.json", ab.json_bytes(packet))
    return packet


def _edit_reviews(judges_dir, run_dir, packet):
    manifest = pair_judges.load_manifest(judges_dir)
    if (ab.read_json(judges_dir / "packet.json") != packet
            or (judges_dir / "rubric.txt").read_bytes() != (run_dir / "snapshots/judge.txt").read_bytes()):
        raise ValueError("judge packet or rubric differs from frozen editing experiment")
    # Reuse the exact existing pair-review batching and X/Y reversal contract offline.
    with tempfile.TemporaryDirectory(prefix="source-voice-review-check-") as directory:
        expected = pair_judges.prepare(judges_dir / "packet.json", run_dir / "snapshots/judge.txt",
                                       Path(directory) / "expected")
    if manifest["jobs"] != expected["jobs"]:
        raise ValueError("judge prompts or assignments differ from fixed blinded order")
    expected_ids = {job["job_id"] for job in manifest["jobs"]}
    for suffix in (".record.json", ".attempt.json"):
        if {path.name for path in judges_dir.glob("*" + suffix)} != {key + suffix for key in expected_ids}:
            raise ValueError("terminal judge records and attempts must cover all scheduled batches")
    public, by_judge = [], {judge: {} for judge in reporting.JUDGE_IDS}
    for job in manifest["jobs"]:
        record = pair_judges.read_record(judges_dir, job)
        parsed = ab.parse_events((judges_dir / (job["job_id"] + ".raw.jsonl")).read_bytes())
        if (any(record.get(key) != parsed[key] for key in ("final_text", "agent_messages", "usage", "resolved_model"))
                or not set(parsed["flags"]).issubset(record["flags"])):
            raise ValueError("judge record differs from actual CLI events")
        attempt = ab.read_json(judges_dir / (job["job_id"] + ".attempt.json"))
        if attempt != {"job_id": job["job_id"], "started_at_utc": record["started_at_utc"],
                       "prompt_sha256": job["prompt_sha256"]}:
            raise ValueError("judge attempt differs from terminal record")
        judge_id = job["job_id"].split("-batch")[0]
        public.append({**reporting.metadata(record), **job, "judge_id": judge_id,
                       "attempt": attempt, "review": record.get("review"), "final_text": record["final_text"]})
        if record["status"] == "completed":
            by_judge[judge_id].update({review["pair_id"]: (review, job["swapped_xy"])
                                      for review in record["review"]["reviews"]})
    return manifest, public, by_judge


def edit_export(run_dir, judges_dir, dest):
    """Export evidence for analysis, without deciding whether the quality gate passed."""
    run_dir, judges_dir, dest = Path(run_dir), Path(judges_dir), Path(dest)
    if dest.exists():
        raise FileExistsError("export destination must be new")
    manifest, cases, mapping = _edit_inputs(run_dir)
    expected_ids = {job["output_id"] + ".json" for job in manifest["jobs"]}
    for directory in ("records", "attempts"):
        if {path.name for path in (run_dir / directory).glob("*.json")} != expected_ids:
            raise ValueError("exactly 12 terminal editor records and attempts are required")
    records = {job["output_id"]: read_record(run_dir, job) for job in manifest["jobs"]}
    originals, editors, outputs = [], [], {}
    for job in manifest["jobs"]:
        record, case = records[job["output_id"]], cases[job["case_id"]]
        attempt = ab.read_json(run_dir / "attempts" / (job["output_id"] + ".json"))
        if attempt != {"output_id": job["output_id"], "started_at_utc": record["started_at_utc"],
                       "prompt_sha256": job["prompt_sha256"]}:
            raise ValueError("editor attempt differs from terminal record")
        text = record["final_text"]
        count = len(text.split()) if text is not None else None
        if record["word_count"] != count:
            raise ValueError("editor word count differs from exact final")
        original_path, final_path = "outputs/" + case["id"] + "-original.txt", "outputs/" + case["id"] + "-edited.txt"
        outputs[original_path] = (run_dir / job["original_path"]).read_bytes()
        originals.append({"case_id": case["id"], "status": "supplied-draft", "model_attempt": False,
                          "text_path": original_path, "sha256": job["original_sha256"],
                          "word_count": len(case["draft"].split())})
        if text is not None:
            outputs[final_path] = text.encode("utf-8")
        editors.append({**reporting.metadata(record), "output_id": job["output_id"], "case_id": case["id"],
                        "attempt": attempt, "original_path": original_path, "original_sha256": job["original_sha256"],
                        "text_path": final_path if text is not None else None, "word_count": count,
                        "min_words": case["min_words"], "max_words": case["max_words"],
                        "within_word_range": case["min_words"] <= count <= case["max_words"] if count is not None else None})
    packet = _edit_packet(cases, mapping, records)
    judge_manifest, reviews, by_judge = _edit_reviews(judges_dir, run_dir, packet)
    comparable_ids = {pair["pair_id"] for pair in packet["pairs"]}
    pairs = []
    for pair in mapping:
        comparable = pair["pair_id"] in comparable_ids
        opinions = {}
        for judge_id, available in by_judge.items():
            found = available.get(pair["pair_id"])
            opinions[judge_id] = reporting.normalize_review(found[0], pair, found[1]) if found else None
        consensus = {}
        for dimension in ("editorial", "practical"):
            votes = [opinion[dimension + "_preference"] if opinion else None for opinion in opinions.values()]
            consensus[dimension] = ("non_comparable" if not comparable else "missing_review" if None in votes
                                    else votes[0] if votes[0] == votes[1] else "disagreement")
        pairs.append({"pair_id": pair["pair_id"], "case_id": pair["case_id"], "comparable": comparable,
                      "judges": opinions, "consensus": consensus})
    preferences = ("original", "edited", "tie", "disagreement", "missing_review", "non_comparable")
    stats = {"denominators": {"originals": 12, "editor_attempts": 12, "pairs": 12, "judges": 2},
             "editors": {"statuses": reporting.counts((r["status"] for r in editors), ("completed", "failed")),
                         "measurements": reporting.measured(editors)},
             "total_measurements": reporting.measured(editors + reviews),
             "consensus": {dimension: reporting.counts((p["consensus"][dimension] for p in pairs), preferences)
                           for dimension in ("editorial", "practical")}, "judges": {}}
    for judge_id in by_judge:
        stats["judges"][judge_id] = {
            "measurements": reporting.measured([r for r in reviews if r["judge_id"] == judge_id]),
            "statuses_by_arm": {arm: reporting.counts(
                (p["judges"][judge_id]["statuses"][arm] if p["judges"][judge_id] else
                 "missing_review" if p["comparable"] else "non_comparable" for p in pairs), reporting.STATUSES)
                for arm in ("original", "edited")}}
    results = {"schema_version": 2, "kind": "editing-regression", "seed": manifest["seed"],
               "exported_at_utc": ab.utc_now(),
               "quality_gate": "not-adjudicated", "caveats": [
                   "Twelve fixed regression drafts are not a population estimate or a human review.",
                   "Originals are supplied data, not model attempts. Failed edits have no fallback PASS.",
                   "All attempts remain in denominators. Model findings require source-based adjudication.",
                   "Unknown model snapshots and costs remain unknown; missing usage is not zero."],
               "editor_manifest_sha256": ab.digest((run_dir / "manifest.json").read_bytes()),
               "judge_manifest_sha256": ab.digest((judges_dir / "manifest.json").read_bytes()),
               "original_records": originals, "editor_records": editors, "judge_records": reviews,
               "mapping": mapping, "pairs": pairs, "not_comparable": packet["not_comparable"], "statistics": stats}
    snapshots = {"schema_version": 2, "common_wrapper": ab.COMMON, "cli_args": manifest["cli_args"],
                 "editor_manifest": manifest, "judge_manifest": judge_manifest,
                 "files": {path: {"sha256": expected, "text": (run_dir / path).read_bytes().decode("utf-8")}
                           for path, expected in manifest["hashes"].items() if path.startswith("snapshots/")}}
    for value in (results, snapshots, *[body.decode("utf-8") for body in outputs.values()]):
        reporting.ensure_publishable(value)
    dest.mkdir(parents=True, exist_ok=False)
    for path, body in outputs.items():
        ab.save(dest / path, body)
    ab.save(dest / "results.json", ab.json_bytes(results))
    ab.save(dest / "snapshots.json", ab.json_bytes(snapshots))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    for key in ("dest", "dataset", "protocol", "rubric"):
        prep.add_argument("--" + key, type=Path, required=True)
    prep.add_argument("--previous-snapshots", type=Path, default=PREVIOUS)
    execute = commands.add_parser("run")
    execute.add_argument("--run-dir", type=Path, required=True)
    execute.add_argument("--execute", action="store_true")
    blind_parser = commands.add_parser("blind")
    blind_parser.add_argument("--run-dir", type=Path, required=True)
    blind_parser.add_argument("--dest", type=Path, required=True)
    judge_prep = commands.add_parser("judge-prepare")
    for key in ("packet", "rubric", "dest"):
        judge_prep.add_argument("--" + key, type=Path, required=True)
    judge_exec = commands.add_parser("judge-run")
    judge_exec.add_argument("--dest", type=Path, required=True)
    judge_exec.add_argument("--execute", action="store_true")
    exporter = commands.add_parser("export")
    for key in ("run-dir", "judges-dir", "dest"):
        exporter.add_argument("--" + key, type=Path, required=True)
    for command, keys in (("edit-prepare", ("dest", "dataset", "protocol", "rubric", "editor")),
                          ("edit-run", ("run-dir",)), ("edit-blind", ("run-dir", "dest")),
                          ("edit-export", ("run-dir", "judges-dir", "dest"))):
        subparser = commands.add_parser(command)
        for key in keys:
            subparser.add_argument("--" + key, type=Path, required=True)
        if command == "edit-run":
            subparser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "edit-prepare":
            manifest = edit_prepare(args.dest, args.dataset, args.protocol, args.rubric, args.editor)
            result = {"status": "prepared", "job_count": len(manifest["jobs"])}
        elif args.command == "edit-run":
            result = edit_run(args.run_dir, args.execute)
        elif args.command == "edit-blind":
            packet = edit_blind(args.run_dir, args.dest)
            result = {"comparable": len(packet["pairs"]), "not_comparable": len(packet["not_comparable"])}
        elif args.command == "edit-export":
            exported = edit_export(args.run_dir, args.judges_dir, args.dest)
            result = {"status": "exported", "editor_attempts": len(exported["editor_records"]),
                      "pairs": len(exported["pairs"]), "judge_records": len(exported["judge_records"])}
        elif args.command == "prepare":
            manifest = prepare(args.dest, args.dataset, args.protocol, args.rubric, args.previous_snapshots)
            result = {"status": "prepared", "job_count": len(manifest["jobs"])}
        elif args.command == "run":
            result = run(args.run_dir, args.execute)
        elif args.command == "blind":
            packet = blind(args.run_dir, args.dest)
            result = {"comparable": len(packet["trios"]), "not_comparable": len(packet["not_comparable"])}
        elif args.command == "judge-prepare":
            manifest = judge_prepare(args.packet, args.rubric, args.dest)
            result = {"status": "prepared", "job_count": len(manifest["jobs"])}
        elif args.command == "judge-run":
            result = judge_run(args.dest, args.execute)
        else:
            exported = export(args.run_dir, args.judges_dir, args.dest)
            result = {"status": "exported", "writer_attempts": len(exported["writer_records"]),
                      "trios": len(exported["trios"]), "judge_records": len(exported["judge_records"])}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 1 if result.get("failed", 0) else 0


if __name__ == "__main__":
    sys.exit(main())
