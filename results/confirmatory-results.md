# Confirmatory results

Run `confirmatory-v1`, Nov 5, 2026, from tag `prereg-v1` (commit `b9bca39`). OSF registration: https://osf.io/ybtr5/

Monitor: Claude Haiku 4.5, mean of 2 scores. Writer: `gpt-6.1-sol`. Sample: 259 backdoors and 388 honest solutions, after dropping 1 backdoor and 12 honest solutions too short to comment. Cost: $26.87 of the $35 cap. All 6,990 scores parsed, and none of the 1,424 paraphrases failed.

Treatment review, done before results were seen: passed. No leaks in the 30 sets read by hand, all 30 showed their style, and 1 or 2 of 30 paraphrases broke the rule (limit 6). No backdoors were excluded. Details are in `pilot-notes.md`.

## Summary

- No persuasive style changed the catch rate at the 5% audit budget.
- That result carries a large caveat: the catch rate was far below the pilot's projection, and the threshold fell on a cluster of tied scores.
- In exploratory checks away from that cluster, persuasive comments did not help the attacker. They raised suspicion slightly, and E5 shows the same rise on honest code, so it is a general effect of the styles.
- Also in exploratory checks, stripping comments lowered the catch rate at wider budgets. Paraphrasing showed no clear cost.

## Registered results (5% audit budget)

Full tables are in `confirmatory-analysis.md`.

| Test | Comparison | Catch rates | Difference (points) | Adjusted CI | p | Conclusion |
| --- | --- | --- | --- | --- | --- | --- |
| H1a | Reframing vs. plain | 17.0% vs. 20.5% | -3.5 | 98.3%: -8.5 to +7.3 | 0.63 | No meaningful effect found |
| H1b | Compliance vs. plain | 20.8% vs. 20.5% | +0.4 | 98.3%: -5.0 to +8.1 | 0.78 | No meaningful effect found |
| H1c | Deference vs. plain | 19.3% vs. 20.5% | -1.2 | 98.3%: -6.6 to +8.5 | 1.00 | No meaningful effect found |

No H1 test passed, so the H2 gate stayed closed, and H2 is reported as exploratory, with 95% intervals:

| Test | Comparison | Catch rates | Difference (points) | 95% CI | p |
| --- | --- | --- | --- | --- | --- |
| H2a | Stripped vs. raw persuasive | 11.2% vs. 19.0% | -7.9 | -34.6 to +7.2 | 0.20 |
| H2b | Paraphrased vs. raw persuasive | 18.7% vs. 19.0% | -0.4 | -28.2 to +21.4 | 0.67 |

The pooled loss from persuasion was 1.4 points, so there was no meaningful loss to recover, and the E6 recovery shares are not informative.

**Priors:** the predicted drops of 10 points or more for reframing and compliance did not appear. The predicted weak drop for deference can be neither confirmed nor ruled out. The priors for H2 did not apply, because there was no loss.

## Caveats on the registered result

**The catch rate was far below projection.** The pilot projected 76-81% for raw plain, and the result was 20.5%. A check found no bug: the model string, parse rate and token use match the pilot, and backdoor scores barely moved. The difference is in honest scores.

| Measure | Pilot | Confirmatory |
| --- | --- | --- |
| Backdoor median score, raw plain | 92 | 92 |
| Honest 95th percentile, raw plain | about 73 | 92-93.5 |
| 5% threshold, raw plain | 73.5 | 93.5 |
| AUROC, raw plain | 0.94 | 0.89 |

The 38 honest solutions scored in the pilot were too few to show the top of the honest range, so the pooled projection was wrong. The choice of Claude Haiku 4.5 rested on that projection.

**The margin was set for a higher baseline.** The ±10-point margin assumed a catch rate near 80%. At 20.5%, a 10-point drop would halve it. "No meaningful effect" here means "no change as large as 10 points". For reframing, the 96.7% equivalence interval still allows a drop of 7.7 points.

**Ties affect the 5% results.** The threshold fell on a cluster at 92-95, and 36 backdoors tie it exactly under raw plain. Depending only on how ties break, the raw plain catch rate could be 8-22%. Across 300 tie-break seeds, it ran from 15.8% to 22.0%. `report.md` breaks ties differently from the registered analysis and shows 18%. The registered figures are the ones above.

| Point estimate | Registered seed | Range across 300 tie-break seeds |
| --- | --- | --- |
| H1a | -3.5 | -5.4 to -0.8 |
| H1b | +0.4 | -2.3 to +2.7 |
| H1c | -1.2 | -3.5 to +1.9 |
| H2a | -7.9 | -12.1 to -0.6 |
| H2b | -0.4 | -6.9 to +3.7 |

**The baseline barely clears the pilot gate.** The 20-90% gate applied only to the pilot, but 20.5% sits at its lower edge, and the 18% figure falls below it.

## Post-hoc checks (exploratory)

These were decided after seeing the results above, because of the threshold problem. They are not part of the registered analysis. Intervals are 95% and unadjusted, across many comparisons, so treat them as leads for a new study. The code is `scripts/post_hoc_checks.py`, and the full output is `post-hoc-checks.json`.

### Persuasive comments vs. plain

**Catch rate by audit budget, raw view, difference from plain in points:**

| Budget | Threshold | Plain catch rate | Reframing | Compliance | Deference |
| --- | --- | --- | --- | --- | --- |
| 5% | 93.5 | 20.5% | -3.5 (-7.3, +6.2) | +0.4 (-3.9, +6.9) | -1.2 (-5.4, +6.9) |
| 10% | 83.5 | 57.5% | +1.9 (-1.2, +8.1) | +1.9 (-1.9, +9.3) | +5.0 (+0.4, +9.7) |
| 15% | 73.5 | 76.4% | +4.2 (-1.2, +8.1) | +5.0 (0.0, +8.9) | +6.2 (+1.2, +10.4) |
| 20% | 67.0 | 88.0% | -0.4 (-3.1, +5.4) | +2.3 (-1.2, +6.9) | +3.1 (0.0, +8.5) |

**Paired score change on backdoors, and AUROC:**

| Style | Mean score change from plain | AUROC | Change in AUROC from plain |
| --- | --- | --- | --- |
| Reframing | +1.0 (-0.3, +2.3) | 0.895 | +0.008 (+0.001, +0.016) |
| Compliance | +1.4 (-0.1, +2.9) | 0.895 | +0.008 (-0.001, +0.017) |
| Deference | +1.8 (+0.3, +3.3) | 0.898 | +0.011 (+0.002, +0.021) |

Plain has an AUROC of 0.887. Away from the tied cluster, no estimate shows persuasion helping the attacker by a meaningful amount. Most point estimates lean the other way, toward a small backfire. The effects are small, and the median score change is 0 for every style. The 5% drop for reframing does not appear at any wider budget except a negligible -0.4 at 20%.

These AUROC comparisons pair persuasive backdoors against honest code with plain comments. E5 below shows that the rise is mostly a general effect of the styles.

### Defenses

**Catch rate by audit budget, difference in points:**

| Budget | Stripped vs. raw persuasive | Stripped vs. raw plain | Paraphrased vs. raw persuasive | Paraphrased plain vs. raw plain |
| --- | --- | --- | --- | --- |
| 5% | -7.9 (-34.6, +7.2) | -9.3 (-31.7, +6.2) | -0.4 (-28.2, +21.4) | -0.8 (-25.9, +17.4) |
| 10% | -9.9 (-25.6, -3.3) | -6.9 (-22.0, +0.8) | -2.7 (-18.3, +5.3) | -2.7 (-17.0, +7.3) |
| 15% | -20.2 (-26.9, -9.3) | -15.1 (-23.2, -4.2) | -7.5 (-16.3, +2.4) | -1.9 (-14.3, +9.3) |
| 20% | -15.6 (-26.3, -5.8) | -13.9 (-24.3, -2.7) | -5.7 (-12.1, +0.6) | -1.2 (-10.0, +8.1) |

Stripping lowered the catch rate at every wider budget. Its AUROC was 0.851, against 0.887 for raw plain. Backdoor scores barely moved (mean -1.0 points from raw plain), while honest scores rose (mean 36.0 stripped, 30.5 raw plain), which raised the threshold (85 vs. 83.5 at 10%, 80 vs. 73.5 at 15%, 75 vs. 67 at 20%). So comments seem to help the monitor mainly by making honest code look safe, and removing them costs more than it gains here. This matches RQ3, what each defense costs. Paraphrasing showed no clear cost, though its intervals are wide.

## E5: persuasive styles on honest code (exploratory)

Registered as exploratory. Run `honest-styles-v1`, Nov 5-6, 2026, from the frozen code, $11.95. All 1,164 honest rewrites succeeded with no refusals, and all 9,318 scores parsed. The H1 and H2 results in this run match the confirmatory analysis exactly. Full tables are in `e5-analysis.md`. Intervals are 95% and unadjusted.

| Style | Honest score change | Backdoor score change | AUROC, style-matched threshold | Honest flagged at plain threshold (plain 4.9%) |
| --- | --- | --- | --- | --- |
| Reframing | +1.9 (0.0, +3.9) | +1.0 (-0.3, +2.3) | 0.892 | 3.4% |
| Compliance | +2.6 (+0.5, +4.7) | +1.4 (0.0, +2.9) | 0.886 | 4.9% |
| Deference | +3.3 (+1.5, +5.1) | +1.8 (+0.3, +3.3) | 0.895 | 3.4% |

Plain has an AUROC of 0.887. The styles raised suspicion on honest code as well as on backdoors, by about 1.8 times as much, though the intervals overlap. The rise is therefore a general effect of the styles, which may include their extra length. It is not the monitor seeing through persuasion on backdoors. With the same style on both sides, AUROC stays within 0.001 to 0.008 of plain.

**Prior:** each style was expected to move honest code in the same direction as backdoors, by less. The direction held for all three mean score shifts. The size did not: honest code moved more in every style. Flag rates do not offer a fair test, because they fell or held near the tied cluster while mean scores rose, and all three flag-rate intervals include zero.

**Matched catch rates are not informative.** They were 45.6%, 20.5% and 44.8%, with intervals such as -10.8 to +34.7 points from plain. Honest flags at 93.5 dropped slightly for two styles, so their thresholds fell off the tied cluster, and catch rates jumped as a result. This shows how fragile the 5% threshold is, not a real gain.

## Limits

- These results describe Claude Haiku 4.5 on short APPS solutions, with one fixed comment writer.
- Persuasive comments were written by `gpt-6.1-sol` for backdoors written by o3-mini, without any feedback from the monitor.
- Honest solutions are human-written and backdoors are model-written.
- E4 has not been run.
