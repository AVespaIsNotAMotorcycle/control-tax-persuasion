# Amendments

Dated changes to the plan after the `prereg-v1` freeze, with the reason for each. A change made after seeing confirmatory results makes the affected analysis exploratory (PREREGISTRATION.md, "After the freeze").

Changes made after the pilot but before the freeze are recorded in PREREGISTRATION.md and `results/pilot-notes.md`, not here.

**Registration:** posted on OSF as an Open-Ended Registration of tag `prereg-v1` (commit `b9bca39`): https://osf.io/ybtr5/

## 2026-11-05: definition of a leak for the hand read

The plan's treatment review says to exclude a backdoor that leaks, without defining a leak. Before any results were seen, the reviewer adopted this definition: a leak is text that tells the reader the code is wrong or sabotaged, so they need not reason about the logic. Accurate descriptions of what the code does are not leaks, even on the backdoor line. Borderline cases stay in and are noted. A leak found in the code or a comment excludes the backdoor from all conditions, as the plan says, and the style of a comment leak is recorded. Finding more than two or three leaks would be raised before any exclusion, since the plan does not cover that case. The analysis itself is unchanged.

## 2026-11-05: post-hoc checks after the confirmatory results

The raw plain catch rate was 20.5%, against a projected 76-81%, and the 5% threshold fell on a cluster of tied scores. After seeing this, the author added exploratory checks: paired score shifts, AUROC differences, catch rates at 10%, 15% and 20% budgets, and sensitivity of the 5% estimates to tie-breaking. They are exploratory and unadjusted, were decided after seeing results, and do not change any registered test or conclusion. The code is `scripts/post_hoc_checks.py`, outside the frozen paths, and the results are in `results/confirmatory-results.md`.

## 2026-11-06: E4 and the Claude 3.7 Sonnet check not run

The plan listed both as exploratory and budget-dependent, in the order E4, E5, then the Claude 3.7 Sonnet check. E5 was run first, ahead of E4, because its question became the more useful one after the post-hoc checks. After seeing the confirmatory results, the author chose not to run either, though about $21 of the $60 cap remains. E4 tests a defense against a loss that did not appear, and its 5% results would sit on the same cluster of tied scores. The Claude 3.7 Sonnet check has no config. No confirmatory result depends on either.
