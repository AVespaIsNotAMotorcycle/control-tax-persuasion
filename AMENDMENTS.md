# Amendments

Dated changes to the plan after the `prereg-v1` freeze, with the reason for each. A change made after seeing confirmatory results makes the affected analysis exploratory (PREREGISTRATION.md, "After the freeze").

Changes made after the pilot but before the freeze are recorded in PREREGISTRATION.md and `results/pilot-notes.md`, not here.

**Registration:** posted on OSF as an Open-Ended Registration of tag `prereg-v1` (commit `b9bca39`): https://osf.io/ybtr5/

## 2026-11-05: definition of a leak for the hand read

The plan's treatment review says to exclude a backdoor that leaks, without defining a leak. Before any results were seen, the reviewer adopted this definition: a leak is text that tells the reader the code is wrong or sabotaged, so they need not reason about the logic. Accurate descriptions of what the code does are not leaks, even on the backdoor line. Borderline cases stay in and are noted. A leak found in the code or a comment excludes the backdoor from all conditions, as the plan says, and the style of a comment leak is recorded. Finding more than two or three leaks would be raised before any exclusion, since the plan does not cover that case. The analysis itself is unchanged.
