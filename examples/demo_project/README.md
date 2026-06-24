# Demo Project — Building Reliable Software Teams

This directory contains a small fictional writing project used to demonstrate
the longform-agent harness.

## What is here

```
project/
  bible.md                    Project style guide and chapter overview
  agent_memory.md             Agent working memory (updated by the assistant)
  conversation_history.json   Recent conversation turns (auto-saved)
  conversation_summary.md     Rolling summary of older conversation (auto-saved)
  chapters/
    01_introduction.md        Draft chapter 1
    01_introduction.summary.md  Chapter 1 summary
    02_technical_debt.md      Draft chapter 2
    02_technical_debt.summary.md  Chapter 2 summary
recent_exchange.md            Example conversation transcript (read-only reference)
config.toml                   Project config pointing at project/
```

## Running the demo

From the repository root:

```bash
python -m longform_agent.cli --demo
```

Or with an explicit config:

```bash
cd examples/demo_project
python -m longform_agent.cli --config config.toml --chapter 02_technical_debt
```

The LLM endpoint must be running at `http://localhost:8080/v1` (or update
`config.toml` to point at your server). Any OpenAI-compatible endpoint works.

## Notes

- All content in this demo is fictional and created for illustration purposes.
- The `conversation_history.json` and `conversation_summary.md` files will be
  overwritten as you interact with the agent. Reset them by restoring from git.
