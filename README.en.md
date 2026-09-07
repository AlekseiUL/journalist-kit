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

**First paired pilot:** 24 outputs on six new synthetic briefs, reviewed by two
blinded model judges. Both preferred Kit more often for practical suitability,
but livelier writing was not established; both variants still made errors.
[Full results, every output, costs and limitations](evals/ab-2026-09-07/README.md).

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
- Optional user-owned voice preferences; no private example corpus included.
- A standalone standard-library Python checker: exact UTF-8 hashing, measured
  length, bounded style findings, and limited source-use diagnostics.
- Synthetic examples and evaluation cases with explicit failure criteria.

The checker always reports semantic source review as `NOT_REVIEWED`. A clean
lint is not a fact-check. See [the checker contract](docs/checker.md) and
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
