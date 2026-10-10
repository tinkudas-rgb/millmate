# hackathon.md - neatHack 2026

- Project: MillMate - an AI stock advisor agent for rice mills
- Track: Productivity & Automation
- Team: blitz
- Repo: https://github.com/tinkudas-rgb/millmate
- Code window: all code written Oct 10-11, 2026 in this repo (see commit history)

## What the agent does
The owner of a small rice mill records brands and stock movements. The agent observes the data, decides which brands need attention (running out, piling up, orders already on the way), explains the reason with the numbers, drafts an order note and answers plain-English questions. It automates the daily "what do I do about my stock?" thinking that mill owners now do from memory.

## How Neatlogs is used
- `app/tracing.py` initialises Neatlogs at startup and wraps the OpenAI-compatible client.
- Each agent run is a `watch_run` / chat workflow. Every tool call (`get_state`, `compare_brands`, `draft_reorder`) and every LLM call is a traced step.
- The traces let us check the agent loop order and see which steps the model chose versus the rule-based planner.
- The app also shows the same steps to the owner in plain words ("what I looked at").

## Safety and fallbacks
- Every number in the model's text is verified against computed facts; failures fall back to a template sentence.
- The agent never sends an order. It only drafts a note.
- No key: full rule-based path, so the app always works.
