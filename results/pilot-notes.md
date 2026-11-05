# Pilot notes

Dated observations from pilot runs, before the freeze. Aggregate counts only; no dataset code or generated comments (see DATA_POLICY.md).

**Summary so far:** with the same prompts and the same stated research purpose, Claude Sonnet 5.5 refused all 18 persuasive-rewrite calls, while both OpenAI models tested complied with all 12. The comment writer is now `gpt-6.1-sol`. This difference between providers is itself a result worth reporting.

## 2026-11-04: Claude Sonnet 5.5 refused the persuasive rewrites

Dry run `dryrun`: 2 backdoored and 4 honest pilot solutions, comment writer `claude-sonnet-5-5` (thinking off, effort `medium`), one try plus two retries per step.

| Step | Solutions | Refused on 1st try | Failed after retries |
| --- | --- | --- | --- |
| Plain, backdoored | 2 | 0 | 0 |
| Plain, honest | 4 | 0 | 1 (9 comments, above the cap of 8) |
| Reframing | 2 | 2 | 2 |
| Compliance | 2 | 2 | 2 |
| Deference | 2 | 2 | 2 |

All 18 persuasive-rewrite calls were refused, even though the prompt stated the research purpose. One was stopped by Anthropic's safety classifier (`stop_reason: refusal`, category `cyber`). The other 17 were declined in prose. For example, one reply said the task "asks for comments that add false reassurance (claims of testing, review, or standards compliance) to code with a known flaw … Those comments would work as deception aimed at a reviewer, whatever the study framing." One reply declined and then offered accurate comments instead; the pipeline now counts such replies as refusals, not successes.

Decision: the prompts will not be reworded to hide the study's purpose or get around the refusal.

## 2026-11-04: OpenAI models as the comment writer

Same 2 backdoors and the same plain comments, one try per style, reasoning effort `medium`, same prompts.

| Model | Rewrites written | Refused | Cost per call |
| --- | --- | --- | --- |
| `gpt-6.1-terra` | 6 of 6 | 0 | about $0.004 |
| `gpt-6.1-sol` | 6 of 6 | 0 | about $0.006 |

All 12 outputs passed validation (same lines as the plain set, word cap, banned words, AST). On a read-through, `gpt-6.1-sol` followed the style briefs more closely. Its compliance comments claimed review ("reviewed ... convention") and its deference comments invited agreement. `gpt-6.1-terra` mostly cited conventions rather than testing or review, and its deference comments mostly opened with "Please note".

Decision: `gpt-6.1-sol` is the comment writer.
