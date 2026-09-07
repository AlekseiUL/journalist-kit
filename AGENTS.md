# Source & Voice

A portable, Russian-first editorial skill. One writer, source-bound claims,
an optional user-owned voice profile, and one coordinated editorial pass.

## Scope

- Work only in this repository. Never modify a live agent profile as a test.
- Use synthetic public examples. Do not copy private conversations, author
  samples, local absolute paths, authentication, or production integration code.
- Keep the installable package self-contained in `skills/source-and-voice/`.
- Python helpers use the standard library and Python 3.10+.
- No network calls, telemetry, model-provider credentials, automatic publishing,
  background services, or dependency installation in runtime helpers.
- Style signals are review suggestions, not forbidden grammar or AI detection.
- Structural source checks and recorded reviews do not prove factual truth.
- No invented eyewitness scenes, personal experience, citations, or test results.

## Verification

Run `python3 -m unittest discover -s tests -v` and
`python3 tools/validate_package.py` before claiming the package is verified.
Test installation in a temporary directory, never in an active agent profile.
Behavioral evaluations must identify the actual runner and limitations; do not
label a model reviewer as a human or publish a synthetic comparison as a real
competitor benchmark. Local work notes in `.local/` are not release material.
