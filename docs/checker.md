# Editorial checker contract

The portable checker uses Python 3.10+ and only the standard library. It performs
no network requests, model calls, writes, or publishing. Importing it performs no
I/O. Source contents and profile strings are inert data, never code or commands.

```sh
python3 skills/source-and-voice/scripts/editorial_check.py BODY.txt \
  --sources SOURCES.json --voice VOICE.json --min-words 100 --max-chars 3000
```

All flags are optional. The body is the exact text intended for publication.
`--min-words`, `--max-words`, `--min-chars`, and `--max-chars` accept nonnegative
integers up to 1,000,000,000, with each minimum no greater than its maximum.
Bounds are inclusive. No strict-style mode is provided: editorial heuristics
cannot determine whether prose is acceptable.

The reusable API is `analyze(text, sources=None, voice=None, *, min_words=None,
max_words=None, min_chars=None, max_chars=None)`. It returns a JSON-ready dict and
raises `InputError` (a `ValueError`) for invalid inputs. It does not modify inputs.

## Input schemas and limits

Source packets require exactly these fields; every record requires every listed
field. IDs must be unique. An empty `sources` array is accepted with an advisory.

```json
{
  "version": 1,
  "sources": [
    {
      "id": "s1",
      "content": "Работы займут две недели.",
      "origin": "Synthetic public bulletin",
      "kind": "record",
      "use": "public"
    }
  ]
}
```

`kind` is `record`, `statement`, or `author_note`. `use` is `public`,
`paraphrase_only`, or `private`. These labels are supplied by the caller and do
not prove authenticity, authority, permission, or independence. Record strings
must be nonempty. Limits: 100 records; ID 120 characters; origin 2,000 characters;
content 200,000 characters. Unknown fields, wrong types, duplicate JSON keys,
non-finite numbers, malformed JSON, and invalid UTF-8 are rejected.

Voice profiles are optional objects allowing only `name`, `language`, `register`,
`rhythm` (nonempty strings, at most 2,000 characters each), plus `avoid` and
`prefer` (arrays of at most 64 nonempty strings, 200 characters each). All profile
fields are optional. Only `avoid` triggers automated matching: case-insensitive
literal substrings, including punctuation and regex metacharacters. `prefer`
never creates required keywords. Language, register, rhythm, and preferences
remain instructions for the writer and reviewer; this checker does not score them.

Every input file is limited to 1 MiB (1,048,576 bytes). API text and canonical
serialized JSON packets have the same limit. Reading is bounded before decoding.

## Output and exit codes

JSON output has `version: 1`, `measurements` (`words`, `chars`, `utf8_bytes`),
`body_sha256`, `style`, `source_review`, `issues`, `limits`, and `exit_code`.
Non-ASCII characters are JSON-escaped for terminals with any ASCII-compatible
encoding; decoding the JSON restores the exact Unicode strings. This does not
change the input text, its hash, or character offsets.

- Words use Python's `str.split()` with Unicode whitespace; punctuation does not
  split words. Characters are Unicode code points, including whitespace and
  each code point of an emoji/combining sequence, not grapheme clusters.
- Body SHA-256 is over exact UTF-8 bytes. CRLF and LF differ; no whitespace,
  Unicode, BOM, or newline normalization occurs.
- `style.status` is `NO_SIGNALS` or `REVIEW`. Neither means ready to publish.
- `source_review.status` is always `NOT_REVIEWED`, even with exact source matches.
- Supplied sources add `source_packet_sha256`. CLI hashing uses exact file bytes
  (`source_packet_hash_basis: file_bytes`). API hashing uses sorted, compact,
  UTF-8 JSON (`canonical_json`); different file whitespace is unavailable to an
  API receiving an already parsed object.
- Findings contain `rule`, `quote`, `start`, `end`, and `message`; source findings
  may include `source_ids`. Offsets are zero-based Python string offsets with
  exclusive end, and quote is the exact body slice. Excerpts are capped at 240
  characters; comparisons may use a longer full span. Packet-level findings
  have empty quote and offsets 0/0.
- `issues` repeats findings with `severity: warning` or `error` and includes
  objective length/empty-body errors. Findings are deterministic in rule and
  input order. Each style/source category is capped at 40 findings; `limits`
  reports truncation. Error detection continues after the finding cap, and a
  restricted-quote error replaces a retained warning if needed to show why the
  command returned exit 1.
- Exit 0: no objective blocking issue detected; style/source advice may remain.
  Exit 1: empty body, violated length bound, or detected restricted direct quote.
  Exit 2: invalid CLI arguments, schema, encoding, size, or I/O. Invalid-input
  JSON contains `version`, an `INPUT_ERROR` issue, and `exit_code`; measurements
  are unavailable. `--help` prints ordinary argparse help.

## What signals mean

Style rules cover a small set of broad opening formulas and closing/helpdesk
phrases, exact repeated paragraphs/sentences, three or more similar contrast
constructions, and user-selected literal avoidance phrases. They suggest a
review, never automatic rewriting or deletion. Duplicate paragraphs require at
least eight whitespace words and compare exact trimmed text. Repeated sentences
use a conservative punctuation approximation and the same minimum word count.
Semantic paraphrase duplication is outside the checker's reach. Neutral grammar
such as `является`, em dashes, lists, short sentences, and ordinary contrasts
is not globally flagged.

Quote candidates are paired guillemets `«…»`, curly double quotes `“…”`, and
paired straight double quotes on one line without backslashes. Apostrophes and unpaired quotes are
not treated as quotations. Up to 1,000 candidates are compared; `limits` exposes
the examined count and whether remaining candidates were skipped. Nested quotes,
escaped quotes, measurements, and typography can still require manual review.

An exact quotation substring found only in records labeled `private` or
`paraphrase_only` causes `RESTRICTED_QUOTE`. If both public and restricted records
match, `MIXED_QUOTE_PERMISSIONS` requests attribution/permission review without
blocking. A source packet cannot establish legal permission. Unquoted paraphrases,
identifying details, privacy leaks, or prohibited factual use are not detected.
An exact match found only in author notes requests original-attribution review.

Quotes absent from the supplied packet are advisory only. To avoid flagging
every quoted term, unmatched quotes require at least three whitespace words or
20 characters. Absence from a selective packet does not prove fabrication;
matching it does not prove authenticity or verify attribution. Comparison does
not normalize case, punctuation, whitespace, ellipses, or translation.

Records with identical origins after trimming receive a shared-origin advisory;
different origin strings do not establish independence. Strength markers such
as `сразу`, `всегда`, `значительно`, and `automatically` absent from the source
excerpts request semantic review of certainty/scope. This is lexical absence,
not evidence of claim distortion, and synonyms or an unrelated matching word
can change the signal. A reviewer must compare claims with the relevant sources.

There is no overall PASS/READY result, factual confidence, authorship detection,
or numeric naturalness score. Human or explicitly identified model editorial
review remains separate from these diagnostics.
