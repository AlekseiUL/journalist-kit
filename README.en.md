# Journalist Kit

A Russian-first editorial skill for the writing agent you already use.

[Русский README](README.md) · [Installation](docs/integrations.md) · [Evaluation](docs/evaluation.md)

Source-bound journalism, a configurable author voice, and structural anti-slop
editing. One writer by default; specialist help only when it adds real value.
The core workflow is provider-independent. The reference material and primary
evaluation suite are in Russian; the checker includes selected English signals,
not a claim of equivalent editorial quality in every language.

**Beta:** no guaranteed factual accuracy, human authorship, detector evasion,
automatic publication, or superiority over other writing skills.

**Beta.3 reliability pilot:** instructions shortened by about a quarter; 36 new
outputs compared with a plain writer and beta.1. Both blinded model judges more
often preferred beta.3 to the plain writer. However, improved reliability over
beta.1 was not established: both flagged more critical outputs in beta.3.
This is an **experimental beta**, not an unconditionally recommended upgrade.
[Every output, results, costs and limitations](evals/reliability-2026-09-07/README.md).
The [first beta.1 pilot](evals/ab-2026-09-07/README.md) and
[beta.2 experiment](evals/revision-2026-09-07/README.md) remain unchanged.

This is currently an unpublished local release candidate. The owner has not yet
confirmed the proposed public repository and license. The clone command below
is for the eventual publication; local checkout users can test the installer now.

## Install

Journalist Kit ships one skill with the stable installation ID `source-and-voice`.

Requires Python 3.10+ and your existing agent. No runtime dependencies, network
requests, telemetry, or model credentials are embedded in the package.

```bash
git clone https://github.com/AlekseiUL/journalist-kit.git
cd journalist-kit
python3 tools/install.py --dest "$HOME/.hermes/skills"
python3 tools/install.py --dest "$HOME/.hermes/skills" --apply
```

Preview first; apply copies one new skill without overwriting existing files.
Use the appropriate profile's skills directory, then start a new session.
For another client, choose its documented skills directory. This is not an
all-clients compatibility claim.

## What is included

- Six journalism modes and six forms, with a lighter route for ordinary posts.
- A single coordinated composition, voice, and language edit followed by a
  source-fidelity reread.
- Source meaning takes priority over a smoother phrase: when a flourish requires
  an assumption, keep the supported wording and improve selection, order and rhythm.
- Optional user-owned voice preferences; no private example corpus included.
- The requested tone works without a separate voice profile; supported author
  intent is preserved alongside factual constraints, not replaced with neutral prose.
- A standalone standard-library Python checker: exact UTF-8 hashing, measured
  length, bounded style findings, and limited source-use diagnostics.
- Synthetic examples and evaluation cases with explicit failure criteria.

The checker always reports semantic source review as `NOT_REVIEWED`. In the
earlier 36-output beta.2 pilot it raised no style signals and missed semantic errors;
a clean lint is not a fact-check. See [the checker audit](evals/revision-2026-09-07/AUDIT.md),
[the checker contract](docs/checker.md) and
[the release evidence](docs/release-check.md).

An optional [reference author configuration](docs/author-agent.md) uses the same
skill; you do not need to create another agent to use this project.

## Test

```bash
python3 -m unittest discover -s tests -v
python3 tools/validate_package.py
```

See [contribution guidelines](CONTRIBUTING.md), [methodological provenance](THIRD_PARTY_NOTICES.md),
and [license](LICENSE).
