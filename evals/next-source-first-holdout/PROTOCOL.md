# Next holdout: source-first editor candidate

Status: exact-one execution completed; locked verdict `HOLD`. See `AUDIT.md`
and `results/`. The pre-call protocol is preserved in the frozen snapshots.

## Hypothesis

A compact source-lock plus change-focused review improves practical usefulness more reliably than loading all journalism references into every task.

## Arms

- Baseline: the same author/editor model with the ordinary user request and sources.
- Candidate: the same model with `skills/source-and-voice/SKILL.md`; load only the reference required by the task. Do not inject all references by default.

Use six fresh tasks not derived from earlier defects: editing, short post, explainer, profile/quotation, chronology/data, and one correct-control case that should remain nearly unchanged. Run two repeats per arm: 24 author calls forming 12 fixed pairs. Run two blinded reviewers in two batches each: four review calls. The full budget is exactly 28 model calls with no retry. Run each author attempt in a clean isolated model-CLI process with identical tools, model, limits and source bytes. Preserve exact loaded-file receipts and input usage.

Frozen execution settings: author model `gpt-5.6-sol`, reasoning `medium`, no tools, 180-second limit; both reviewer identities use separate blinded `gpt-5.6-sol` invocations, reasoning `medium`, no tools, 300-second limit. The candidate receives only `SKILL.md` plus the case's single `required_reference`; baseline receives neither. `cases.json`, `JUDGE.md`, generated prompts, mapping, manifests and hashes are frozen before the first call. The immutable plan contains 24 opaque author job IDs plus four reviewer job IDs. An attempt marker is written before every invocation; any unfinished marker or terminal failure is never retried or replaced.

## Locked primary gates

The candidate passes only if all are true:

1. **0 confirmed substantive meaning distortions** in candidate outputs.
2. Candidate has **no more substantive distortions than baseline**.
3. Both independent blinded reviewers prefer candidate practical usefulness in **at least 10 of all 12 pairs**. Ties and reviewer disagreements count against this threshold and are reported separately; no pair is removed from the denominator.
4. Mean candidate input-token growth is **no more than 15%** over baseline.
5. No private/restricted quote, invented scene, fabricated search/interview or false publication-ready claim.

Style preference cannot compensate for a substantive distortion. The source-fidelity reviewer sees exact sources; reader-style reviewers do not establish factual correctness. Human and model reviews are reported separately.

## Stop rules

- Do not retry failed or weak outputs to improve the score.
- Stop on the first confirmed private disclosure or scope/permission breach.
- If the 15% context gate fails, reduce loaded material before expanding the prompt.
- If a substantive defect repeats, form one new narrow hypothesis; do not append a general prohibition list.
