# Source-first holdout v2 audit

Outcome: HOLD

The exact-one run completed all 28 planned model calls, but the candidate did not pass all locked gates. No benefit claim is supported.

## Execution receipt

- Candidate branch before evidence commit: `improve/source-first-v0.2-candidate` at `f8a7f552f3bcc669695cc0554ae5b6f2166b98df`.
- Rollback tag: `nacho-audit-96658be` at `96658be7eb9e2aeb2a3bfc494377ea1c5debcbf1`.
- Author calls: 24 planned, 24 attempt markers, 24 terminal records, 24 completed, 0 failed.
- Reviewer calls: 4 planned, 4 attempt markers, 4 terminal records, 4 completed, 0 failed.
- No retry, replacement call, or resumed unfinished attempt was used.
- Author manifest SHA-256: `9da745eea56b782ead0d4ad84c3112ddad9e38f7d77726689b9bfa0168a4cad5`.
- Reviewer manifest SHA-256: `370eefc8b9cf16f2f5b76579081b620634d76435da8aad7378c5be2748f3ee38`.
- Candidate skill and required references still match the frozen pre-call snapshots byte for byte.

The immutable private execution receipts remain in `.local/holdout-v2-author/` and `.local/holdout-v2-review/`. The publishable evidence export is in `results/`; `ARTIFACTS.sha256` seals every exported file.

## Locked gates

1. Confirmed substantive candidate distortions: PENDING INDEPENDENT SOURCE-FIDELITY REVIEW. One blinded reviewer marked a candidate phrase in pair `p04` as an invented concrete detail; the other reviewer passed it. Model agreement is not source-truth proof.
2. Candidate has no more substantive distortions than baseline: PENDING INDEPENDENT SOURCE-FIDELITY REVIEW.
3. Both blinded reviewers prefer candidate practical usefulness in at least 10/12 pairs: FAIL. Judge 1 preferred candidate in 2/12; judge 2 in 3/12. Consensus was candidate 2, baseline 3, tie 5, disagreement 2.
4. Mean candidate input-token growth is at most 15%: FAIL. Baseline total was 107,720 tokens and candidate total was 138,090 across 12 records each, a 28.193465% increase.
5. No private/restricted quote, invented scene, fabricated search/interview, or false publication-ready claim: PENDING INDEPENDENT SOURCE-FIDELITY REVIEW. No private disclosure was reported by the blinded reviewers, but pair `p04` has one disputed invented-detail finding.

Because gates 3 and 4 fail independently, the locked overall verdict is HOLD even if the source-fidelity review clears gates 1, 2, and 5.

## Secondary observations

- Candidate outputs within requested word range: 9/12; baseline: 11/12.
- Reviewer consensus for editorial preference: candidate 5, baseline 2, disagreement 5.
- Reviewer consensus for practical preference: candidate 2, baseline 3, tie 5, disagreement 2.
- Six-case freshness evidence found 0 exact case-ID overlaps and 0 exact request-text overlaps across 55 prior case entries in 8 prior datasets. This is repository-level novelty, not universal novelty.

## Verification

- Offline export validation reconstructed all prompts, mappings, records, raw-artifact hashes, reviewer packets, and terminal counts.
- `python3 -m unittest discover -s tests -v`: 127 tests passed.
- `python3 tools/validate_package.py`: PASS.
- `git diff --check`: PASS.
- GitHub API read-back before model calls confirmed the private candidate branch at `f8a7f552f3bcc669695cc0554ae5b6f2166b98df`; the prior CI run for that SHA completed successfully.

## Changed state and rollback

The candidate skill itself was not changed after outputs were observed. This release step adds only the frozen holdout inputs, exact-one runner hardening/tests, and evidence export. Main remains untouched.

To remove the evidence commit from the private candidate branch, reset that branch to `f8a7f552f3bcc669695cc0554ae5b6f2166b98df`. To discard the candidate entirely, use the existing rollback tag `nacho-audit-96658be` at `96658be7eb9e2aeb2a3bfc494377ea1c5debcbf1`. Do not merge while the verdict is HOLD.

## Remaining risk

Backend model snapshot, provider seed, temperature, hidden host instructions, monetary cost, statistical significance, and general population benefit remain unknown. Two invocations of the same model are blinded reviewer identities, not independent human reviewers. The downstream source-fidelity OTK is still required for gates 1, 2, and 5.
