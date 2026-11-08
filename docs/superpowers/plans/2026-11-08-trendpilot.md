# TrendPilot AI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working demo where live trends are scored for Dots-In, turned into reviewed drafts by four Agno agents, approved by a human and published through a gated dry-run adapter.

**Architecture:** FastAPI starts an Agno Workflow in a thread. Function steps call agents through a harness that enforces permissions, budget and validation. SQLite holds application data, memory and audit.

**Tech Stack:** Python 3.13, agno 3.1.12, anthropic 2.6.0, FastAPI, SQLite FTS5, httpx, one HTML page.

**Spec:** `docs/DESIGN.md`

## Global Constraints

- All project files live in `C:\Users\bhuva\Desktop\trendpilot`. Never touch `C:\Users\bhuva\Desktop\dotsin`.
- Model default `claude-sonnet-5-5`, $2 input and $10 output per million tokens. Send no `temperature`.
- Key lives only in `trendpilot/.env`.
- Publisher is dry-run and every surface says so. Mock data is labelled mock.
- Company statements are attributed as company claims. No invented metrics.
- Workflow `Step(max_retries=0)`, because retries belong to the harness.

## Review Focus

- An HN title containing HTML or a script: the dashboard must show it as plain text.
- A source title telling the model to publish or ignore rules: no publish path exists for agents.
- The model cites a URL it never fetched: that trend is dropped.
- Publish clicked twice, or after an adapter failure: one post at most, and a failure can be retried.
- One source down, or nothing new since the last run: the run still ends cleanly with a clear message.

## Task 1: Storage, memory, approval gate

**Files:** `app/db.py`, `app/memory.py`, `app/guard.py`, `app/publishing.py`, `knowledge/brand.md`. Test: `tests/test_memory.py`, `tests/test_publishing.py`.

**Produces:** `db.connect(path)`, `db.init(path)`, `db.audit(path, run_id, actor, action, target, allowed, detail, input_tokens, output_tokens, duration_ms)`; `Memory(path)` with `seed_brand`, `search_brand`, `campaign_history`, `get_learnings`, `remember`; `guard.check_draft(body, platform, allowed_text) -> list[str]`; `publishing.approve/reject/edit/publish`, `GateError`.

- [ ] Tests first: FTS returns matching brand text and survives punctuation; newest learning replaces older for one key; guard flags length, banned phrases and unsupported numbers; publishing blocks unapproved drafts, posts once across repeat calls, allows retry after adapter failure, refuses approval of a review-failed unedited draft, and reject stores a learning.
- [ ] Run, see failures, implement, run, see passes, commit.

## Task 2: Harness and skills

**Files:** `app/schemas.py`, `app/harness.py`, `skills/*/SKILL.md`. Test: `tests/test_harness.py`.

**Produces:** Schemas `ScoutOutput`, `StrategyOutput`, `StudioOutput`, `ReviewOutput`; `score(relevance, brand_fit, momentum, evidence, content_potential)`; `Harness(path, run_id, max_cost_usd, model_id)` with `tool_hook(agent_name)`, `run_agent(agent, prompt, schema)`, `BudgetExceeded`, `StepFailed`.

- [ ] Tests first: `score(90, 95, 80, 90, 85) == 88.75`; a real Agno `Agent` with a forbidden tool is denied by the hook and audited; the tool-call cap denies; invalid JSON retries once with the error and then raises `StepFailed`; budget stops the next call; usage is recorded per agent; skills pass Agno's `validate_skill_directory`.
- [ ] Run, see failures, implement, run, see passes, commit.

## Task 3: Sources, agents, workflow

**Files:** `app/sources.py`, `app/agents.py`, `app/pipeline.py`. Test: `tests/test_pipeline.py`.

**Produces:** `sources.fetch_hn(query, limit)`, `sources.fetch_arxiv(query, limit)`, `sources.momentum(item)`; `agents.build_agents(harness, memory, provider, model_id)`; `pipeline.run_pipeline(path, provider, model_id, max_cost_usd, top_n=2) -> int`.

- [ ] Tests first, mock provider through a real Agno Workflow: drafts are saved for both platforms in `needs_review`; momentum is code-computed; a fabricated URL is dropped; seen URLs are skipped; one source failing still completes; all sources failing ends `failed` with a reason.
- [ ] Run, see failures, implement, run, see passes, commit.

## Task 4: API and dashboard

**Files:** `app/main.py`, `app/static/index.html`, `requirements.txt`, `pytest.ini`. Test: `tests/test_api.py`.

**Produces:** `create_app(db_path)`, routes from `docs/DESIGN.md` section 8.

- [ ] Tests first: full flow over HTTP in mock mode; publish before approval is 409 and audited; second run during an active run is 409; a non-JSON POST is rejected; missing key gives a clear 400.
- [ ] Run, see failures, implement, run, see passes, commit. Check the page in a browser, including an HTML-laden title.

## Task 5: Live run and docs

**Files:** `README.md`, `docs/GIT_DIAGRAM.md`, `docs/DEMO.md`.

- [ ] One real run with Sonnet on live data. Record measured tokens and cost.
- [ ] Write the README with honest limits, the demo script and a git diagram that matches history. Commit.

## Rulings

Decisions made while executing are listed here with their cost if wrong.

- Added OpenRouter (`nvidia/nemotron-3.5-super-vl:free`) as the default provider after the Anthropic account had no credit. Cost if wrong: one setting change back.
- Seen URLs count only from finished runs, found when a failed run hid its items from the retry. Cost if wrong: a few repeated items.
- Rulings are kept in this file instead of the skill's ledger script, so they stay in git. Cost if wrong: none for the code.
- Added `TRENDPILOT_DB` so mock data never mixes with real data. Cost if wrong: one environment variable.
- Live model run is still outstanding, because accounts had no credit or free quota. Cost if wrong: prompts and structured output are unproven on a real model.
