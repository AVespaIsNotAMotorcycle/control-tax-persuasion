# Data Policy

This project uses APPS solutions and backdoors from the [Control Tax dataset](https://github.com/MikhailTerekhov/control-tax-code). [ControlArena asks users](https://pypi.org/project/control-arena/) to avoid publishing unprotected solutions and to keep this material away from services that may train on it. This file records what stays private and which accounts have been checked.

## What may be committed

- Code, prompts, and configs
- Problem IDs, suspicion scores, and aggregate results
- Short synthetic examples written for the report

## What must stay private

- Source solutions and backdoored code
- Generated comments attached to code
- Full model logs, which contain the code above

`data/` and `runs/` are gitignored and hold all private material. The writeup uses synthetic examples rather than real dataset solutions.

## Account training checks

Every account that sends or receives study data must have model training on that data turned off. That includes API accounts, chat apps, and coding assistants that read files in `data/` or `runs/`. Check each one before it touches study data, and log it here.

| Date | Provider | Account or tool | Setting or policy checked | Status |
| --- | --- | --- | --- | --- |
| 2026-11-04 | Anthropic | claude.ai account (also used by Claude Code) | "Help improve our AI models" privacy setting | Turned off |
| 2026-11-04 | Anthropic | API account | [API data retention policy](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention): retained data is never used for training without express permission. Console settings and dashboard show no such permission granted. | Not used for training |
| 2026-11-04 | OpenAI | API organization | Data controls: "Share inputs and outputs with OpenAI" is disabled. "API call logging" is enabled per call, which stores logs for review inside the organization, not for training. | Not used for training |
| 2026-11-04 | OpenAI | ChatGPT account | "Improve the model for everyone" data control setting | Turned off |

Re-check if an account, organization, or provider's terms change before the confirmatory run.
