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
| | Anthropic | API account | Commercial terms on training with API data | To check |
| | OpenAI | API organization | Data controls, including any opt-in data sharing | To check |
| | OpenAI | ChatGPT (only if used for this project) | Model improvement setting | To check |

Re-check if an account, organization, or provider's terms change before the confirmatory run.
