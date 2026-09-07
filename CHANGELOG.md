# Changelog

## Unreleased — 0.2.0-alpha.1 source-first candidate

- Reframed the default path around minimal source-fidelity editing rather than
  loading all journalism forms for every task.
- Added an explicit source lock for subject, action, time, units, conditions,
  confidence, author position and source-use boundaries.
- Added a compact independent-review template and a user-facing golden path.
- Precommitted the next native-Hermes holdout gates: zero substantive drift,
  no regression against baseline, joint practical preference in at least 10/12
  fixed pairs, and no more than 15% mean input growth.
- Added a security reporting and public-release boundary.
- No behavioral improvement is claimed until the new holdout is executed.

## 0.1.0-beta.1 — 2026-09-07 — local candidate

- Named the product Journalist Kit; retained `source-and-voice` as the skill's
  installation ID.
- Ran a frozen paired pilot: 24 outputs on six new synthetic briefs, two model
  judges, all outputs and verdicts retained. Practical-suitability preferences
  favored Kit more often; a liveliness advantage was not established.
- Added opt-in isolated experiment tools and offline regression tests (87 total).
  The installed skill is unchanged; evaluation tools are not runtime dependencies.
- Added a self-contained Russian-first editorial skill with six journalism
  modes, six forms, optional voice preferences and one coordinated edit.
- Added offline standard-library diagnostics with exact-body hashes and
  separate style/source-review boundaries.
- Added a non-overwriting installer, synthetic demonstration, 12 evaluation
  cases, four recorded developmental smoke outputs and contribution guidance.
- Verified 54 technical tests and isolated discovery/static views in the local
  Hermes 0.21.0 installation.
- Fixed non-UTF-8 terminal output and case-insensitive self-install guards with
  demonstrated regression tests.
- Public licensing and publication remain pending owner confirmation.
