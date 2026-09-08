# Journalist Kit

![Hermes Journalist Kit cover](assets/journalist-kit-cover.jpg)

Source-first editing and journalistic work for the writing agent you already use.

[Русский](README.md) · [Golden path](docs/golden-path.md) · [Example](examples/library-hours/README.md) · [Installation](docs/integrations.md) · [Evaluation](docs/evaluation.md)

Journalist Kit ships one portable skill, `source-and-voice`. It helps preserve
source meaning, author voice, and the requested form, then applies one
coordinated editorial pass. It is a set of model instructions plus a standalone
local diagnostic, not a new agent, model, or service.

## What is included

- six work products: story angle, interview plan, fact-and-gap map, sourced
  explainer, full article, and investigation brief;
- six forms: straight news, service explainer, interview/profile, reported
  narrative, analysis/column, and solutions journalism;
- a shorter route for a post or minimal edit;
- an optional user-owned voice profile;
- the `editorial_check.py` Python CLI: exact UTF-8 hashing, length measurements,
  bounded style signals, and limited structural source-use diagnostics;
- synthetic examples, tests, and recorded experimental evaluations.

## Limitations

The package does not perform research, interviews, fact-checking, model
generation, or publication. It does not prove truth, human authorship, freedom
from editorial errors, superiority over other skills, or compatibility with
every client. The diagnostic always leaves semantic source review as
`NOT_REVIEWED`; exit code 0 is not permission to publish.

Published evaluations use synthetic materials. Model reviewers are not human
reviewers. The latest completed source-first holdout received `HOLD`: the
candidate introduced an unsupported scene detail, received 2/12 and 3/12
preferences from two model reviewers, and increased input by 28.2% against a
15% limit. See the [exact audit](evals/next-source-first-holdout/FINAL_AUDIT.md).
The current `0.2.0-alpha.2` is a new unverified candidate, not a proven
improvement.

## Requirements and clean installation

You need Git, Python 3.10+, and an agent that supports skill directories. The
package has no third-party Python dependencies, network requests, telemetry,
model credentials, services, MCP server, or automatic configuration changes.

```bash
git clone https://github.com/AlekseiUL/journalist-kit.git
cd journalist-kit
python3 tools/install.py --dest "$HOME/.hermes/skills"
python3 tools/install.py --dest "$HOME/.hermes/skills" --apply
```

If the repository is still private, cloning requires access and GitHub
authentication. The first command previews the plan; the second copies one new
directory. The installer does not select a profile, overwrite an existing
`source-and-voice`, restart services, or change memory, history, or
configuration. For a named profile, use its actual `skills` directory; see the
[integration guide](docs/integrations.md).

## Verify the installation

From the repository root, install into a temporary directory and run the
installed copy itself:

```bash
tmp="$(mktemp -d)"
python3 tools/install.py --dest "$tmp/skills"
python3 tools/install.py --dest "$tmp/skills" --apply
python3 "$tmp/skills/source-and-voice/scripts/editorial_check.py" \
  examples/library-hours/after.txt \
  --sources examples/library-hours/sources.json
```

Expect `DRY_RUN`, then `INSTALLED`, and
`source_review.status: NOT_REVIEWED` in the final JSON. This verifies files and
CLI execution, not editorial quality or skill discovery by a specific agent.
Start a new session and explicitly ask the agent to use `source-and-voice`.

## Short usage examples

Edit a post:

```text
Use source-and-voice. Edit this draft minimally: move the main point earlier,
remove repetition, preserve my position, and add no new facts.
Materials: ...
Draft: ...
```

Write from sources:

```text
Use source-and-voice. Prepare a sourced explainer from the attached sources.
Separate established information from unknowns, preserve caveats, and do not
invent background. Sources: ...
```

The local checker exposes these actual flags:

```bash
python3 skills/source-and-voice/scripts/editorial_check.py BODY.txt \
  --sources SOURCES.json --voice VOICE.json \
  --min-words 100 --max-chars 3000
```

See [docs/checker.md](docs/checker.md) for the full schema, limits, and exit-code
contract.

## When installation or checking fails

The installer returns JSON with `status: ERROR` and exit code 2. It does not
modify an existing skill. If failure occurs after a new directory was created,
the error explicitly asks you to inspect that incomplete new copy; there is no
automatic deletion. Check the path, permissions, and absence of an existing
`source-and-voice`, then use a new empty destination.

The diagnostic CLI returns 1 for a detected objective blocking issue and 2 for
invalid arguments, schema, encoding, size, or I/O. Do not replace a failed run
with a manual PASS claim.

## Safe update, uninstall, and rollback

The installer intentionally does not update in place. End the active session,
save the installed directory under a new name outside the discovered skills
directory, and install the new version into the released path. Keep personal
voice data outside the package.

To disable or uninstall, move only the installed `source-and-voice` directory
to a backup location you choose. To roll back, move the new copy out of the
skills directory and return the saved copy to its previous path. Do not remove
other skills, the profile, or history. Start a new session and repeat the
installed-CLI check after any change.

## Verify the repository

```bash
python3 -m unittest discover -s tests -v
python3 tools/validate_package.py --release
python3 -m compileall -q skills tools tests
git diff --check
```

Tests cover code, package completeness, links, syntax, schemas, installation,
and safety constraints. They do not assess literary quality or factual truth.
Reproducible reports must use permitted, anonymized materials; see
[CONTRIBUTING.md](CONTRIBUTING.md).

## License and provenance

The code and installable skill are available under the [MIT License](LICENSE).
Methodological references and reuse boundaries are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Private correspondence,
personal voice corpora, and closed production integrations are not included.

## Resources

- YouTube: https://youtube.com/@alekseiulianov
- Telegram SPRUT_AI: https://t.me/Sprut_AI
- Telegram chat: https://t.me/+eH-qNIDmud8zNDZi
- AI Операционка: https://t.me/tribute/app?startapp=sJyg
- GitHub: https://github.com/AlekseiUL
