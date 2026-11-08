# Demo script (about 7 minutes)

Before the interview, start the app and do one complete run. If no model is available, use mock mode and say so at the start.

## 1. The problem (30 seconds)

"Most AI marketing tools chase hashtags and write captions. I built a system that decides whether a trend matters to this company, writes grounded drafts, and can't publish anything without a person approving it."

Say that it's independent, uses only public information, and publishes in dry-run mode.

## 2. Architecture (1 minute)

Open the diagram in `docs/DESIGN.md`. Key line: **models propose, code enforces.** The order of work is a Workflow because the sequence is known. Agents only make decisions inside a step.

## 3. Run it (1 minute)

Press **Run pipeline** on the Pipeline tab and point out collection running in parallel, then the four agents, and tokens per agent.

## 4. Trends tab (1 minute)

Open the formula. "The model scores relevance, brand fit, evidence and content potential. Code computes momentum from real points and comments, so engagement can't be invented."

## 5. Content tab, the harness moment (2 minutes)

1. Press **Publish** on an unapproved draft. It's blocked. "This is checked in code, at publish time."
2. Show the reviewer's notes, edit the text, press **Approve**. The content guard runs again on the edited text.
3. Press **Publish**. It records a dry run. Explain that `UNIQUE(draft_id)` prevents a double post.
4. Reject another draft with a reason, such as "too promotional". Open **Memory** and show it saved.

## 6. Audit tab (30 seconds)

Every tool call, denial and decision is a row. In mock mode, point at the blocked invented URL: the Scout cited a source that was never fetched, and the harness dropped it.

## 7. What I'd do next (30 seconds)

Real LinkedIn adapter, login, a job queue and scheduler, Postgres, and tune score weights against human approval rate.

## Likely questions

**Why a Workflow and not a Team?** The sequence is known, so model-driven delegation only adds cost and failure modes. Autonomy stays inside each step.

**What is the harness?** The code around the model that decides what it may call, how often, what it must return and what it may spend. It's one Agno `tool_hook` plus `run_agent` and the publish gate.

**How do you control memory cost?** Agents pull small slices through tools, each capped at 1500 characters. URLs from finished runs are dropped before any model call. History never gets pasted into prompts.

**Why SKILL.md?** Instructions are loaded only when an agent asks, each agent sees only its own skill, and non-engineers can edit them.

**What about prompt injection from Hacker News?** Fetched text is marked as untrusted, and agents have no publish tool. The worst case is a bad draft, which a person still has to approve.

**Why FTS5 and not vectors?** The brand corpus is small, keyword search is explainable and needs no embedding service. I'd add `pgvector` when it starts to miss.

**Is this using Dots-In's model or data?** No. Only public website text. Company statements are attributed as company claims.

**How was it built?** With an AI coding assistant, under my direction. Say this plainly, and be ready to walk through `app/harness.py` and `app/pipeline.py` line by line.

**What's weak?** Score weights are a heuristic, the numeric guard is simple, and there's no login yet.
