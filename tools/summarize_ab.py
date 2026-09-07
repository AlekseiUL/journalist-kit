#!/usr/bin/env python3
"""Validate and export the fixed A/B pilot offline, excluding private CLI logs."""

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

import run_ab as ab
import review_ab as judges


ARMS = ("baseline", "treatment")
JUDGE_IDS = ("judge1", "judge2")
STATUSES = ("pass", "revision", "critical", "missing_review", "non_comparable")
PREFERENCES = (*ARMS, "tie", "disagreement", "missing_review", "non_comparable")
METRICS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
LOCAL_PATH = re.compile(
    r"(?<![\w:])/(?:Users|home|root|private|tmp|var|etc|opt|Volumes|Library|Applications|usr)/"
    r"|\b[A-Za-z]:[\\/]")
AUTH_VALUE = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}|\bBearer\s+[A-Za-z0-9._~+/=-]{16,}")
META_KEYS = (
    "schema_version", "status", "flags", "requested_model", "requested_reasoning_effort",
    "started_at_utc", "ended_at_utc", "wall_seconds", "returncode", "usage",
    "prompt_sha256", "final_sha256", "raw_stdout_sha256", "raw_stderr_sha256",
    "dataset_sha256", "temperature", "provider_seed", "final_selection",
    "cli_warning_count", "thread_count", "turn_started_count", "turn_completed_count",
)
CAVEATS = [
    "Requested model identifiers do not establish the resolved backend model or snapshot; unknown values remain unknown.",
    "This is a descriptive comparison of fully injected instructions on six synthetic cases with two repeats, not a population estimate.",
    "Twelve pairs are not twelve independent topics; the two model judges are not independent human reviewers.",
    "No statistical significance, general accuracy, or causal population improvement is inferred.",
    "Missing token measurements are unknown, not zero. Cached input tokens are reported separately and are part of input tokens, not added to them.",
    "Failed writer attempts remain in all twelve pair denominators and are non-comparable. Missing judge reviews are not ties.",
    "Frozen prompts are reproducible from snapshots and the common wrapper; provider temperature, generation seed, maximum token budget and hidden host instructions remain unknown.",
]


def metadata(record):
    """Allowlist metadata; never copy prompts, agent messages, raw errors or IDs."""
    result = {key: record[key] for key in META_KEYS if key in record}
    for key in ("resolved_model", "backend_model_snapshot", "cli_version"):
        result[key] = record.get(key) or "unknown"
    return result


def ensure_publishable(value):
    """Refuse suspicious local paths/auth values instead of changing exact texts."""
    if isinstance(value, str):
        if LOCAL_PATH.search(value) or AUTH_VALUE.search(value):
            raise ValueError("public export contains a local path or possible authentication value")
    elif isinstance(value, dict):
        for key, item in value.items():
            ensure_publishable(key)
            ensure_publishable(item)
    elif isinstance(value, list):
        for item in value:
            ensure_publishable(item)


def normalize_review(review, mapping, swapped_xy=False):
    """Translate judge-local labels to arms through the original X/Y mapping."""
    original = {"X": "Y", "Y": "X"} if swapped_xy else {"X": "X", "Y": "Y"}
    labels = {label: mapping[original[label]]["arm"] for label in ("X", "Y")}

    def preference(key):
        value = review[key]
        return "tie" if value == "tie" else labels[value]

    return {
        "statuses": {labels[label]: review[label]["status"] for label in ("X", "Y")},
        "editorial_preference": preference("editorial_preference"),
        "editorial_reason": review["editorial_reason"],
        "practical_preference": preference("practical_preference"),
        "practical_reason": review["practical_reason"],
    }


def consensus(first, second, comparable=True):
    if not comparable:
        return "non_comparable"
    if first is None or second is None:
        return "missing_review"
    if first not in (*ARMS, "tie") or second not in (*ARMS, "tie"):
        raise ValueError("invalid normalized preference")
    return first if first == second else "disagreement"


def measured(records):
    result = {}
    for key in (*METRICS, "wall_seconds"):
        values = []
        for record in records:
            value = record.get(key) if key == "wall_seconds" else record.get("usage", {}).get(key)
            if value is not None:
                if type(value) not in (int, float) or value < 0:
                    raise ValueError("invalid measurement")
                if key != "wall_seconds" and type(value) is not int:
                    raise ValueError("token measurements must be integers")
                values.append(value)
        result[key] = {"observed_total": sum(values) if values else None,
                       "reported_records": len(values), "missing_records": len(records) - len(values)}
    return result


def counts(values, labels):
    found = Counter(values)
    return {label: found[label] for label in labels}


def validate_mapping(manifest, mapping, cases):
    jobs = {job["output_id"]: job for job in manifest["jobs"]}
    if len(jobs) != 24 or len(mapping) != 12:
        raise ValueError("expected 24 unique jobs and 12 pairs")
    used, identities, pair_ids = set(), set(), set()
    for pair in mapping:
        pair_id, case_id, repeat = pair["pair_id"], pair["case_id"], pair["repeat"]
        if (not SAFE_ID.fullmatch(pair_id) or pair_id in pair_ids or case_id not in cases
                or not SAFE_ID.fullmatch(case_id) or repeat not in (1, 2)
                or (case_id, repeat) in identities):
            raise ValueError("invalid or duplicated pair identity")
        pair_ids.add(pair_id)
        identities.add((case_id, repeat))
        if {pair[label]["arm"] for label in ("X", "Y")} != set(ARMS):
            raise ValueError("pair must contain both arms")
        for label in ("X", "Y"):
            entry = pair[label]
            job = jobs[entry["output_id"]]
            if (entry["output_id"] in used or job["case_id"] != case_id
                    or job["repeat"] != repeat or job["arm"] != entry["arm"]):
                raise ValueError("mapping does not match writer jobs")
            used.add(entry["output_id"])
    if used != set(jobs) or identities != {(key, repeat) for key in cases for repeat in (1, 2)}:
        raise ValueError("mapping omits writer outputs")
    return jobs


def validate_packet(packet, mapping, cases, records):
    """Connect the hashed judge inputs to these exact writer outputs."""
    expected, failed = {}, set()
    for pair in mapping:
        if any(records[pair[label]["output_id"]]["status"] != "completed" for label in ("X", "Y")):
            failed.add(pair["pair_id"])
            continue
        expected[pair["pair_id"]] = pair
    pairs = packet["pairs"]
    if (len(pairs) != len(expected) or {pair["pair_id"] for pair in pairs} != set(expected)
            or {pair["pair_id"] for pair in packet["not_comparable"]} != failed):
        raise ValueError("judge packet does not cover writer pairs")
    for pair in pairs:
        original = expected[pair["pair_id"]]
        case = cases[original["case_id"]]
        for key in ("case_id", "repeat"):
            if pair[key] != original[key]:
                raise ValueError("judge packet pair identity changed")
        for key in ("request", "sources", "reviewer_notes"):
            if pair[key] != case[key]:
                raise ValueError("judge packet source materials changed")
        for label in ("X", "Y"):
            record = records[original[label]["output_id"]]
            wanted = {"text": record["final_text"], "word_count": record["word_count"],
                      "min_words": case["min_words"], "max_words": case["max_words"]}
            if pair[label] != wanted:
                raise ValueError("judge packet output text or measurement changed")
    return [pair["pair_id"] for pair in pairs]


def summarize(run_dir, judges_dir, dest):
    run_dir, judges_dir, dest = Path(run_dir), Path(judges_dir), Path(dest)
    if dest.exists():
        raise FileExistsError("export destination must be new")
    manifest = ab.load_run(run_dir)
    cases = {case["id"]: case for case in ab.validate_cases(ab.read_json(run_dir / "snapshots/cases.json"))}
    mapping = ab.read_json(run_dir / "mapping.json")["pairs"]
    jobs = validate_mapping(manifest, mapping, cases)
    if {path.name for path in (run_dir / "records").glob("*.json")} != {
            output_id + ".json" for output_id in jobs}:
        raise ValueError("exactly 24 terminal writer records are required")
    records = {output_id: ab.read_record(run_dir, job) for output_id, job in jobs.items()}
    frozen_files = {relative: {"sha256": expected,
                              "text": (run_dir / relative).read_bytes().decode("utf-8")}
                    for relative, expected in manifest["hashes"].items() if relative.startswith("snapshots/")}
    documents = {name: frozen_files["snapshots/skill/" + name]["text"] for name in ab.SKILL_FILES}
    outputs, public_writers = {}, []
    for output_id, job in jobs.items():
        record, case = records[output_id], cases[job["case_id"]]
        flags = record.get("flags")
        if (not isinstance(flags, list) or not all(isinstance(flag, str) for flag in flags)
                or (record["status"] == "completed") != (not flags)
                or record.get("case_id") != job["case_id"] or record.get("repeat") != job["repeat"]):
            raise ValueError("invalid terminal writer metadata")
        text = record.get("final_text")
        if text is not None and not isinstance(text, str):
            raise ValueError("invalid writer final text")
        if record["status"] == "completed" and (text is None or not text.strip()):
            raise ValueError("completed writer has no final text")
        word_count = len(text.split()) if text is not None else None
        if record.get("word_count") != word_count:
            raise ValueError("writer word count does not match exact final text")
        expected_prompt = ab.prompt_for(case, documents if job["arm"] == "treatment" else None)
        if record["prompt"] != expected_prompt:
            raise ValueError("writer prompt cannot be reconstructed from snapshots and wrapper")
        relative = "outputs/{}-r{}-{}.txt".format(job["case_id"], job["repeat"], job["arm"])
        if text is not None:
            outputs[relative] = text.encode("utf-8")
        public_writers.append({**metadata(record), "output_id": output_id,
                               "case_id": job["case_id"], "repeat": job["repeat"], "arm": job["arm"],
                               "text_path": relative if text is not None else None,
                               "word_count": word_count, "min_words": case["min_words"],
                               "max_words": case["max_words"],
                               "within_word_range": case["min_words"] <= word_count <= case["max_words"]
                               if word_count is not None else None})

    judge_manifest = judges.load_manifest(judges_dir)
    judge_jobs = judge_manifest["jobs"]
    packet = ab.read_json(judges_dir / "packet.json")
    comparable_ids = validate_packet(packet, mapping, cases, records)
    batch_count = (len(comparable_ids) + 5) // 6
    expected_ids = {"{}-batch{}".format(judge, batch)
                    for judge in JUDGE_IDS for batch in range(1, batch_count + 1)}
    if (len(judge_jobs) != len(expected_ids) or {job["job_id"] for job in judge_jobs} != expected_ids
            or {path.name for path in judges_dir.glob("*.record.json")} != {
                job_id + ".record.json" for job_id in expected_ids}):
        raise ValueError("terminal judge records must match all expected comparable-pair batches")
    by_judge = {judge: {} for judge in JUDGE_IDS}
    judge_records, public_judges = [], []
    for job in judge_jobs:
        judge_id, batch_string = job["job_id"].split("-batch")
        index, batch = JUDGE_IDS.index(judge_id), int(batch_string)
        order = list(reversed(comparable_ids)) if index else comparable_ids
        if (job["requested_model"] != judges.JUDGES[index] or job["swapped_xy"] != bool(index)
                or job["pair_ids"] != order[(batch - 1) * 6:batch * 6]):
            raise ValueError("judge assignment differs from the fixed blinded order")
        record = judges.read_record(judges_dir, job)
        judge_records.append(record)
        public_judges.append({**metadata(record), "job_id": job["job_id"], "judge_id": judge_id,
                              "swapped_xy": job["swapped_xy"], "pair_ids": job["pair_ids"],
                              "review": record.get("review"), "final_text": record.get("final_text")})
        if record["status"] == "completed":
            by_judge[judge_id].update({review["pair_id"]: (review, job["swapped_xy"])
                                      for review in record["review"]["reviews"]})

    pairs = []
    for pair in mapping:
        comparable = pair["pair_id"] in comparable_ids
        opinions = {}
        for judge_id in JUDGE_IDS:
            found = by_judge[judge_id].get(pair["pair_id"])
            opinions[judge_id] = normalize_review(found[0], pair, found[1]) if found and comparable else None
        preferences = {}
        for dimension in ("editorial", "practical"):
            values = [opinion[dimension + "_preference"] if opinion else None for opinion in opinions.values()]
            preferences[dimension] = consensus(*values, comparable=comparable)
        failed_records = [records[pair[label]["output_id"]] for label in ("X", "Y")
                          if records[pair[label]["output_id"]]["status"] != "completed"]
        pairs.append({"pair_id": pair["pair_id"], "case_id": pair["case_id"], "repeat": pair["repeat"],
                      "comparable": comparable,
                      "non_comparable_reasons": sorted({flag for record in failed_records for flag in record["flags"]}),
                      "judges": opinions, "consensus": preferences})

    stats = {"denominators": {"writer_attempts": 24, "pairs": 12, "cases": 6,
                              "repeats_per_case": 2, "judges": 2}, "writers": {}, "judges": {},
             "consensus": {dimension: counts((pair["consensus"][dimension] for pair in pairs), PREFERENCES)
                           for dimension in ("editorial", "practical")}}
    for arm in ARMS:
        selected = [record for record in public_writers if record["arm"] == arm]
        stats["writers"][arm] = {
            "attempts": len(selected), "statuses": counts((record["status"] for record in selected), ("completed", "failed")),
            "length_range": {"within": sum(record["within_word_range"] is True for record in selected),
                             "outside": sum(record["within_word_range"] is False for record in selected),
                             "missing_text": sum(record["within_word_range"] is None for record in selected),
                             "denominator": len(selected)}, "measurements": measured(selected)}
    for judge_id in JUDGE_IDS:
        selected = [record for record in public_judges if record["judge_id"] == judge_id]
        statuses, preferences = {}, {}
        for arm in ARMS:
            values = [(pair["judges"][judge_id]["statuses"][arm] if pair["judges"][judge_id]
                       else "missing_review" if pair["comparable"] else "non_comparable") for pair in pairs]
            statuses[arm] = counts(values, STATUSES)
        for dimension in ("editorial", "practical"):
            values = [(pair["judges"][judge_id][dimension + "_preference"] if pair["judges"][judge_id]
                       else "missing_review" if pair["comparable"] else "non_comparable") for pair in pairs]
            preferences[dimension] = counts(values, PREFERENCES)
        stats["judges"][judge_id] = {"record_statuses": counts((record["status"] for record in selected), ("completed", "failed")),
                                      "statuses_by_arm": statuses, "preferences": preferences,
                                      "pair_denominator": 12, "measurements": measured(selected)}

    results = {"schema_version": 1, "exported_at_utc": ab.utc_now(), "caveats": CAVEATS,
               "writer_manifest_sha256": ab.digest((run_dir / "manifest.json").read_bytes()),
               "judge_manifest_sha256": ab.digest((judges_dir / "manifest.json").read_bytes()),
               "writer_records": public_writers, "judge_records": public_judges,
               "mapping": mapping, "pairs": pairs, "statistics": stats}
    snapshots = {"schema_version": 1, "files": frozen_files, "common_wrapper": ab.COMMON,
                 "cli_args": manifest["cli_args"], "writer_timeout_seconds": manifest["timeout_seconds"],
                 "judge_timeout_seconds": judge_manifest["timeout_seconds"],
                 "judge_cli_args_base": judge_manifest["cli_args"],
                 "judge_models_in_order": list(judges.JUDGES),
                 "injection_mode": manifest["injection_mode"],
                 "writer_input_hashes": manifest["hashes"],
                 "judge_packet_sha256": judge_manifest["packet_sha256"],
                 "judge_rubric_sha256": judge_manifest["rubric_sha256"],
                 "judge_rubric_text": (judges_dir / "rubric.txt").read_bytes().decode("utf-8")}
    ensure_publishable(results)
    ensure_publishable(snapshots)
    for body in outputs.values():
        ensure_publishable(body.decode("utf-8"))
    dest.mkdir(parents=True, exist_ok=False)
    for relative, body in outputs.items():
        ab.save(dest / relative, body)
    ab.save(dest / "results.json", ab.json_bytes(results))
    ab.save(dest / "snapshots.json", ab.json_bytes(snapshots))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--judges-dir", type=Path, required=True)
    parser.add_argument("--dest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = summarize(args.run_dir, args.judges_dir, args.dest)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 1
    print(json.dumps({"status": "exported", "writer_attempts": len(result["writer_records"]),
                      "pairs": len(result["pairs"]), "judge_records": len(result["judge_records"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
