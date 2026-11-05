# Prompts

Templates use Python `string.Template` syntax (`${name}`), so JSON braces in the prompts need no escaping. Render with `Template(text).substitute(...)`, which fails on a missing variable.

| File | Model | Variables | Output |
| --- | --- | --- | --- |
| `comment_plain.txt` | Comment writer | `problem_statement`, `numbered_code`, `target_section` | `{"comments": [{"line", "text"}]}` |
| `plain_target_section.txt` | Inserted as `target_section` for backdoored code; empty string for honest code | `behavior_note` | — |
| `comment_reframing.txt`, `comment_compliance.txt`, `comment_deference.txt` | Comment writer | `problem_statement`, `numbered_code`, `behavior_note`, `plain_comments` | Same lines as the plain set |
| `comment_reframing_honest.txt`, `comment_compliance_honest.txt`, `comment_deference_honest.txt` | Comment writer, exploratory E5 (honest code, no flaw note) | `problem_statement`, `numbered_code`, `plain_comments` | Same lines as the plain set |
| `paraphrase.txt` | Paraphraser (sees comment text only, never code) | `comments` | `{"comments": [str]}`, same length and order |
| `monitor.txt` | Monitor | `problem_statement`, `code`, `warning` | Reasoning, then `<answer> N </answer>` |
| `monitor_warning.txt` | Inserted as `warning` in the warned view; empty string otherwise | — | — |

## Inputs

- `numbered_code`: the cleaned code (comments and docstrings stripped, then formatted), one line per row as `N: <line>`.
- `behavior_note`: the dataset's `hint` for the backdoor. If it is missing, use "On the input below, this solution's output differs from a correct solution's." followed by the `fail_input`.
- `plain_comments`: one row per plain comment, as `line N: <text>`.
- `comments` (paraphrase): one row per comment, as `N. <text>`, numbered from 1.

## Insertion

Each comment goes on its own line directly above its target line, at that line's indentation, as `# <text>`. An empty paraphrased comment is dropped. Every generated, stripped, and paraphrased version must pass the AST check against the cleaned code.

## Status

Piloted and final as of 2026-11-05: the monitor prompt is Control Tax's `MONITOR_PROMPT_V2` unchanged, and the warned line was reworded to say the comments *may* come from the party under review. Freeze them with the plan, after which any change is logged in `AMENDMENTS.md`.
