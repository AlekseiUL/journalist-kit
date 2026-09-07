#!/usr/bin/env python3
"""Bounded, offline editorial diagnostics. Python 3.10+, standard library only."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


VERSION = 1
MAX_BYTES = 1_048_576
MAX_FINDINGS = 40
MAX_QUOTES = 1_000
MAX_EXCERPT = 240


class InputError(ValueError):
    """Invalid input; the command line maps this exception to exit status 2."""


def _bytes(text: str, label: str) -> bytes:
    if not isinstance(text, str):
        raise InputError(f"{label} must be a string")
    try:
        encoded = text.encode("utf-8")
    except UnicodeError as exc:
        raise InputError(f"{label} must contain valid UTF-8 characters") from exc
    if len(encoded) > MAX_BYTES:
        raise InputError(f"{label} exceeds {MAX_BYTES} UTF-8 bytes")
    return encoded


def _fields(value, allowed: set[str], required: set[str], label: str) -> None:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise InputError(f"{label} must be an object")
    if set(value) - allowed or required - set(value):
        raise InputError(f"{label} has missing or unknown fields")


def _string(value, label: str, limit: int) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise InputError(f"{label} must be a nonempty string of at most {limit} characters")
    _bytes(value, label)


def _canonical_bytes(value, label: str) -> bytes:
    try:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise InputError(f"{label} must be finite JSON data") from exc
    return _bytes(serialized, label)


def _validate_sources(packet) -> bytes:
    _fields(packet, {"version", "sources"}, {"version", "sources"}, "sources")
    if type(packet["version"]) is not int or packet["version"] != VERSION:
        raise InputError("sources.version must be the integer 1")
    records = packet["sources"]
    if not isinstance(records, list) or len(records) > 100:
        raise InputError("sources.sources must be an array of at most 100 records")
    ids = set()
    keys = {"id", "content", "origin", "kind", "use"}
    for index, record in enumerate(records):
        label = f"sources.sources[{index}]"
        _fields(record, keys, keys, label)
        for key, limit in (("id", 120), ("content", 200_000), ("origin", 2_000),
                           ("kind", 20), ("use", 20)):
            _string(record[key], f"{label}.{key}", limit)
        if record["id"] in ids:
            raise InputError("source IDs must be unique")
        ids.add(record["id"])
        if record["kind"] not in {"record", "statement", "author_note"}:
            raise InputError(f"{label}.kind must be record, statement, or author_note")
        if record["use"] not in {"public", "paraphrase_only", "private"}:
            raise InputError(f"{label}.use must be public, paraphrase_only, or private")
    return _canonical_bytes(packet, "sources")


def _validate_voice(voice) -> None:
    _fields(voice, {"name", "language", "register", "rhythm", "avoid", "prefer"},
            set(), "voice")
    for key in ("name", "language", "register", "rhythm"):
        if key in voice:
            _string(voice[key], f"voice.{key}", 2_000)
    for key in ("avoid", "prefer"):
        if key not in voice:
            continue
        if not isinstance(voice[key], list) or len(voice[key]) > 64:
            raise InputError(f"voice.{key} must be an array of at most 64 strings")
        for item in voice[key]:
            _string(item, f"voice.{key} item", 200)
    _canonical_bytes(voice, "voice")


class _Findings:
    def __init__(self, text: str):
        self.text = text
        self.items: list[dict] = []
        self.truncated = False
        self.has_error = False

    def add(self, rule: str, start: int, end: int, message: str,
            severity: str = "warning", **details) -> None:
        self.has_error |= severity == "error"
        end = min(end, start + MAX_EXCERPT)
        finding = {"rule": rule, "quote": self.text[start:end],
                   "start": start, "end": end, "message": message,
                   "severity": severity, **details}
        if len(self.items) >= MAX_FINDINGS:
            self.truncated = True
            # A warning flood must not hide the explanation for exit status 1.
            if severity == "error":
                for index in range(len(self.items) - 1, -1, -1):
                    if self.items[index]["severity"] != "error":
                        self.items[index] = finding
                        break
            return
        self.items.append(finding)

    def public(self) -> list[dict]:
        return [{k: v for k, v in item.items() if k != "severity"}
                for item in self.items]


def _paragraphs(text: str):
    start = 0
    for boundary in re.finditer(r"(?:\r?\n[ \t]*){2,}|\Z", text):
        raw = text[start:boundary.start()]
        trimmed = raw.strip()
        if trimmed:
            offset = start + len(raw) - len(raw.lstrip())
            yield offset, offset + len(trimmed), trimmed
        start = boundary.end()


def _style(text: str, voice, findings: _Findings) -> None:
    opener = re.match(
        r"\s*(?:в современном (?:быстро меняющемся )?мире|"
        r"в эпоху стремительных перемен|in today['’]s fast-paced world|"
        r"in (?:the|an) ever-evolving (?:world|landscape))\b", text, re.I)
    if opener:
        start = opener.start() + len(opener.group()) - len(opener.group().lstrip())
        findings.add("BIG_PICTURE_OPENING", start, opener.end(),
                     "Check whether this broad opening can start with a concrete fact.")
    tail_start = max(0, len(text) - 600)
    for match in re.finditer(
        r"\b(?:подводя итоги?|в заключение хочется (?:сказать|отметить)|"
        r"надеюсь,? (?:это|я) (?:помог|помогла|помогло)|"
        r"если (?:хотите|нужно),? я могу|"
        r"in conclusion|i hope (?:this|that) helps|let me know if you(?:['’]d| would) like)\b",
        text[tail_start:], re.I):
        findings.add("FORMULAIC_TAIL", tail_start + match.start(), tail_start + match.end(),
                     "Review this closing formula against the publication's purpose.")
    seen = set()
    duplicate_ranges = []
    for start, end, paragraph in _paragraphs(text):
        if len(paragraph.split()) < 8:
            continue
        if paragraph in seen:
            duplicate_ranges.append((start, end))
            findings.add("DUPLICATE_PARAGRAPH", start, end,
                         "This paragraph repeats exactly; check whether repetition is intentional.")
        seen.add(paragraph)
    seen = set()
    duplicate_index = 0
    for match in re.finditer(r"[^.!?\n]+[.!?]?", text):
        if match.group()[-1] not in ".!?" or (match.end() < len(text) and not text[match.end()].isspace()):
            continue
        sentence = match.group().strip()
        if len(sentence.split()) < 8:
            continue
        start = match.end() - len(sentence)
        while duplicate_index < len(duplicate_ranges) and duplicate_ranges[duplicate_index][1] <= start:
            duplicate_index += 1
        in_duplicate = (duplicate_index < len(duplicate_ranges)
                        and duplicate_ranges[duplicate_index][0] <= start)
        if sentence in seen and not in_duplicate:
            findings.add("REPEATED_SENTENCE", start, match.end(),
                         "This sentence repeats exactly; assess whether it repeats a thesis or serves a purpose.")
        seen.add(sentence)
    contrasts = list(re.finditer(
        r"\b(?:это\s+не\b[^.!?\n]{1,160},\s*а\b|"
        r"(?:this|it)\s+is\s+not\b[^.!?\n]{1,160}[,;]\s*(?:this|it)\s+is\b)"
        r"[^.!?\n]{1,160}", text, re.I))
    if len(contrasts) >= 3:
        for match in contrasts[:MAX_FINDINGS + 1]:
            findings.add("REPEATED_CONTRAST", match.start(), match.end(),
                         "Several similar contrast constructions recur. Keep those that express a real distinction.")
    for literal in (voice or {}).get("avoid", []):
        for match in re.finditer(re.escape(literal), text, re.I):
            findings.add("VOICE_AVOID", match.start(), match.end(),
                         "This literal phrase appears in the supplied voice profile's avoid list.")
            if findings.truncated:
                break


_QUOTES = re.compile(r"«([^«»]+)»|“([^“”]+)”|(?<![\w\\])\"([^\"\\\r\n]+)\"(?!\w)")
_STRENGTH = re.compile(
    r"\b(?:сразу|всегда|значительно|гарантированно|доказано|"
    r"automatically|always|immediately|significantly|guaranteed)\b", re.I)


def _source_checks(text: str, records: list[dict], findings: _Findings) -> dict:
    origins: dict[str, list[str]] = {}
    for record in records:
        origins.setdefault(record["origin"].strip(), []).append(record["id"])
    for ids in origins.values():
        if len(ids) > 1:
            findings.add("SHARED_ORIGIN", 0, 0,
                         "These records share an origin; do not count them as independent confirmations.",
                         source_ids=ids)
    if not records:
        findings.add("EMPTY_SOURCE_PACKET", 0, 0, "The supplied source packet contains no records.")
    examined = 0
    truncated = False
    for match in _QUOTES.finditer(text):
        if examined >= MAX_QUOTES:
            truncated = True
            findings.add("QUOTE_SCAN_LIMIT", match.start(), match.end(),
                         "The quote candidate limit was reached; remaining quotes were not compared.")
            break
        examined += 1
        inner = next(value for value in match.groups() if value is not None)
        matches = [record for record in records if inner in record["content"]]
        if matches and all(record["use"] != "public" for record in matches):
            findings.add("RESTRICTED_QUOTE", match.start(), match.end(),
                         "This exact excerpt occurs only in supplied records marked private or paraphrase_only; remove the direct quote or resolve permission.",
                         severity="error", source_ids=[record["id"] for record in matches])
        elif matches and any(record["use"] != "public" for record in matches):
            findings.add("MIXED_QUOTE_PERMISSIONS", match.start(), match.end(),
                         "This excerpt occurs in both public and restricted records; check its attribution and permission.",
                         source_ids=[record["id"] for record in matches])
        elif not matches and (len(inner.split()) >= 3 or len(inner) >= 20):
            findings.add("QUOTE_NOT_IN_PACKET", match.start(), match.end(),
                         "No exact excerpt match in this selective packet. Review attribution and wording; this does not establish fabrication.")
        if matches and all(record["kind"] == "author_note" for record in matches):
            findings.add("AUTHOR_NOTE_QUOTE", match.start(), match.end(),
                         "Only author notes contain this excerpt; establish the original attribution before treating it as a sourced quotation.")
    source_terms = {match.group().casefold() for record in records
                    for match in _STRENGTH.finditer(record["content"])}
    for match in _STRENGTH.finditer(text):
        if match.group().casefold() not in source_terms:
            findings.add("CLAIM_STRENGTH_REVIEW", match.start(), match.end(),
                         "This strength marker is absent from the supplied excerpts. Semantically assess scope and certainty; absence of a word does not prove a distorted claim.")
            if findings.truncated:
                break
    return {"quote_candidates_examined": examined, "quote_scan_truncated": truncated}


def analyze(text: str, sources=None, voice=None, *, min_words: int | None = None,
            max_words: int | None = None, min_chars: int | None = None,
            max_chars: int | None = None) -> dict:
    """Return advisory diagnostics; invalid input raises InputError, with no I/O.

    Characters are Unicode code points, words are whitespace-separated tokens.
    Input text is never normalized. API source hashes use canonical JSON; the CLI
    replaces this hash with that of the exact source file bytes and labels it.
    """
    raw = _bytes(text, "body")
    bounds = {"min_words": min_words, "max_words": max_words,
              "min_chars": min_chars, "max_chars": max_chars}
    for name, value in bounds.items():
        if value is not None and (type(value) is not int or not 0 <= value <= 1_000_000_000):
            raise InputError(f"{name} must be an integer from 0 to 1000000000")
    for unit in ("words", "chars"):
        low, high = bounds[f"min_{unit}"], bounds[f"max_{unit}"]
        if low is not None and high is not None and low > high:
            raise InputError(f"min_{unit} must not exceed max_{unit}")
    source_bytes = _validate_sources(sources) if sources is not None else None
    if voice is not None:
        _validate_voice(voice)
    measurements = {"words": len(text.split()), "chars": len(text), "utf8_bytes": len(raw)}
    objective = []
    if not text.strip():
        objective.append({"rule": "EMPTY_BODY", "severity": "error", "message": "The body is empty or contains only whitespace."})
    for name, bound in bounds.items():
        if bound is None:
            continue
        direction, unit = name.split("_")
        count = measurements[unit]
        if (direction == "min" and count < bound) or (direction == "max" and count > bound):
            objective.append({"rule": name.upper(), "severity": "error",
                              "message": f"Body has {count} {unit}; {name} is {bound}."})
    style = _Findings(text)
    _style(text, voice, style)
    source = _Findings(text)
    scan = {"quote_candidates_examined": 0, "quote_scan_truncated": False}
    if sources is not None:
        scan = _source_checks(text, sources["sources"], source)
    result = {
        "version": VERSION,
        "body_sha256": hashlib.sha256(raw).hexdigest(),
        "measurements": measurements,
        "style": {"status": "REVIEW" if style.items else "NO_SIGNALS", "findings": style.public()},
        "source_review": {"status": "NOT_REVIEWED", "findings": source.public()},
        "issues": objective + style.items + source.items,
        "limits": {"max_findings_per_category": MAX_FINDINGS,
                   "style_findings_truncated": style.truncated,
                   "source_findings_truncated": source.truncated, **scan},
        "exit_code": 1 if objective or source.has_error else 0,
    }
    if source_bytes is not None:
        result["source_packet_sha256"] = hashlib.sha256(source_bytes).hexdigest()
        result["source_packet_hash_basis"] = "canonical_json"
    return result


def _read(path: str, label: str) -> bytes:
    try:
        with Path(path).open("rb") as handle:
            data = handle.read(MAX_BYTES + 1)
    except OSError as exc:
        raise InputError(f"Cannot read {label}: {exc.strerror or type(exc).__name__}") from exc
    if len(data) > MAX_BYTES:
        raise InputError(f"{label} exceeds {MAX_BYTES} bytes")
    return data


def _decode(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeError as exc:
        raise InputError(f"{label} must be UTF-8") from exc


def _json(data: bytes, label: str):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InputError(f"{label} has a duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(_value):
        raise InputError(f"{label} contains a non-finite JSON value")

    try:
        return json.loads(_decode(data, label), object_pairs_hook=unique,
                          parse_constant=invalid_constant)
    except (ValueError, RecursionError) as exc:
        raise InputError(f"{label} is invalid JSON: {exc}") from exc


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError(message)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description=__doc__)
    parser.add_argument("body", help="UTF-8 publication body; exact bytes are hashed")
    parser.add_argument("--sources", help="Source packet JSON (untrusted data, never executed)")
    parser.add_argument("--voice", help="Optional user-selected voice JSON")
    for name in ("min_words", "max_words", "min_chars", "max_chars"):
        parser.add_argument("--" + name.replace("_", "-"), type=int)
    try:
        args = parser.parse_args(argv)
        body = _decode(_read(args.body, "body"), "body")
        source_bytes = _read(args.sources, "sources") if args.sources else None
        sources = _json(source_bytes, "sources") if source_bytes is not None else None
        voice = _json(_read(args.voice, "voice"), "voice") if args.voice else None
        # JSON null is a supplied malformed packet, not an omitted option.
        if args.sources and sources is None:
            raise InputError("sources must be an object")
        if args.voice and voice is None:
            raise InputError("voice must be an object")
        result = analyze(body, sources, voice, min_words=args.min_words,
                         max_words=args.max_words, min_chars=args.min_chars,
                         max_chars=args.max_chars)
        if source_bytes is not None:
            result["source_packet_sha256"] = hashlib.sha256(source_bytes).hexdigest()
            result["source_packet_hash_basis"] = "file_bytes"
    except InputError as exc:
        result = {"version": VERSION, "issues": [{"rule": "INPUT_ERROR", "severity": "error",
                                                   "message": str(exc)}], "exit_code": 2}
    # ASCII JSON escapes preserve Unicode values even on non-UTF-8 terminals.
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
