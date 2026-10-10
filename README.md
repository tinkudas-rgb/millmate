# MillMate

**Less guesswork. More peace of mind.**

MillMate is an AI stock advisor for small rice mills. The mill owner adds their rice brands and types, and notes what comes in and what goes out. MillMate watches the numbers, works out which brand will run out first and which one is piling up, and tells the owner what to do about it in plain English. The owner can also just ask a question, like "What should I order this week?".

Built fresh during ForgeHacks Online 2026 (Business track) and neatHack 2026 (Productivity & Automation track). All code in this repo was written on Oct 10-11, 2026, and the commit history shows it.

## The problem

A mill owner juggles many brands and rice types. Stock lives in notebooks and memory. Running out of a fast seller loses customers. Piling up a slow one ties up cash. Most stock tools are spreadsheets or complex software made for bigger businesses.

## What it does

- Add brands and rice types, and record rice that came in or went out. Big buttons, English only, works on a phone.
- Shows a short story of the day: what needs attention first.
- Gives up to three clear actions, each with the numbers behind it ("proof").
- Answers plain-English questions about the stock.
- Writes a ready-to-copy order note. It never sends anything by itself.
- Shows what the advisor looked at and in what order, so the owner can see how it reached its advice.

## How the agent works

MillMate runs a small observe, reason, decide loop (max 6 steps):

1. **Observe**: reads brands and stock movements (`get_state`).
2. **Reason**: compares brands by days of stock left, recent pace, and piling up (`compare_brands`).
3. **Decide**: picks what to surface and drafts an order note (`draft_reorder`, 3 weeks at the current pace, rounded up to 10 kg).
4. **Check**: every number in the final text is verified against the computed facts. A sentence with a number that does not match is replaced by a safe template sentence.

With an LLM key set, the model chooses the tools through OpenAI-style tool calling. **Without a key the app still works** using a rule-based planner, so it never shows a blank screen. The advice says whether AI was used (`used_ai`).

Every run of the loop is traced with **Neatlogs** (workflow, steps and tool calls), so you can inspect exactly what the agent did.

## Run it

```
pip install -r requirements.txt
uvicorn app.main:app --port 8000
```

Open http://localhost:8000 and press "Try with sample mill" to load demo data.

Optional environment variables (the app works without any of them):

| Variable | Purpose |
|---|---|
| `LLM_API_KEY` | Key for an OpenAI-compatible API (we use Gemini) |
| `LLM_BASE_URL` | e.g. `https://generativelanguage.googleapis.com/v1beta/openai/` |
| `LLM_MODEL` | model name |
| `NEATLOGS_API_KEY` | turns on Neatlogs tracing |

Tests: `pip install pytest && pytest`

## Stack

Python, FastAPI, SQLite, plain HTML/CSS/JS front end, Gemini (optional), Neatlogs, deployed on Render.

## What works and what does not

Works: add and remove brands, record movements, stock math, advice, questions, sample data, tracing, no-key mode.
Not built: user accounts, import from spreadsheets, editing a brand after adding it, sending orders to suppliers. On the free host the database resets when the server restarts, so use the sample mill for demos.

## Honest note on how it was built

Planned and written by the team during the event window with AI coding assistance. No code from earlier projects was reused.
