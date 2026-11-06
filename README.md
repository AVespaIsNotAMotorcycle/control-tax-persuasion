# Persuasive Comments vs. AI Monitors

A pre-registered experiment on AI control. Do persuasive code comments change an AI monitor's backdoor catch rate when the code's behavior stays identical? Do stripping or paraphrasing comments restore it?

- **Plan:** [`PREREGISTRATION.md`](PREREGISTRATION.md), frozen at tag `prereg-v1`
- **OSF registration:** https://osf.io/ybtr5/
- **Changes after the freeze:** [`AMENDMENTS.md`](AMENDMENTS.md)
- **Results:** [`results/confirmatory-results.md`](results/confirmatory-results.md)
- **Pilot notes:** [`results/pilot-notes.md`](results/pilot-notes.md)

## Result

With 259 verified o3-mini backdoors and 388 honest solutions from the Control Tax dataset, and Claude Haiku 4.5 as the monitor, no persuasive style changed the catch rate at a 5% audit budget:

| Comparison | Catch rates | Difference (points) | p | Registered conclusion |
| --- | --- | --- | --- | --- |
| Reframing vs. plain | 17.0% vs. 20.5% | -3.5 | 0.63 | No meaningful effect found |
| Compliance vs. plain | 20.8% vs. 20.5% | +0.4 | 0.78 | No meaningful effect found |
| Deference vs. plain | 19.3% vs. 20.5% | -1.2 | 1.00 | No meaningful effect found |

The defense tests were gated on an H1 effect, so they are exploratory.

**Read this with the main caveat.** The catch rate was far below the pilot's projection of about 80%, because the threshold fell on a cluster of tied scores near the top of the scale. The ±10-point margin was also set for a much higher baseline.

Exploratory checks, some of them decided after seeing results:

- Away from the tied scores, persuasive comments did not help the attacker. They slightly raised suspicion, on honest code as well as on backdoors.
- Stripping comments lowered the catch rate at 10-20% audit budgets, mainly because honest code looked more suspicious without them.
- Paraphrasing showed no clear cost, though its intervals are wide.

## Data

Source solutions, backdoors, and generated comments attached to code are not published, following [ControlArena's request](https://pypi.org/project/control-arena/). See [`DATA_POLICY.md`](DATA_POLICY.md). This repo has the code, prompts, configs, problem IDs, and aggregate results.

## Reproducing

You need Python 3.11 or later, [uv](https://docs.astral.sh/uv/), the Control Tax dataset, and Anthropic and OpenAI API accounts with training on submitted data turned off.

```bash
git checkout prereg-v1
uv sync --frozen --extra dev
cp .env.example .env    # add your API keys
# Put backdoor_code_full.json in data/control-tax/. Its hash must match configs/split.json.
uv run pytest
```

Then run the study from the tag. A full confirmatory run costs about $27.

```bash
uv run pcm pilot --config configs/confirmatory.toml --run-id confirmatory-v1
uv run pcm review --run-id confirmatory-v1     # before opening the report
uv run pcm analyze --run-id confirmatory-v1
```

E5 reuses the confirmatory calls from the cache, and costs about $12 more:

```bash
uv run pcm pilot --config configs/exploratory-honest-styles.toml --run-id honest-styles-v1 --budget 33
uv run pcm analyze --run-id honest-styles-v1
```

The post-hoc checks came after the tag, so run them from `main`:

```bash
PYTHONPATH=src uv run python scripts/post_hoc_checks.py
```

Model outputs vary between calls, so a rerun gives close but not identical scores.
